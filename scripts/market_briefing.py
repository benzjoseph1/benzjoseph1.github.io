#!/usr/bin/env python3
"""
Daily Market Briefing Generator
Runs at ~4pm PST weekdays via GitHub Actions.
Uses Claude + Anthropic web_search to gather market data, then emails via Gmail SMTP.
"""

import json
import os
import re
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

RECIPIENT_EMAIL = "benzjoseph1@gmail.com"
SENDER_EMAIL = os.environ["GMAIL_USERNAME"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

PREDICTIONS_FILE = os.path.join(os.path.dirname(__file__), "last_predictions.json")

# Model to use — sonnet is faster and cheaper for this daily task
MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Predictions persistence
# ---------------------------------------------------------------------------

def load_last_predictions() -> dict:
    if os.path.exists(PREDICTIONS_FILE):
        with open(PREDICTIONS_FILE) as f:
            return json.load(f)
    return {}


def save_predictions(predictions: dict):
    with open(PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2)


# ---------------------------------------------------------------------------
# Briefing generation via Claude + web search
# ---------------------------------------------------------------------------

def call_claude(client: anthropic.Anthropic, system: str, messages: list) -> str:
    """
    Run a Claude inference loop, handling the web_search_20250305 tool.
    The tool is executed server-side by Anthropic — the client only needs to
    acknowledge tool_use turns with empty tool_result content so Claude can
    continue to its final answer.
    """
    for _ in range(20):  # safety cap on turns
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

        if response.stop_reason == "tool_use":
            # Web search is server-side — Anthropic executes it automatically.
            # We just acknowledge with empty tool_result content so Claude continues.
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",
                        }
                        for block in response.content
                        if block.type == "tool_use"
                    ],
                },
            ]
        else:
            # Unexpected stop reason — return whatever text we have
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

    return "Error: exceeded max turns in Claude loop"


def generate_briefing(today_str: str, last_preds: dict) -> tuple[str, dict]:
    """
    Generate the full HTML briefing and extract next-day predictions.
    Returns (html_body, predictions_dict).
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    today = date.today()
    next_trading_day = today + timedelta(days=1)
    while next_trading_day.weekday() >= 5:  # skip weekends
        next_trading_day += timedelta(days=1)
    next_day_str = next_trading_day.strftime("%B %-d, %Y")

    # Build prior predictions review section
    if last_preds:
        picks = last_preds.get("stock_picks", [])
        if isinstance(picks, list) and picks and isinstance(picks[0], dict):
            picks_formatted = "\n".join(
                f"  • {p['ticker']} ({p.get('direction','')}) — {p.get('thesis','')}"
                for p in picks
            )
        else:
            picks_formatted = str(picks)

        prior_section = f"""
PREVIOUS DAY'S PREDICTIONS TO GRADE (made on {last_preds.get('date', 'prior day')}):
  S&P 500 call: {last_preds.get('sp500_direction', 'N/A')}
  Stock picks:
{picks_formatted}

Search for what actually happened with each of these today, then grade each prediction
as CORRECT, INCORRECT, or MIXED with a 1-sentence explanation."""
    else:
        prior_section = "PREVIOUS PREDICTIONS: This is the first edition — note there are no prior predictions to grade."

    system_prompt = """You are a professional financial analyst writing a daily after-market briefing email.
Use web_search to gather real, current data — never guess numbers.
Output EXACTLY the structure requested: HTML email first, then the prediction JSON block delimited as shown."""

    user_prompt = f"""Write the Daily Business + Stock Market Briefing for {today_str}.

{prior_section}

STEP 1 — Search for all of the following (use multiple searches):
  • "S&P 500 Nasdaq Dow close {today_str}" — index levels and % changes
  • "top business news stock market {today_str}" — major stories
  • "biggest stock gainers losers movers {today_str}" — with reasons
  • "sector performance {today_str}" — which sectors led/lagged
  • "earnings IPO merger acquisition {today_str}" — corporate events
  • Any grading searches needed for the prior predictions above

STEP 2 — Output a complete HTML email body using this structure and inline style guide:

