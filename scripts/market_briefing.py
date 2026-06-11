#!/usr/bin/env python3
"""
Daily Market Briefing Generator
Runs at 4pm PST (Mon-Fri) via GitHub Actions.
Generates a stock market + business news briefing using Claude + web search,
then emails it via Gmail SMTP.
"""

import os
import smtplib
import json
import re
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

RECIPIENT_EMAIL = "benzjoseph1@gmail.com"
SENDER_EMAIL = os.environ["GMAIL_USERNAME"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Path to persist yesterday's predictions for grading
PREDICTIONS_FILE = os.path.join(os.path.dirname(__file__), "last_predictions.json")


def load_last_predictions() -> dict:
    if os.path.exists(PREDICTIONS_FILE):
        with open(PREDICTIONS_FILE) as f:
            return json.load(f)
    return {}


def save_predictions(predictions: dict):
    with open(PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2)


def generate_briefing(today_str: str, last_preds: dict) -> tuple[str, str, dict]:
    """
    Uses Claude with web_search to generate the full briefing.
    Returns (html_body, plain_text_body, new_predictions_dict).
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prior_pred_context = ""
    if last_preds:
        prior_pred_context = f"""
PREVIOUS DAY'S PREDICTIONS (from {last_preds.get('date', 'prior day')}):
- S&P 500 Direction Prediction: {last_preds.get('sp500_direction', 'N/A')}
- Stock Picks: {', '.join(last_preds.get('stock_picks', []))}

Compare these predictions against what actually happened today and grade them (accurate / partially accurate / inaccurate) with brief notes.
"""
    else:
        prior_pred_context = "This is the FIRST edition — no prior predictions to review. Note that briefly."

    system_prompt = """You are a professional financial analyst producing a daily stock market briefing email.
Use the web_search tool to gather fresh, real-time data for today's market close.
Be specific with numbers (index levels, % moves, stock prices). Be concise but data-rich.
Format your final output as a single JSON object with these keys:
- "html": complete HTML email body (styled, scannable, use tables/colored cards)
- "plain": plain text version
- "predictions": {
    "sp500_direction": "UP ~X% / DOWN ~X% / FLAT",
    "sp500_rationale": "1-2 sentence reason",
    "stock_picks": ["TICK1", "TICK2", "TICK3", "TICK4", "TICK5"],
    "stock_pick_rationales": {"TICK1": "reason", ...}
  }
"""

    user_prompt = f"""Generate the Daily Business + Stock Market Briefing for {today_str}.

{prior_pred_context}

Search for and include ALL of the following:

1. MAJOR INDICES at close: S&P 500, Nasdaq, Dow Jones, Russell 2000 (levels + % change)
2. KEY MARKET NARRATIVE: What drove today's moves? (2-3 sentences)
3. SECTOR PERFORMANCE: Which sectors led/lagged?
4. TOP 5 BUSINESS NEWS STORIES that moved the market (each: headline + 1-2 sentence summary)
5. NOTABLE STOCK MOVERS: Top gainers, top losers, notable large-cap stories
6. MID/SMALL CAP MOVERS: Any mid/small cap stocks showing unusual breakout activity
7. EARNINGS / IPO / M&A highlights from today
8. 5 STOCKS WORTH WATCHING (with 1-sentence thesis each)
9. PREVIOUS PREDICTION REVIEW: Grade the prior predictions (see above)
10. NEXT-DAY PREDICTIONS:
    - S&P 500 direction for the next trading day (with rationale)
    - 5 stock picks for near-term gains (can be longs or shorts/puts) with brief rationale each

Format the HTML with a clean, professional dark-header style. Use color-coded index cards (green/red).
Make it easy to scan in an email client.
"""

    messages = [{"role": "user", "content": user_prompt}]

    # web_search_20250305 is a server-side built-in tool — the API executes searches
    # automatically and returns a complete response in a single call (no manual loop).
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8192,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=messages,
    )

    # Extract the final text response (server-side tool; stop_reason will be end_turn)
    final_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_text += block.text

    # Parse the JSON from the response
    json_match = re.search(r"\{[\s\S]*\}", final_text)
    if json_match:
        data = json.loads(json_match.group())
    else:
        # Fallback: treat entire response as HTML
        data = {
            "html": f"<pre>{final_text}</pre>",
            "plain": final_text,
            "predictions": {
                "sp500_direction": "See email",
                "stock_picks": [],
                "stock_pick_rationales": {},
            },
        }

    return data["html"], data["plain"], data.get("predictions", {})


def send_email(subject: str, html_body: str, plain_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
    print(f"Email sent to {RECIPIENT_EMAIL}")


def main():
    today = date.today()
    today_str = today.strftime("%B %-d, %Y")  # e.g. "June 8, 2026"
    subject = f"Daily Business + Stock News [{today_str}]"

    print(f"Generating briefing for {today_str}...")
    last_preds = load_last_predictions()

    html_body, plain_body, new_preds = generate_briefing(today_str, last_preds)

    send_email(subject, html_body, plain_body)

    # Persist today's predictions for tomorrow's grading
    new_preds["date"] = today_str
    save_predictions(new_preds)
    print("Predictions saved for tomorrow's review.")


if __name__ == "__main__":
    main()
