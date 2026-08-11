"""
collect.py
==========

THE MAIN SCRIPT. This is the one you actually run:

    python collect.py

What it does, in order:
  1. Reads your settings (config.py).
  2. Connects to Meta and to your Google Sheet.
  3. Fetches the last 90 days of Instagram posts + their insights.
  4. Fetches the last 90 days of Facebook Page posts + their insights.
  5. Fetches day-by-day Instagram account stats (followers, reach, profile views).
  6. Upserts all of it into the IG_Posts / FB_Posts / DailyStats tabs.
  7. Prints a summary: how many fetched, new, updated, and which metrics Meta
     refused to give us.

Useful flags while you are getting set up:
    python collect.py --limit 5          only 5 posts per platform (fast test)
    python collect.py --dry-run          fetch everything, write nothing
    python collect.py --days 7           just the last week
    python collect.py --skip-fb          Instagram only
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from config import ConfigError
from meta_client import (
    MetaClient,
    MetaError,
    MetaPermanentError,
    MetaTokenError,
    parse_meta_time,
)
from sheets_client import SheetsClient, SheetsError, UpsertResult

# Exit codes - GitHub Actions uses these to decide if the run failed.
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_TOKEN = 2
EXIT_META = 3
EXIT_SHEETS = 4


# ===========================================================================
# Keeping track of metrics Meta refused
# ===========================================================================

class RefusalTracker:
    """
    Collects every "Meta said no to this metric" event so we can report them once
    at the end, instead of spamming one line per post.
    """

    def __init__(self) -> None:
        # {"impressions": {"count": 128, "example": "(#100) metric[0] must be ..."}}
        self.records: dict[str, dict[str, Any]] = {}

    def add(self, refusals: dict[str, str]) -> None:
        for metric, reason in refusals.items():
            entry = self.records.setdefault(metric, {"count": 0, "example": reason})
            entry["count"] += 1

    def report(self) -> list[str]:
        """Build the human-readable block printed in the final summary."""
        if not self.records:
            return ["Metrics Meta refused    : none - every metric worked."]

        lines = ["Metrics Meta refused:"]
        for metric in sorted(self.records, key=lambda m: -self.records[m]["count"]):
            entry = self.records[metric]
            lines.append(f"  - {metric}  (refused on {entry['count']} request(s))")
            lines.append(f"      Meta said: {str(entry['example'])[:160]}")
            note = config.METRIC_NOTES.get(metric)
            if note:
                lines.append(f"      What I know: {note}")
        lines.append("")
        lines.append("  Blank cells in the sheet for these metrics are expected, not a")
        lines.append("  crash. To stop asking for a dead metric, remove it from the")
        lines.append("  relevant list in config.py.")
        return lines


# ===========================================================================
# Tiny formatting helpers
# ===========================================================================

def blank_if_none(value: Any) -> Any:
    """Google Sheets should show an empty cell, not the word 'None'."""
    return "" if value is None else value


def date_only(raw: str | None) -> str:
    """'2026-08-11T14:03:22+0000' -> '2026-08-11' (empty string if unparseable)."""
    parsed = parse_meta_time(raw)
    return parsed.date().isoformat() if parsed else ""


def nested_count(container: Any, *path: str) -> Any:
    """
    Safely dig into Meta's nested reply shapes.

    nested_count(post, "comments", "summary", "total_count")
    returns None instead of exploding if any level is missing.
    """
    current = container
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def progress(done: int, total: int, label: str) -> None:
    """Print a one-line progress note every 10 items so long runs feel alive."""
    if done == total or done % 10 == 0:
        print(f"   ...{label}: {done}/{total}", flush=True)


# ===========================================================================
# Turning Meta's replies into sheet rows
# ===========================================================================

def build_ig_row(post: dict, insights: dict[str, Any], run_stamp: str) -> dict[str, Any]:
    """One Instagram post -> one dictionary shaped like the IG_Posts columns."""
    row: dict[str, Any] = {
        "id": post.get("id", ""),
        "timestamp": post.get("timestamp", ""),
        "date": date_only(post.get("timestamp")),
        "media_type": post.get("media_type", ""),
        "media_product_type": post.get("media_product_type", ""),
        "permalink": post.get("permalink", ""),
        "caption": post.get("caption") or "",
        "like_count": blank_if_none(post.get("like_count")),
        "comments_count": blank_if_none(post.get("comments_count")),
        config.TIMESTAMP_COLUMN: run_stamp,
    }

    # Insight metric names match the column names one-for-one on Instagram, so
    # we can copy them across by name. Anything Meta refused stays blank.
    for metric in config.IG_MEDIA_METRICS:
        if metric in config.IG_POSTS_HEADERS:
            row[metric] = blank_if_none(insights.get(metric))

    return row


def build_fb_row(post: dict, insights: dict[str, Any], run_stamp: str) -> dict[str, Any]:
    """One Facebook post -> one dictionary shaped like the FB_Posts columns."""
    # Facebook's metric names are longer than our column names, so translate.
    reach = insights.get(config.FB_POST_INSIGHT_METRICS["reach"])
    impressions = insights.get(config.FB_POST_INSIGHT_METRICS["impressions"])
    clicks = insights.get(config.FB_POST_INSIGHT_METRICS["clicks"])
    reactions = insights.get(config.FB_POST_INSIGHT_METRICS["reactions"])

    # Reactions/comments/shares are also available as plain counts on the post
    # itself. Those are more reliable than insights, so we use them as a fallback
    # (reactions) or as the only source (comments, shares).
    if reactions is None:
        reactions = nested_count(post, "reactions", "summary", "total_count")

    comments = nested_count(post, "comments", "summary", "total_count")
    shares = nested_count(post, "shares", "count")

    return {
        "id": post.get("id", ""),
        "created_time": post.get("created_time", ""),
        "date": date_only(post.get("created_time")),
        "status_type": post.get("status_type", ""),
        "permalink_url": post.get("permalink_url", ""),
        "message": post.get("message") or "",
        "reach": blank_if_none(reach),
        "impressions": blank_if_none(impressions),
        "reactions": blank_if_none(reactions),
        "comments": blank_if_none(comments),
        "shares": blank_if_none(shares),
        "clicks": blank_if_none(clicks),
        config.TIMESTAMP_COLUMN: run_stamp,
    }


def build_daily_rows(
    daily: dict[str, dict[str, Any]], run_stamp: str
) -> list[dict[str, Any]]:
    """The account-stats dictionary -> one row per calendar day, oldest first."""
    rows = []
    for day in sorted(daily):
        stats = daily[day]
        row: dict[str, Any] = {"date": day, config.TIMESTAMP_COLUMN: run_stamp}
        for metric in config.IG_ACCOUNT_METRICS:
            if metric in config.DAILY_STATS_HEADERS:
                row[metric] = blank_if_none(stats.get(metric))
        rows.append(row)
    return rows


# ===========================================================================
# The three collection steps
# ===========================================================================

def collect_instagram_posts(
    meta: MetaClient,
    ig_id: str,
    since_ts: int,
    run_stamp: str,
    refusals: RefusalTracker,
    limit: int | None,
) -> list[dict[str, Any]]:
    """Step 1 + 2: fetch IG posts, then fetch each post's insights."""
    print("\n[1/3] Instagram posts")
    print("   Fetching the post list (this pages through all results)...")

    posts = meta.get_ig_media(ig_id, since_ts, limit=limit)
    print(f"   Found {len(posts)} Instagram post(s) in the window.")

    rows: list[dict[str, Any]] = []
    for index, post in enumerate(posts, start=1):
        post_id = post.get("id")
        insights: dict[str, Any] = {}

        if post_id:
            try:
                items, refused = meta.fetch_insights(post_id, config.IG_MEDIA_METRICS)
                insights = meta.flatten_insights(items)
                refusals.add(refused)
            except MetaPermanentError as exc:
                # Meta refused insights for this post entirely - common for very
                # old posts, stories, or ads. Keep the post, lose the metrics.
                print(f"   [skip insights] post {post_id}: {exc}")

        rows.append(build_ig_row(post, insights, run_stamp))
        progress(index, len(posts), "insights")

    return rows


