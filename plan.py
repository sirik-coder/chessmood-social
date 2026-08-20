"""
plan.py  -  Layer 5: the weekly content plan
============================================

WHAT THIS DOES
--------------
It looks at what already worked, then tells you what to post THIS WEEK:
  - how many posts
  - which format (carousel / reel / image)
  - which content type (lesson, puzzle, promo...)
  - which topic (taken from your Backlog tab, which YOU fill in)

WHAT IT DOES NOT DO
-------------------
It does not invent topics. It never guesses what chess content to make.
Topics come from the "Backlog" tab in your Google Sheet.
You fill that tab from the ChessMood website, courses and blog.

WHY EXPERIMENTS
---------------
Some content types have only 2 or 3 posts. That is not enough to know
anything. So every week a few slots are marked EXPERIMENT: posts made on
purpose to find out whether something works. After a few weeks those
guesses turn into real answers.

HOW TO RUN
----------
    python plan.py                 (default 4 posts for the week)
    python plan.py --posts 5       (plan 5 posts instead)
    python plan.py --no-write      (print only, do not save to the sheet)
"""

import os
import sys
import json
import math
import argparse
from datetime import datetime, timedelta, timezone

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from tags_config import tag_caption


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

POSTS_TAB = "IG_Posts"
BACKLOG_TAB = "Backlog"
PLAN_TAB = "Weekly_Plan"

# A content type needs at least this many posts before we trust its numbers.
TRUST_THRESHOLD = 5

# Content types we never want to disappear, even if they score low.
# Community posts are not for saves. They are for warmth.
ALWAYS_KEEP = ["community"]


# ---------------------------------------------------------------------------
# CONNECTION
# ---------------------------------------------------------------------------

def connect_to_sheet():
    load_dotenv()
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    key_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not sheet_id or not key_value:
        sys.exit("ERROR: GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON missing from .env")

    if key_value.startswith("{"):
        info = json.loads(key_value)
    else:
        if not os.path.exists(key_value):
            sys.exit(f"ERROR: key file not found at: {key_value}")
        with open(key_value, "r", encoding="utf-8") as f:
            info = json.load(f)

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(sheet_id)
    except Exception as e:
        sys.exit(f"ERROR: could not open the sheet.\n  {e}")


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------

def to_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def safe_divide(top, bottom):
    if top is None or bottom is None or bottom <= 0:
        return None
    return top / bottom


def average(numbers):
    clean = [n for n in numbers if n is not None]
    return sum(clean) / len(clean) if clean else None


def pct(rate):
    return "  -  " if rate is None else f"{rate * 100:.2f}%"


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def line(char="-", width=74):
    print(char * width)


# ---------------------------------------------------------------------------
# READ THE PAST
# ---------------------------------------------------------------------------

def load_posts(sheet):
    """Read IG_Posts and work out the rates for every post."""
    try:
        tab = sheet.worksheet(POSTS_TAB)
    except gspread.WorksheetNotFound:
        sys.exit(f"ERROR: tab '{POSTS_TAB}' not found. Run collect.py first.")

    posts = []
    for row in tab.get_all_records():
        reach = to_number(row.get("reach"))
        post = {
            "date": parse_date(row.get("date")),
            "caption": row.get("caption", ""),
            "tag": tag_caption(row.get("caption", "")),
            "save_rate": safe_divide(to_number(row.get("saved")), reach),
            "share_rate": safe_divide(to_number(row.get("shares")), reach),
            "reach": reach,
        }

        media = (row.get("media_type") or "").upper()
        product = (row.get("media_product_type") or "").upper()
        if product == "REELS":
            post["format"] = "REEL"
        elif media.startswith("CAROUSEL"):
            post["format"] = "CAROUSEL"
        elif media == "IMAGE":
            post["format"] = "IMAGE"
        else:
            post["format"] = media or "UNKNOWN"

        posts.append(post)

    return posts


