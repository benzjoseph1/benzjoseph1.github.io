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


def generate_briefing(today_str: str, last_preds: dict) -> tuple[str, str, dict]:
    """
    Uses Claude with built-in web_search to generate the full briefing.
    web_search_20250305 is server-side: Anthropic executes searches automatically,
    so the response arrives at stop_reason="end_turn" without a client-side loop.
    Returns (html_body, plain_text_body, new_predictions_dict).
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if last_preds and last_preds.get("stock_picks"):
        prior_pred_context = f"""
PREVIOUS DAY'S PREDICTIONS (from {last_preds.get('date', 'prior day')}):
- S&P 500 Direction Prediction: {last_preds.get('sp500_direction', 'N/A')}
- Stock Picks: {', '.join(last_preds.get('stock_picks', []))}
- Rationales: {json.dumps(last_preds.get('stock_pick_rationales', {}), indent=2)}

Compare these predictions against what actually happened today and grade each one
(accurate / partially accurate / inaccurate) with a 1-sentence note on why.
"""
    else:
        prior_pred_context = "This is the FIRST edition — no prior predictions to review. Note that briefly."

    system_prompt = """You are a professional financial analyst producing a daily stock market briefing email.
Use the web_search tool to gather fresh, real-time data for today's market close.
Be specific with numbers (index levels, % moves, stock prices). Be concise but data-rich.

IMPORTANT: Your entire final response must be a single valid JSON object — no text before or after it — with exactly these keys:
{
  "html": "<full HTML email body as a string — styled, scannable>",
  "plain": "<plain text version as a string>",
  "predictions": {
    "sp500_direction": "UP ~X% / DOWN ~X% / FLAT",
    "sp500_rationale": "1-2 sentence reason",
    "stock_picks": ["TICK1", "TICK2", "TICK3", "TICK4", "TICK5"],
    "stock_pick_rationales": {"TICK1": "reason", "TICK2": "reason", ...}
  }
}
Escape all inner quotes and newlines properly for valid JSON."""

    user_prompt = f"""Generate the Daily Business + Stock Market Briefing for {today_str}.

{prior_pred_context}

Search for and include ALL of the following sections:

1. MAJOR INDICES at close: S&P 500, Nasdaq, Dow Jones, Russell 2000 (exact levels + % change)
2. KEY MARKET NARRATIVE: 2-3 sentences explaining what drove today's moves
3. SECTOR PERFORMANCE: Which sectors led and which lagged today
4. TOP 5 BUSINESS NEWS STORIES that moved the market (headline + 1-2 sentence summary each)
5. NOTABLE LARGE-CAP MOVERS: Top gainers and losers with % moves
6. MID/SMALL CAP WATCH: Any mid/small cap stocks showing unusual breakout or breakdown activity
7. EARNINGS / IPO / M&A highlights from today
8. 5 STOCKS WORTH WATCHING — with 1-sentence thesis each (can include ones showing major moves)
9. PREVIOUS PREDICTION REVIEW: Grade the prior predictions (see above)
10. NEXT-DAY PREDICTIONS:
    - S&P 500 direction for the next trading day (with rationale)
    - 5 stock picks for near-term gains (longs or shorts) with brief rationale each

HTML style requirements:
- Dark navy header bar with white title and date
- Color-coded index cards (green for up, red for down)
- Scannable section headers
- Clean table/card layout that renders well in Gmail
"""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8192,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Extract the final text (web_search_20250305 is server-side; stop_reason should be end_turn)
    final_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_text += block.text

    # Parse JSON — handle both bare JSON and JSON wrapped in a markdown code block
    data = None
    json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", final_text)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    if data is None:
        json_match = re.search(r"\{[\s\S]*\}", final_text)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

    if data is None:
        # Fallback: wrap the raw response so the email still delivers
        data = {
            "html": f"<pre style='font-family:sans-serif'>{final_text}</pre>",
            "plain": final_text,
            "predictions": {
                "sp500_direction": "See email",
                "sp500_rationale": "",
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
    today_str = today.strftime("%B %-d, %Y")  # e.g. "June 12, 2026"
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
