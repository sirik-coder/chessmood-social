"""
tag_report.py
=============

Reads IG_Posts and writes tag_report.txt: how each content type performs
now that the new rules are in. Read-only - it changes nothing.
"""
from __future__ import annotations
import stats

OUT = "tag_report.txt"

def main() -> None:
    client = stats.get_client()
    posts = stats.load_posts(client)
    if posts.empty:
        print("IG_Posts is empty.")
        return

    lines: list[str] = []
    def say(t: str = "") -> None:
        lines.append(t)

    by_tag = stats.group_summary(posts, "tag")
    say("=" * 78)
    say(" BY CONTENT TYPE")
    say("=" * 78)
    say(by_tag.to_string(index=False))

    by_fmt = stats.group_summary(posts, "format")
    say()
    say("=" * 78)
    say(" BY FORMAT")
    say("=" * 78)
    say(by_fmt.to_string(index=False))

    say()
    say("=" * 78)
    say(" CAROUSELS ONLY, BY CONTENT TYPE  (fair comparison - same format)")
    say("=" * 78)
    car = posts[posts["format"] == "Carousel"]
    if not car.empty:
        say(stats.group_summary(car, "tag").to_string(index=False))
    else:
        say("no carousels found")

    freq = stats.posting_frequency(posts)
    say()
    say(f"Posting: {freq['posts']} posts in {freq['days']} days "
        f"= {freq['per_week']}/week, one every {freq['gap_days']} days")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    main()