def score_groups(posts, field):
    """Average the rates for each group, and note how many posts it has."""
    buckets = {}
    for p in posts:
        buckets.setdefault(p[field], []).append(p)

    out = {}
    for name, group in buckets.items():
        out[name] = {
            "count": len(group),
            "save_rate": average([p["save_rate"] for p in group]),
            "share_rate": average([p["share_rate"] for p in group]),
            "avg_reach": average([p["reach"] for p in group]),
            "trusted": len(group) >= TRUST_THRESHOLD,
        }
    return out


# ---------------------------------------------------------------------------
# READ THE BACKLOG (topics you added yourself)
# ---------------------------------------------------------------------------

def load_backlog(sheet):
    """
    Read the Backlog tab. If it does not exist, create it with headers and
    a few example rows, so you know what to type.
    """
    try:
        tab = sheet.worksheet(BACKLOG_TAB)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title=BACKLOG_TAB, rows=300, cols=6)
        tab.append_row(["topic", "content_type", "source_url", "notes", "used", "used_date"])
        tab.append_rows([
            ["5 endgame mistakes beginners make", "lesson_tip",
             "https://chessmood.com/blog/...", "from the endgame course", "", ""],
            ["Can you find the mate in 2?", "puzzle", "", "use a position from the tactics course", "", ""],
            ["How our student went from 1200 to 1600", "student_story", "", "ask the coaches", "", ""],
        ], value_input_option="RAW")
        print(f"\n  Created the '{BACKLOG_TAB}' tab with 3 example rows.")
        print("  Open it and add your own topics from the ChessMood website.\n")

    items = []
    for row in tab.get_all_records():
        topic = str(row.get("topic", "")).strip()
        if not topic:
            continue
        used = str(row.get("used", "")).strip().lower()
        if used in ("yes", "y", "true", "1", "done"):
            continue  # already posted, skip it
        items.append({
            "topic": topic,
            "content_type": str(row.get("content_type", "")).strip().lower(),
            "source_url": str(row.get("source_url", "")).strip(),
            "notes": str(row.get("notes", "")).strip(),
        })
    return items


def take_topic(backlog, wanted_type):
    """Pick and remove one unused topic matching this content type."""
    for i, item in enumerate(backlog):
        if item["content_type"] == wanted_type:
            return backlog.pop(i)
    return None


# ---------------------------------------------------------------------------
# READ WHICH EXPERIMENTS WE ALREADY RAN
# ---------------------------------------------------------------------------

def recent_experiments(sheet, weeks=3):
    """Look at past plans so we do not repeat the same experiment every week."""
    try:
        tab = sheet.worksheet(PLAN_TAB)
    except gspread.WorksheetNotFound:
        return []

    cutoff = datetime.now(timezone.utc).date() - timedelta(weeks=weeks)
    done = []
    for row in tab.get_all_records():
        when = parse_date(row.get("week_of"))
        if when and when >= cutoff and str(row.get("purpose", "")).upper() == "EXPERIMENT":
            done.append(str(row.get("content_type", "")).strip())
    return done


# ---------------------------------------------------------------------------
# BUILD THE PLAN
# ---------------------------------------------------------------------------

