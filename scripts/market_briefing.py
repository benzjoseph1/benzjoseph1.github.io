#!/usr/bin/env python3
"""
Daily Market Briefing Generator
Runs at 4pm PDT (Mon-Fri) via GitHub Actions.
Generates a stock market + business news briefing using Claude + web search,
then emails it via Gmail SMTP.
"""

import os
import smtplib
import json
import re
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

RECIPIENT_EMAIL = "benzjoseph1@gmail.com"
SENDER_EMAIL = os.environ["GMAIL_USERNAME"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

PREDICTIONS_FILE = os.path.join(os.path.dirname(__file__), "last_predictions.json")


def load_last_predictions() -> dict:
    if os.path.exists(PREDICTIONS_FILE):
        with open(PREDICTIONS_FILE) as f:
            return json.load(f)
    return {}


def save_predictions(predictions: dict):
    with open(PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2)


def extract_between(text: str, start_tag: str, end_tag: str) -> str:
    match = re.search(re.escape(start_tag) + r"([\s\S]*?)" + re.escape(end_tag), text)
    return match.group(1).strip() if match else ""


def generate_briefing(today_str: str, last_preds: dict) -> tuple[str, str, dict]:
    """
    Uses Claude with web_search_20250305 (server-side) to generate the full briefing.
    Returns (html_body, plain_text_body, new_predictions_dict).
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if last_preds:
        prior_pred_context = f"""
PREVIOUS DAY'S PREDICTIONS (from {last_preds.get('date', 'prior day')}):
- S&P 500 Direction Prediction: {last_preds.get('sp500_direction', 'N/A')}
- Stock Picks: {', '.join(last_preds.get('stock_picks', []))}

At the top of your briefing, review and grade these predictions (accurate / partially accurate / inaccurate) with a brief note on what actually happened.
"""
    else:
        prior_pred_context = "This is the FIRST edition — no prior predictions to review. Note that briefly at the top."

    system_prompt = """You are a professional financial analyst producing a daily stock market briefing email.
Use the web_search tool to gather real-time data for today's market close before writing the briefing.
Search for: today's index close prices, top business news, biggest stock movers, earnings/IPO/M&A news.

Structure your ENTIRE response using these exact delimiters — nothing outside them:

<HTML_START>
[Full styled HTML email body here — dark header, color-coded index cards, scannable sections]
</HTML_END>

<PLAIN_START>
[Full plain text version here]
</PLAIN_END>

<PREDICTIONS_JSON>
{
  "sp500_direction": "UP ~X% / DOWN ~X% / FLAT",
  "sp500_rationale": "1-2 sentence reason",
  "stock_picks": ["TICK1", "TICK2", "TICK3", "TICK4", "TICK5"],
  "stock_pick_rationales": {
    "TICK1": "brief rationale",
    "TICK2": "brief rationale",
    "TICK3": "brief rationale",
    "TICK4": "brief rationale",
    "TICK5": "brief rationale"
  }
}
</PREDICTIONS_JSON>
"""

    user_prompt = f"""Generate the Daily Business + Stock Market Briefing for {today_str}.

{prior_pred_context}

Search for and include ALL of the following:

1. MAJOR INDICES at close: S&P 500, Nasdaq, Dow Jones, Russell 2000 (levels + % change)
2. KEY MARKET NARRATIVE: What drove today's moves? (2-3 sentences)
3. SECTOR PERFORMANCE: Which sectors led/lagged?
4. TOP 5 BUSINESS NEWS STORIES that moved the market (headline + 1-2 sentence summary each)
5. NOTABLE STOCK MOVERS: Top gainers, top losers (with % moves), notable large-cap stories
6. MID/SMALL CAP MOVERS: Any mid/small caps showing unusual breakout activity
7. EARNINGS / IPO / M&A highlights from today
8. 5 STOCKS WORTH WATCHING — with 1-sentence thesis each
9. PREVIOUS PREDICTION REVIEW: Grade prior predictions (see above)
10. NEXT-DAY PREDICTIONS:
    - S&P 500 direction for the next trading day (with rationale, note any key macro events)
    - 5 stock picks for near-term gains (can be longs or short/put plays) with brief rationale each

HTML style guide:
- Dark navy header with date and index summary bar (green/red colored)
- Sections separated clearly with bold labels
- Top movers in a two-column table (green gainers | red losers)
- 5 watch stocks as cards with ticker, % move, and thesis
- Next-day picks as numbered list with bold ticker + rationale
- Footer with disclaimer
"""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8192,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # web_search_20250305 is a server-side tool — Anthropic executes searches automatically.
    # The final response arrives with stop_reason == "end_turn"; just extract text blocks.
    final_text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )

    html_body = extract_between(final_text, "<HTML_START>", "</HTML_END>")
    plain_body = extract_between(final_text, "<PLAIN_START>", "</PLAIN_END>")
    predictions_raw = extract_between(final_text, "<PREDICTIONS_JSON>", "</PREDICTIONS_JSON>")

    # Fallback: if delimiters not found, use raw text
    if not html_body:
        html_body = f"<pre style='font-family:monospace'>{final_text}</pre>"
    if not plain_body:
        plain_body = final_text

    predictions = {}
    if predictions_raw:
        try:
            predictions = json.loads(predictions_raw)
        except json.JSONDecodeError:
            pass

    return html_body, plain_body, predictions


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
    today_str = today.strftime("%B %-d, %Y")
    subject = f"Daily Business + Stock News [{today_str}]"

    print(f"Generating briefing for {today_str}...")
    last_preds = load_last_predictions()

    html_body, plain_body, new_preds = generate_briefing(today_str, last_preds)

    send_email(subject, html_body, plain_body)

    new_preds["date"] = today_str
    save_predictions(new_preds)
    print("Predictions saved for tomorrow's review.")


if __name__ == "__main__":
    main()