def collect_facebook_posts(
    meta: MetaClient,
    page_id: str,
    since_ts: int,
    run_stamp: str,
    refusals: RefusalTracker,
    limit: int | None,
) -> list[dict[str, Any]]:
    """Step 3: fetch Facebook Page posts and their insights."""
    print("\n[2/3] Facebook Page posts")

    # Page insights normally need a Page token rather than a user token.
    page_token = meta.get_page_access_token(page_id)

    print("   Fetching the post list...")
    posts = meta.get_fb_posts(page_id, since_ts, token=page_token, limit=limit)
    print(f"   Found {len(posts)} Facebook post(s) in the window.")

    metric_names = list(config.FB_POST_INSIGHT_METRICS.values())

    rows: list[dict[str, Any]] = []
    for index, post in enumerate(posts, start=1):
        post_id = post.get("id")
        insights: dict[str, Any] = {}

        if post_id:
            try:
                items, refused = meta.fetch_insights(
                    post_id, metric_names, token=page_token
                )
                insights = meta.flatten_insights(items)
                refusals.add(refused)
            except MetaPermanentError as exc:
                print(f"   [skip insights] post {post_id}: {exc}")

        rows.append(build_fb_row(post, insights, run_stamp))
        progress(index, len(posts), "insights")

    return rows


