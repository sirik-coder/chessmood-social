"""
read_inspiration.py
===================

Layer 2.5 - "read the spreadsheet for me".

Reads the Inspiration tab (competitor outlier posts) and the Analysis_Snapshot
tab (your own numbers), sends both to Google Gemini, and gets back plain
sentences telling you what to actually post.

Output goes to three places:
  1. The terminal, so you see it immediately
  2. insights.md, a small file you can reopen any time
  3. A tab called AI_Insights in the same Google Sheet
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai

import config
from sheets_client import SheetsClient, SheetsError

load_dotenv()

# ---------------------------------------------------------------------------
# Settings you may want to change later
# ---------------------------------------------------------------------------

INSPIRATION_TAB = "Inspiration"
SNAPSHOT_TAB = "Analysis_Snapshot"
OUTPUT_TAB = "AI_Insights"
OUTPUT_FILE = "insights.md"

GEMINI_MODEL = "gemini-3.6-flash"

# Captions can be very long. Cut them so the request stays small and cheap.
MAX_CAPTION_CHARS = 300

# Safety brake: how many Inspiration rows to send at most.
MAX_ROWS = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_tab_as_text(sheets: SheetsClient, tab_name: str) -> str:
    """
    Read one tab and turn it into simple text lines Gemini can understand.
    Returns "" if the tab does not exist.
    """
    try:
        worksheet = sheets._retry(sheets.spreadsheet.worksheet, tab_name)
    except Exception:
        print(f"   Tab {tab_name!r} not found - skipping it.")
        return ""

    values = sheets._retry(worksheet.get_all_values)
    if len(values) < 2:
        print(f"   Tab {tab_name!r} is empty - skipping it.")
        return ""

    header = values[0]
    data_rows = values[1:MAX_ROWS + 1]

    lines = []
    for i, row in enumerate(data_rows, start=1):
        parts = []
        for column_index, column_name in enumerate(header):
            if not column_name.strip():
                continue
            cell = row[column_index] if column_index < len(row) else ""
            cell = str(cell).strip()
            if not cell:
                continue
            if len(cell) > MAX_CAPTION_CHARS:
                cell = cell[:MAX_CAPTION_CHARS] + "..."
            parts.append(f"{column_name}={cell}")
        if parts:
            lines.append(f"[{i}] " + " | ".join(parts))

    print(f"   Read {len(lines)} rows from {tab_name!r}.")
    return "\n".join(lines)


def build_prompt(inspiration_text: str, snapshot_text: str) -> str:
    """Write the question we send to Gemini."""

    own_data_block = (
        f"MY OWN RECENT PERFORMANCE:\n{snapshot_text}\n\n"
        if snapshot_text
        else "MY OWN PERFORMANCE DATA: not available this time.\n\n"
    )

    return f"""You are a social media analyst helping ChessMood, a chess
education company that teaches chess online. Their Instagram account posts
chess lessons, tips, and course promotions.

I will give you two sets of data from a spreadsheet.

{own_data_block}COMPETITOR AND TEACHING ACCOUNT OUTLIER POSTS
(these are posts that got at least 2x the normal likes for that account):
{inspiration_text}

Write your answer in VERY SIMPLE ENGLISH. The reader is not a native English
speaker. Short sentences. No marketing jargon. No words like "leverage",
"synergy", "engagement funnel".

Give me exactly these five sections, using these headings:

## 1. What is working (5 sentences)
Five plain sentences about patterns you actually see in the competitor data.
Each sentence must point to real evidence - say how many posts showed it.

## 2. What ChessMood should copy (3 ideas)
Three specific things worth trying, based on the data above. For each one say
in one sentence WHY the data supports it.

## 3. What to avoid (2 things)
Two patterns that clearly did NOT work, with the evidence.

## 4. Five post ideas for next week
Five concrete post ideas. For each: a one-line hook, the format
(carousel / reel / single image), and which pattern from the data it copies.

## 5. One thing I am not sure about
Name one place where the data is too thin to trust, and say what extra data
would fix it. Be honest here - do not invent confidence you do not have.

Do not add any other sections. Do not repeat the raw data back to me.
"""


def ask_gemini(prompt: str) -> str:
    """Send the prompt to Gemini and return the text answer."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found.\n"
            "  -> Add a line like  GEMINI_API_KEY=your_key  to your .env file."
        )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return response.text or "(Gemini returned an empty answer.)"


def save_to_sheet(sheets: SheetsClient, report: str, stamp: str) -> None:
    """Write the report into the AI_Insights tab, one line per row."""
    worksheet, _, _ = sheets.ensure_worksheet(OUTPUT_TAB, ["generated_at", "line"])

    # Wipe the old report so we never stack two reports on top of each other.
    sheets._retry(worksheet.batch_clear, ["A2:B10000"])

    rows = [[stamp, line] for line in report.splitlines() if line.strip()]
    if rows:
        sheets._retry(
            worksheet.append_rows,
            rows,
            value_input_option="RAW",
            insert_data_option="INSERT_ROWS",
            table_range="A1",
        )
    print(f"   Wrote {len(rows)} lines to the {OUTPUT_TAB!r} tab.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("   ChessMood social analysis  -  Layer 2.5  (read it for me)")
    print("=" * 70)

    settings = config.get_settings()

    sheets = SheetsClient(
        service_account_info=settings.google_service_account_info,
        sheet_id=settings.google_sheet_id,
    )

    inspiration_text = read_tab_as_text(sheets, INSPIRATION_TAB)
    if not inspiration_text:
        print("\nStopping: there is nothing in the Inspiration tab to read.")
        print("Run  python inspire.py  first, then try again.")
        return

    snapshot_text = read_tab_as_text(sheets, SNAPSHOT_TAB)

    print("\n   Asking Gemini... (this takes 10-30 seconds)")
    report = ask_gemini(build_prompt(inspiration_text, snapshot_text))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print("\n" + "=" * 70)
    print(report)
    print("=" * 70 + "\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write(f"# ChessMood social insights\n\nGenerated: {stamp}\n\n{report}\n")
    print(f"   Saved to {OUTPUT_FILE}")

    save_to_sheet(sheets, report, stamp)

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except (config.ConfigError, SheetsError, RuntimeError) as error:
        print("\n" + "!" * 70)
        print("Something went wrong:\n")
        print(error)
        print("!" * 70)