<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#1a1a1a;max-width:720px;margin:0 auto;background:#f5f5f5;">
<div style="background:#fff;padding:28px 32px;border-radius:8px;">

  <!-- HEADER -->
  <h1 style="font-size:22px;color:#0a2540;border-bottom:3px solid #0062ff;padding-bottom:10px;">
    Daily Business + Stock Briefing — {today_str}
  </h1>

  <!-- SECTION 1: MARKET OVERVIEW -->
  <!-- Table showing S&P 500, Nasdaq, Dow, Russell 2000: level, % change (green/red), brief driver note -->

  <!-- SECTION 2: SECTOR PERFORMANCE -->
  <!-- Colored chips for each sector with % change -->

  <!-- SECTION 3: KEY MARKET NARRATIVE -->
  <!-- 2-3 sentences on what drove today's moves -->

  <!-- SECTION 4: TOP BUSINESS NEWS (4-6 stories) -->
  <!-- Each: category tag (TECH/MACRO/M&A/HEALTH/etc), bold headline, 1-2 sentence summary -->

  <!-- SECTION 5: EARNINGS / IPO / M&A -->
  <!-- Notable corporate events from today -->

  <!-- SECTION 6: 5 NOTABLE STOCK MOVERS -->
  <!-- Cards: ticker, company, % move (colored), 1-sentence reason -->
  <!-- Include both large caps and any notable mid/small caps showing breakout activity -->

  <!-- SECTION 7: PREVIOUS PREDICTION REVIEW -->
  <!-- Grade prior predictions, or note it's the first edition -->

  <!-- SECTION 8: TOMORROW'S PREDICTIONS ({next_day_str}) -->
  <!-- S&P 500 direction call with rationale -->
  <!-- 5 stock picks: ticker, LONG/SHORT, 1-sentence thesis -->

  <p style="font-size:11px;color:#888;border-top:1px solid #eee;padding-top:12px;">
    Disclaimer: For informational purposes only. Not investment advice.
  </p>
</div>
</body></html>

Use green (#0a8a0a bold) for gains, red (#cc0000 bold) for losses.
Section headers: background #eef3ff, left border 4px solid #0062ff, padding 7px 12px.

STEP 3 — After the HTML, on a new line output EXACTLY this block (no other text after the HTML):

===PREDICTIONS_JSON_START===
{{
  "date": "{today.isoformat()}",
  "sp500_direction": "UP/DOWN/FLAT ~X% — one sentence rationale",
  "stock_picks": [
    {{"ticker": "TICK", "direction": "LONG", "thesis": "one sentence"}},
    {{"ticker": "TICK", "direction": "LONG", "thesis": "one sentence"}},
    {{"ticker": "TICK", "direction": "SHORT", "thesis": "one sentence"}},
    {{"ticker": "TICK", "direction": "LONG", "thesis": "one sentence"}},
    {{"ticker": "TICK", "direction": "LONG", "thesis": "one sentence"}}
  ]
}}
===PREDICTIONS_JSON_END==="""

    raw = call_claude(client, system_prompt, [{"role": "user", "content": user_prompt}])

    # Extract predictions JSON using explicit delimiters
    pred_match = re.search(
        r"===PREDICTIONS_JSON_START===\s*(.*?)\s*===PREDICTIONS_JSON_END===",
        raw,
        re.DOTALL,
    )
    predictions = {}
    if pred_match:
        try:
            predictions = json.loads(pred_match.group(1).strip())
        except json.JSONDecodeError as e:
            print(f"Warning: could not parse predictions JSON: {e}")
            predictions = {"date": today.isoformat(), "sp500_direction": "N/A", "stock_picks": []}

    # HTML is everything before the predictions block
    html_body = raw.split("===PREDICTIONS_JSON_START===")[0].strip()

    # Ensure it looks like HTML — wrap if needed
    if not (html_body.lstrip().startswith("<!") or html_body.lstrip().startswith("<html")):
        html_body = f"<html><body style='font-family:Arial,sans-serif;'>{html_body}</body></html>"

    return html_body, predictions


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    plain = "Please open this email in an HTML-capable client to view the briefing."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"✓ Email sent to {RECIPIENT_EMAIL}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    today = date.today()
    today_str = today.strftime("%B %-d, %Y")
    subject = f"Daily Business + Stock News [{today_str}]"

    print(f"Generating briefing for {today_str}…")
    last_preds = load_last_predictions()
    print(f"Prior predictions loaded: {bool(last_preds)}")

    html_body, new_preds = generate_briefing(today_str, last_preds)

    print("Sending email…")
    send_email(subject, html_body)

    if new_preds:
        save_predictions(new_preds)
        print("✓ Predictions saved for tomorrow's review.")
    else:
        print("Warning: no predictions parsed — predictions file not updated.")


if __name__ == "__main__":
    main()
