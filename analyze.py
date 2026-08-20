"""
analyze.py  -  Layer 2 for ChessMood Social Analytics
=====================================================

WHAT THIS DOES
--------------
It reads the IG_Posts tab from your Google Sheet.
It does NOT call the Meta API. It only reads what collect.py already saved.

Then it answers three questions:
  1. Which posts perform best per person reached (not per raw number)?
  2. Which CONTENT TYPES (puzzle, lesson, story...) earn the most saves?
  3. Which FORMATS (carousel, reel, image) earn the most saves?

HOW TO RUN
----------
    python analyze.py

Optional:
    python analyze.py --days 90     (only look at the last 90 days)
    python analyze.py --no-write    (print only, do not touch the sheet)

WHY RATES AND NOT RAW NUMBERS
-----------------------------
A post that reached 30,000 people will get more saves than one that reached
2,000. That does not make it a better post. So we divide:

    save rate = saves / reach

Now a small post and a big post can be compared fairly.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from tags_config import tag_caption


# ---------------------------------------------------------------------------
# 1. SETUP
# ---------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

POSTS_TAB = "IG_Posts"
SNAPSHOT_TAB = "Analysis_Snapshot"


def connect_to_sheet():
    """Open the Google Sheet using the key from .env."""
    load_dotenv()

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    key_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not sheet_id:
        sys.exit("ERROR: GOOGLE_SHEET_ID is missing from .env")
    if not key_value:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON is missing from .env")

    # The value can be a PATH to a .json file, or the whole JSON as text.
    # Support both, so this also works later on GitHub Actions.
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
        sys.exit(
            f"ERROR: could not open the sheet.\n"
            f"  {e}\n"
            f"  Check the sheet is shared with: {info.get('client_email')}"
        )


# ---------------------------------------------------------------------------
# 2. SMALL HELPERS
# ---------------------------------------------------------------------------

def to_number(value):
    """Turn a cell into a number. Empty or broken cells become None."""
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
    """Divide, but never crash. Returns None if it cannot be done."""
    if top is None or bottom is None:
        return None
    if bottom <= 0:
        return None
    return top / bottom


def parse_date(value):
    """Read the 'date' column (YYYY-MM-DD). Returns None if unreadable."""
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def pct(rate):
    """Show a rate as a percentage string, or a dash if we have no number."""
    if rate is None:
        return "   -  "
    return f"{rate * 100:5.2f}%"


def average(numbers):
    """Average of a list, ignoring None. Returns None if the list is empty."""
    clean = [n for n in numbers if n is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def short(text, length=55):
    """Cut long captions so the terminal stays readable."""
    if not text:
        return ""
    one_line = " ".join(str(text).split())
    if len(one_line) <= length:
        return one_line
    return one_line[:length - 1] + "…"


# ---------------------------------------------------------------------------
# 3. LOAD AND PREPARE THE POSTS
# ---------------------------------------------------------------------------

def load_posts(sheet):
    """Read IG_Posts and turn every row into a clean dictionary."""
    try:
        tab = sheet.worksheet(POSTS_TAB)
    except gspread.WorksheetNotFound:
        sys.exit(f"ERROR: tab '{POSTS_TAB}' not found. Run collect.py first.")

    rows = tab.get_all_records()
    if not rows:
        sys.exit(f"ERROR: tab '{POSTS_TAB}' is empty. Run collect.py first.")

    posts = []
    for row in rows:
        reach = to_number(row.get("reach"))
        saved = to_number(row.get("saved"))
        shares = to_number(row.get("shares"))
        follows = to_number(row.get("follows"))
        interactions = to_number(row.get("total_interactions"))
        visits = to_number(row.get("profile_visits"))

        post = {
            "id": row.get("id"),
            "date": parse_date(row.get("date")),
            "caption": row.get("caption", ""),
            "permalink": row.get("permalink", ""),
            "media_type": (row.get("media_type") or "UNKNOWN").strip(),
            "product_type": (row.get("media_product_type") or "").strip(),
            "reach": reach,
            "saved": saved,
            "shares": shares,
            "follows": follows,

            # The four numbers that actually matter:
            "save_rate": safe_divide(saved, reach),
            "share_rate": safe_divide(shares, reach),
            "follow_rate": safe_divide(follows, reach),
            "engagement_rate": safe_divide(interactions, reach),
            "visit_rate": safe_divide(visits, reach),
        }

        post["tag"] = tag_caption(post["caption"])

        # A nice readable format name, e.g. "CAROUSEL" or "REELS"
        if post["product_type"].upper() == "REELS":
            post["format"] = "REEL"
        elif post["media_type"].upper().startswith("CAROUSEL"):
            post["format"] = "CAROUSEL"
        elif post["media_type"].upper() == "IMAGE":
            post["format"] = "IMAGE"
        elif post["media_type"].upper() == "VIDEO":
            post["format"] = "VIDEO"
        else:
            post["format"] = post["media_type"].upper() or "UNKNOWN"

        posts.append(post)

    return posts


def filter_by_days(posts, days):
    """Keep only posts newer than X days. days=None means keep everything."""
    if not days:
        return posts
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return [p for p in posts if p["date"] and p["date"] >= cutoff]


# ---------------------------------------------------------------------------
# 4. GROUPING
# ---------------------------------------------------------------------------

def summarise_group(posts):
    """Average the important rates for one group of posts."""
    return {
        "count": len(posts),
        "save_rate": average([p["save_rate"] for p in posts]),
        "share_rate": average([p["share_rate"] for p in posts]),
        "follow_rate": average([p["follow_rate"] for p in posts]),
        "engagement_rate": average([p["engagement_rate"] for p in posts]),
        "median_reach": average([p["reach"] for p in posts]),
    }


def group_by(posts, field):
    """Split posts into groups by 'tag' or 'format', and summarise each."""
    buckets = {}
    for p in posts:
        buckets.setdefault(p[field], []).append(p)

    result = {}
    for name, group_posts in buckets.items():
        result[name] = summarise_group(group_posts)
    return result


# ---------------------------------------------------------------------------
# 5. PRINTING
# ---------------------------------------------------------------------------

def line(char="-", width=74):
    print(char * width)


def print_group_table(title, grouped, note=""):
    print()
    line("=")
    print(f" {title}")
    if note:
        print(f" {note}")
    line("=")
    print(f" {'group':<18}{'posts':>6}{'save':>9}{'share':>9}{'follow':>9}{'avg reach':>12}")
    line()

    # Sort so the best save rate is on top. Groups with no data go last.
    def sort_key(item):
        rate = item[1]["save_rate"]
        return -1 if rate is None else rate

    for name, s in sorted(grouped.items(), key=sort_key, reverse=True):
        reach_text = "-" if s["median_reach"] is None else f"{s['median_reach']:,.0f}"
        print(
            f" {name:<18}{s['count']:>6}"
            f"{pct(s['save_rate']):>9}"
            f"{pct(s['share_rate']):>9}"
            f"{pct(s['follow_rate']):>9}"
            f"{reach_text:>12}"
        )


def print_post_list(title, posts, limit=5):
    print()
    line("=")
    print(f" {title}")
    line("=")
    for p in posts[:limit]:
        date_text = p["date"].isoformat() if p["date"] else "??????????"
        print(f" {date_text}  {pct(p['save_rate'])}  {p['format']:<9} [{p['tag']}]")
        print(f"   {short(p['caption'])}")
        if p["permalink"]:
            print(f"   {p['permalink']}")
        print()


def print_declining(recent, older, min_posts=3, drop=0.25):
    """Warn about content types that used to work better than they do now."""
    warnings = []
    for name, recent_stats in recent.items():
        old_stats = older.get(name)
        if not old_stats:
            continue
        if recent_stats["count"] < min_posts or old_stats["count"] < min_posts:
            continue
        r_new = recent_stats["save_rate"]
        r_old = old_stats["save_rate"]
        if r_new is None or r_old is None or r_old <= 0:
            continue
        change = (r_new - r_old) / r_old
        if change <= -drop:
            warnings.append((name, r_old, r_new, change))

    print()
    line("=")
    print(" GETTING WEAKER  (last 30 days vs the full period)")
    line("=")
    if not warnings:
        print(" Nothing is clearly declining. Good.")
        return
    for name, old, new, change in warnings:
        print(f" {name:<18} {pct(old)} -> {pct(new)}   ({change * 100:+.0f}%)")


# ---------------------------------------------------------------------------
# 6. WRITING THE SNAPSHOT
# ---------------------------------------------------------------------------

def write_snapshot(sheet, all_posts, by_tag, by_format):
    """Save today's numbers so we build a history over time."""
    try:
        tab = sheet.worksheet(SNAPSHOT_TAB)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title=SNAPSHOT_TAB, rows=500, cols=10)
        tab.append_row([
            "run_date", "group_kind", "group_name", "posts",
            "save_rate", "share_rate", "follow_rate", "avg_reach",
        ])

    today = datetime.now(timezone.utc).date().isoformat()
    new_rows = []

    def add(kind, name, s):
        new_rows.append([
            today, kind, name, s["count"],
            "" if s["save_rate"] is None else round(s["save_rate"], 5),
            "" if s["share_rate"] is None else round(s["share_rate"], 5),
            "" if s["follow_rate"] is None else round(s["follow_rate"], 5),
            "" if s["median_reach"] is None else round(s["median_reach"], 0),
        ])

    add("all", "ALL_POSTS", summarise_group(all_posts))
    for name, s in by_tag.items():
        add("tag", name, s)
    for name, s in by_format.items():
        add("format", name, s)

    tab.append_rows(new_rows, value_input_option="RAW")
    print(f"\n Wrote {len(new_rows)} rows to the '{SNAPSHOT_TAB}' tab.")


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyse ChessMood Instagram posts.")
    parser.add_argument("--days", type=int, default=None,
                        help="Only look at the last X days. Default: everything.")
    parser.add_argument("--no-write", action="store_true",
                        help="Print results but do not write to the sheet.")
    args = parser.parse_args()

    print()
    line("=")
    print(" ChessMood social analysis  -  Layer 2")
    line("=")

    sheet = connect_to_sheet()
    print(f" Connected to: '{sheet.title}'")

    all_posts = load_posts(sheet)
    posts = filter_by_days(all_posts, args.days)

    if not posts:
        sys.exit(" No posts in that time window. Try a bigger --days number.")

    dates = [p["date"] for p in posts if p["date"]]
    if dates:
        print(f" Posts    : {len(posts)}")
        print(f" Period   : {min(dates)} to {max(dates)}")

    # How much of the data is actually usable?
    usable = len([p for p in posts if p["save_rate"] is not None])
    print(f" With save data: {usable} of {len(posts)}")
    if usable < len(posts):
        print("   (posts without reach or saves are skipped in the rates)")

    # --- the two main tables ---
    by_tag = group_by(posts, "tag")
    by_format = group_by(posts, "format")

    print_group_table(
        "BY CONTENT TYPE",
        by_tag,
        "guessed from the caption - edit tags_config.py to improve this",
    )
    print_group_table(
        "BY FORMAT",
        by_format,
        "this one is exact, it comes from Instagram itself",
    )

    # --- declining check ---
    recent = group_by(filter_by_days(all_posts, 30), "tag")
    print_declining(recent, group_by(all_posts, "tag"))

    # --- best and worst ---
    rated = [p for p in posts if p["save_rate"] is not None]
    rated.sort(key=lambda p: p["save_rate"], reverse=True)

    print_post_list("BEST 5 POSTS  (by save rate)", rated)
    print_post_list("WEAKEST 5 POSTS  (by save rate)", list(reversed(rated)))

    # --- overall ---
    overall = summarise_group(posts)
    print()
    line("=")
    print(" OVERALL AVERAGE")
    line("=")
    print(f" save rate      {pct(overall['save_rate'])}")
    print(f" share rate     {pct(overall['share_rate'])}")
    print(f" follow rate    {pct(overall['follow_rate'])}")
    print(f" engagement     {pct(overall['engagement_rate'])}")

    if not args.no_write:
        write_snapshot(sheet, posts, by_tag, by_format)

    print()
    line("=")
    print(" Done.")
    line("=")
    print()


if __name__ == "__main__":
    main()
