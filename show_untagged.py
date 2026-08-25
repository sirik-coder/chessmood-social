"""
show_untagged.py
================

Writes the captions that tags_config.py could NOT classify into untagged.txt.

Why a file and not the screen: captions contain emoji and special dashes.
Windows writes files in an old encoding by default, which crashes on those
characters. So we open the file ourselves with encoding="utf-8".

Run:  python show_untagged.py
Then open untagged.txt
"""

from __future__ import annotations

import stats

MAX_CHARS = 220
OUT_FILE = "untagged.txt"


def main() -> None:
    client = stats.get_client()
    posts = stats.load_posts(client)

    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)

    if posts.empty:
        print("IG_Posts is empty. Run collect.py first.")
        return

    counts = posts["tag"].value_counts()
    say("=" * 70)
    say(" HOW POSTS ARE TAGGED NOW")
    say("=" * 70)
    for tag, n in counts.items():
        say(f"  {tag:<16} {n:>3}")
    say(f"  {'TOTAL':<16} {len(posts):>3}")

    other = posts[posts["tag"] == "other"]
    say()
    say("=" * 70)
    say(f" THE {len(other)} CAPTIONS THAT MATCHED NOTHING")
    say("=" * 70)

    for i, (_, row) in enumerate(other.iterrows(), start=1):
        caption = " ".join(str(row.get("caption", "")).split())
        if len(caption) > MAX_CHARS:
            caption = caption[:MAX_CHARS] + "..."
        fmt = row.get("format", "?")
        date = str(row.get("date", ""))[:10]
        try:
            saves = float(row.get("save_rate", 0))
        except Exception:
            saves = 0.0
        say()
        say(f"[{i:>2}] {date}  {fmt:<9}  save {saves:.2f}%")
        say(f"     {caption if caption else '(no caption)'}")

    with open(OUT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print(f"Wrote {len(other)} untagged captions to {OUT_FILE}")


if __name__ == "__main__":
    main()