def collect_daily_stats(
    meta: MetaClient,
    ig_id: str,
    since_ts: int,
    until_ts: int,
    run_stamp: str,
    refusals: RefusalTracker,
) -> list[dict[str, Any]]:
    """Step 4: fetch account-level daily Instagram insights."""
    print("\n[3/3] Instagram daily account stats")
    print("   Fetching in 29-day chunks (Instagram's maximum window per call)...")

    daily, refused = meta.get_ig_account_daily(
        ig_id, config.IG_ACCOUNT_METRICS, since_ts, until_ts
    )
    refusals.add(refused)

    rows = build_daily_rows(daily, run_stamp)
    print(f"   Got data for {len(rows)} day(s).")
    return rows


# ===========================================================================
# Command line handling
# ===========================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull Instagram + Facebook performance data into a Google Sheet.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=config.LOOKBACK_DAYS,
        help="How many days of history to collect.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process this many posts per platform. Great for a first test.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from Meta but do not write anything to the Google Sheet.",
    )
    parser.add_argument("--skip-ig", action="store_true", help="Skip Instagram posts.")
    parser.add_argument("--skip-fb", action="store_true", help="Skip Facebook posts.")
    parser.add_argument("--skip-daily", action="store_true", help="Skip DailyStats.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide the per-request retry / rate-limit chatter.",
    )
    return parser.parse_args()


# ===========================================================================
# main
# ===========================================================================