def build_plan(posts, by_tag, by_format, backlog, already_tested, total_posts):
    """
    Decide the slots for this week.

    The rule is simple:
      - Most slots go to what is PROVEN to work.
      - Some slots go to EXPERIMENTS, to test what we do not know yet.
      - One slot is kept for business needs (promoting a course).
    """

    # --- which content types do we actually trust? ---
    trusted = {k: v for k, v in by_tag.items()
               if v["trusted"] and v["save_rate"] is not None and k != "other"}
    unproven = {k: v for k, v in by_tag.items()
                if not v["trusted"] and k != "other"}

    # Best proven type = highest save rate among trusted ones
    ranked_trusted = sorted(trusted.items(),
                            key=lambda kv: kv[1]["save_rate"], reverse=True)

    # Which format wins? Only trust formats with enough posts.
    trusted_formats = {k: v for k, v in by_format.items()
                       if v["count"] >= TRUST_THRESHOLD and v["save_rate"] is not None}
    ranked_formats = sorted(trusted_formats.items(),
                            key=lambda kv: kv[1]["save_rate"], reverse=True)
    best_format = ranked_formats[0][0] if ranked_formats else "CAROUSEL"

    # --- how many experiment slots? about a third, at least one ---
    experiment_slots = max(1, round(total_posts * 0.3)) if unproven else 0
    experiment_slots = min(experiment_slots, total_posts - 1)

    # --- pick which experiments to run, avoiding recent repeats ---
    experiment_order = sorted(
        unproven.items(),
        key=lambda kv: (
            kv[0] in already_tested,          # not tested recently comes first
            -(kv[1]["save_rate"] or 0),       # then the most promising
        ),
    )

    slots = []

    # 1. Experiments
    for name, stats in experiment_order[:experiment_slots]:
        slots.append({
            "purpose": "EXPERIMENT",
            "content_type": name,
            "format": best_format,
            "reason": (
                f"only {stats['count']} post(s) so far - not enough to know. "
                f"Early signal: {pct(stats['save_rate'])} save."
            ),
        })

    # 2. Proven winners fill the rest, minus one slot for business
    business_slots = 1 if total_posts >= 3 else 0
    proven_slots = total_posts - len(slots) - business_slots

    if ranked_trusted:
        best_name, best_stats = ranked_trusted[0]
        for _ in range(max(0, proven_slots)):
            slots.append({
                "purpose": "PROVEN",
                "content_type": best_name,
                "format": best_format,
                "reason": (
                    f"your strongest reliable type: {pct(best_stats['save_rate'])} save "
                    f"across {best_stats['count']} posts."
                ),
            })

    # 3. One business slot - you have to promote courses sometimes
    if business_slots:
        promo = by_tag.get("course_promo", {})
        slots.append({
            "purpose": "BUSINESS",
            "content_type": "course_promo",
            "format": best_format,
            "reason": (
                f"needed for the business, but keep it to one. "
                f"Promos save at {pct(promo.get('save_rate'))} - the weakest type."
            ),
        })

    # --- attach a topic from the backlog to each slot ---
    for slot in slots:
        found = take_topic(backlog, slot["content_type"])
        if found:
            slot["topic"] = found["topic"]
            slot["source_url"] = found["source_url"]
            slot["notes"] = found["notes"]
        else:
            slot["topic"] = "(no topic in backlog - add one)"
            slot["source_url"] = ""
            slot["notes"] = ""

    return slots, best_format, ranked_formats, ranked_trusted


# ---------------------------------------------------------------------------
# PRINTING
# ---------------------------------------------------------------------------