def run(args: argparse.Namespace) -> int:
    verbose = not args.quiet

    # ---- settings --------------------------------------------------------
    settings = config.get_settings()

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    since_ts = int(since.timestamp())
    until_ts = int(now.timestamp())
    run_stamp = now.isoformat(timespec="seconds")

    print("=" * 64)
    print(" ChessMood social analytics collector")
    print("=" * 64)
    print(f" Graph API version : {config.GRAPH_API_VERSION}")
    print(f" Window            : {since.date()} to {now.date()} ({args.days} days)")
    print(f" Google Sheet      : {settings.google_sheet_id}")
    print(f" Service account   : {settings.service_account_email}")
    if args.dry_run:
        print(" MODE              : DRY RUN - nothing will be written")
    print("=" * 64)

    # ---- clients ---------------------------------------------------------
    meta = MetaClient(settings.meta_access_token, verbose=verbose)
    sheets = SheetsClient(
        settings.google_service_account_info,
        settings.google_sheet_id,
        verbose=verbose,
    )

    refusals = RefusalTracker()
    results: list[UpsertResult] = []
    fetched: dict[str, int] = {}

    # ---- Instagram posts -------------------------------------------------
    if args.skip_ig:
        print("\n[1/3] Instagram posts - skipped (--skip-ig)")
    elif not settings.ig_business_account_id:
        print("\n[1/3] Instagram posts - skipped: IG_BUSINESS_ACCOUNT_ID is not set.")
    else:
        ig_rows = collect_instagram_posts(
            meta, settings.ig_business_account_id, since_ts,
            run_stamp, refusals, args.limit,
        )
        fetched["Instagram posts"] = len(ig_rows)
        print("   Writing to the IG_Posts tab...")
        results.append(
            sheets.upsert(
                tab=config.IG_POSTS_TAB,
                headers=config.IG_POSTS_HEADERS,
                rows=ig_rows,
                key_column="id",
                dry_run=args.dry_run,
            )
        )

    # ---- Facebook posts --------------------------------------------------
    if args.skip_fb:
        print("\n[2/3] Facebook Page posts - skipped (--skip-fb)")
    elif not settings.fb_page_id:
        print("\n[2/3] Facebook Page posts - skipped: FB_PAGE_ID is not set.")
    else:
        fb_rows = collect_facebook_posts(
            meta, settings.fb_page_id, since_ts, run_stamp, refusals, args.limit,
        )
        fetched["Facebook posts"] = len(fb_rows)
        print("   Writing to the FB_Posts tab...")
        results.append(
            sheets.upsert(
                tab=config.FB_POSTS_TAB,
                headers=config.FB_POSTS_HEADERS,
                rows=fb_rows,
                key_column="id",
                dry_run=args.dry_run,
            )
        )

    # ---- Daily account stats --------------------------------------------
    if args.skip_daily:
        print("\n[3/3] Instagram daily account stats - skipped (--skip-daily)")
    elif not settings.ig_business_account_id:
        print("\n[3/3] Daily stats - skipped: IG_BUSINESS_ACCOUNT_ID is not set.")
    else:
        daily_rows = collect_daily_stats(
            meta, settings.ig_business_account_id, since_ts, until_ts,
            run_stamp, refusals,
        )
        fetched["Daily stat days"] = len(daily_rows)
        print("   Writing to the DailyStats tab...")
        results.append(
            sheets.upsert(
                tab=config.DAILY_STATS_TAB,
                headers=config.DAILY_STATS_HEADERS,
                rows=daily_rows,
                key_column="date",
                dry_run=args.dry_run,
            )
        )

    # ---- the summary -----------------------------------------------------
    print()
    print("=" * 64)
    print(" SUMMARY" + ("  (DRY RUN - nothing was written)" if args.dry_run else ""))
    print("=" * 64)

    for label, count in fetched.items():
        print(f" {label:<22}: {count} fetched from Meta")

    if fetched:
        print()

    for result in results:
        print(f" Tab {result.tab}:")
        print(f"   new rows            : {result.new}")
        print(f"   updated rows        : {result.updated}")
        print(f"   unchanged (skipped) : {result.unchanged}")
        if result.columns_added:
            print(f"   columns added       : {', '.join(result.columns_added)}")
        if result.skipped_no_key:
            print(f"   dropped (no id)     : {result.skipped_no_key}")

    print()
    for line in refusals.report():
        print(f" {line}")

    print()
    print(f" Meta API requests      : {meta.request_count} "
          f"({meta.retry_count} retried)")
    print("=" * 64)
    print(" Done.")
    return EXIT_OK


def main() -> int:
    args = parse_args()
    try:
        return run(args)

    except ConfigError as exc:
        print("\n--- SETUP PROBLEM ---------------------------------------")
        print(exc)
        print("---------------------------------------------------------")
        return EXIT_CONFIG

    except MetaTokenError as exc:
        # The clear, friendly token message lives in meta_client._token_help.
        print(exc)
        return EXIT_TOKEN

    except SheetsError as exc:
        print("\n--- GOOGLE SHEETS PROBLEM -------------------------------")
        print(exc)
        print("---------------------------------------------------------")
        return EXIT_SHEETS

    except MetaError as exc:
        print("\n--- META API PROBLEM ------------------------------------")
        print(exc)
        print("\nIf this mentions a metric or an endpoint, check config.py -")
        print("Meta renames these periodically and every name lives in that file.")
        print("---------------------------------------------------------")
        return EXIT_META

    except KeyboardInterrupt:
        print("\nStopped by you (Ctrl+C). Anything already written is safe -")
        print("re-running simply updates the same rows again.")
        return EXIT_OK

    except Exception:  # genuinely unexpected - show the trace, it is a real bug
        print("\n--- UNEXPECTED ERROR ------------------------------------")
        traceback.print_exc()
        print("---------------------------------------------------------")
        return EXIT_META


if __name__ == "__main__":
    sys.exit(main())