def print_plan(slots, week_start, posts, ranked_formats, ranked_trusted):
    days = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]
    spread = {1: [1], 2: [1, 4], 3: [1, 3, 5], 4: [0, 2, 4, 6],
              5: [0, 1, 3, 4, 6], 6: [0, 1, 2, 3, 4, 6],
              7: [0, 1, 2, 3, 4, 5, 6]}
    day_slots = spread.get(len(slots), list(range(len(slots))))

    print()
    line("=")
    print(f" YOUR PLAN FOR THE WEEK OF {week_start}")
    line("=")

    for i, slot in enumerate(slots):
        day = days[day_slots[i]] if i < len(day_slots) else "Any day"
        badge = {"EXPERIMENT": "[TEST]", "PROVEN": "[SAFE]", "BUSINESS": "[BIZ ]"}[slot["purpose"]]

        print()
        print(f" {badge}  {day}")
        print(f"   Format : {slot['format']}")
        print(f"   Type   : {slot['content_type']}")
        print(f"   Topic  : {slot['topic']}")
        if slot["source_url"]:
            print(f"   Source : {slot['source_url']}")
        if slot["notes"]:
            print(f"   Note   : {slot['notes']}")
        print(f"   Why    : {slot['reason']}")

    # --- the evidence behind the plan ---
    print()
    line("=")
    print(" WHY THIS PLAN  (the numbers behind it)")
    line("=")

    print("\n Formats we trust:")
    for name, s in ranked_formats:
        print(f"   {name:<10} {pct(s['save_rate'])} save   {s['count']:>3} posts")

    print("\n Content types we trust (5+ posts):")
    if ranked_trusted:
        for name, s in ranked_trusted:
            print(f"   {name:<16} {pct(s['save_rate'])} save   {s['count']:>3} posts")
    else:
        print("   None yet. Everything is still an experiment.")

    # --- posting pace ---
    dates = [p["date"] for p in posts if p["date"]]
    if len(dates) > 1:
        span_days = (max(dates) - min(dates)).days
        if span_days > 0:
            per_week = len(dates) / (span_days / 7)
            print(f"\n Your pace so far: {per_week:.1f} posts per week "
                  f"({len(dates)} posts over {span_days} days).")

    print()
    line("=")
    print(" HOW TO USE THIS")
    line("=")
    print(" [SAFE] = do this, the data supports it")
    print(" [TEST] = we do not know yet, this post is how we find out")
    print(" [BIZ ] = needed for business, not for performance")
    print()
    print(" Topics come from your Backlog tab. If a slot says")
    print(" '(no topic in backlog)', open the sheet and add topics")
    print(" from the ChessMood website for that content type.")
    print()
    print(" After you post, mark the topic as 'yes' in the used column.")
    print()


# ---------------------------------------------------------------------------
# SAVE THE PLAN
# ---------------------------------------------------------------------------

def write_plan(sheet, slots, week_start):
    try:
        tab = sheet.worksheet(PLAN_TAB)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title=PLAN_TAB, rows=500, cols=8)
        tab.append_row(["week_of", "purpose", "format", "content_type",
                        "topic", "reason", "posted", "result_notes"])

    rows = [[
        str(week_start), s["purpose"], s["format"], s["content_type"],
        s["topic"], s["reason"], "", "",
    ] for s in slots]

    tab.append_rows(rows, value_input_option="RAW")
    print(f" Saved {len(rows)} rows to the '{PLAN_TAB}' tab.\n")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build the weekly content plan.")
    parser.add_argument("--posts", type=int, default=4,
                        help="How many posts to plan for the week. Default 4.")
    parser.add_argument("--no-write", action="store_true",
                        help="Print the plan but do not save it to the sheet.")
    args = parser.parse_args()

    if args.posts < 1 or args.posts > 7:
        sys.exit("ERROR: --posts must be between 1 and 7.")

    print()
    line("=")
    print(" ChessMood weekly plan  -  Layer 5")
    line("=")

    sheet = connect_to_sheet()
    print(f" Connected to: '{sheet.title}'")

    posts = load_posts(sheet)
    if not posts:
        sys.exit(" No posts found. Run collect.py first.")

    by_tag = score_groups(posts, "tag")
    by_format = score_groups(posts, "format")

    backlog = load_backlog(sheet)
    print(f" Posts analysed : {len(posts)}")
    print(f" Topics waiting : {len(backlog)}")

    already_tested = recent_experiments(sheet)

    # This coming Monday
    today = datetime.now(timezone.utc).date()
    week_start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    slots, best_format, ranked_formats, ranked_trusted = build_plan(
        posts, by_tag, by_format, backlog, already_tested, args.posts
    )

    print_plan(slots, week_start, posts, ranked_formats, ranked_trusted)

    if not args.no_write:
        write_plan(sheet, slots, week_start)


if __name__ == "__main__":
    main()
