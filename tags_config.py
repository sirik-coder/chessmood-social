"""
tags_config.py
==============

These rules were written by reading REAL ChessMood captions.

YOU CAN EDIT THIS FILE. You never need to touch analyze.py.
If a post lands in the wrong group, add a better keyword here,
then run  python analyze.py  again.

HOW IT WORKS
------------
The script reads each caption in lowercase.
It checks the groups IN ORDER, from top to bottom.
The FIRST group that matches wins.
If nothing matches, the post is tagged "other".

So the MOST SPECIFIC groups must stay at the TOP.
"""

TAG_RULES = [

    # ------------------------------------------------------------------
    # 0. QUESTION - you ask the audience something, or ask them to act.
    #    ADDED after reading the untagged posts. These were the five best
    #    carousels in the whole account (4.45%, 3.91%, 3.86%, 3.36%, 3.21%
    #    save) and they had no tag at all. Placed FIRST because the pattern
    #    is stronger than the topic.
    # ------------------------------------------------------------------
    ("question", [
        "which book would you add",
        "are you more of",
        "what should i do next",
        "what should be my plan",
        "should we do more",
        "you know who needs this",
        "comment the word",
        "comment below",
        "tag someone",
        "send!",
        "which one",
        "agree?",
        "do you agree",
        "what do you think",
        "who else",
    ]),

    # ------------------------------------------------------------------
    # 0b. EVENT - tournaments, prize funds, partner competitions.
    # ------------------------------------------------------------------
    ("event", [
        "grand prix",
        "in prizes",
        "lichess",
        "grand final",
        "tournament",
        "giveaway",
        "20/20",
    ]),

    # ------------------------------------------------------------------
    # 0c. BLOG ARTICLE - pointing at written content off Instagram.
    # ------------------------------------------------------------------
    ("blog_article", [
        "latest article",
        "our blog",
        "on the blog",
        "read the article",
        "new article",
    ]),

    # ------------------------------------------------------------------
    # 1. PUZZLE - you ask the viewer to solve something
    # ------------------------------------------------------------------
    ("puzzle", [
        "6 puzzles",
        "puzzles. 6 lev",
        "can you solve",
        "can you find",
        "white to move",
        "black to move",
        "white to play",
        "black to play",
        "find the best move",
        "find the move",
        "mate in",
        "what would you play",
        "your move",
        "solve this",
        "puzzle",
        "back rank is on",
    ]),

    # ------------------------------------------------------------------
    # 2. COURSE PROMO - selling or announcing a course or episode
    #    Checked BEFORE gm_feature, because course posts usually name a GM.
    # ------------------------------------------------------------------
    ("course_promo", [
        "the course is live",
        "meet your new coach",
        "in this course",
        "inside our course",
        "new course",
        "course is available",
        "available for eve",
        "credits to the course",
        "you can watch the",
        "not in yet",
        "courseupdate",
        "course update",
        "enroll",
        "sign up",
        "link in bio",
        "chessmood.com",
        "discover the only",
        "new podcast",
        "new episode",
        "we've put together",
        "we have put together",
    ]),

    # ------------------------------------------------------------------
    # 3. STUDENT STORY - a real student's result or journey
    # ------------------------------------------------------------------
    ("student_story", [
        "our student",
        "our members",
        "our member",
        "success story",
        "testimonial",
        "his journey",
        "her journey",
        "went from",
        "improved from",
        "rating jump",
        "this story will ch",
        "if this relates to y",
    ]),

    # ------------------------------------------------------------------
    # 4. COMMUNITY - birthdays, travel, the dog, tributes, feelings
    # ------------------------------------------------------------------
    ("community", [
        "happy birthday",
        "say happy b",
        "arjuk",
        "#besttrip",
        "#travel",
        "a pure reminder",
        "our hearts are",
        "memories we",
        "collecting memo",
        "for danya",
        "a legend, a teac",
        "we never got to",
        "wishing you a",
        "we can save the",
        "do a kind thing",
        "because kindnes",
        # ADDED from the untagged posts:
        "danya",                  # the tribute reel said "Danya", not "for danya"
        "@gm_avetik",             # the handle, not the words "gm avetik"
        "narmadawalk",
        "grand slam",
        "this is why we love chess",
        "teaching em young",
        "joined the trend",
    ]),

    # ------------------------------------------------------------------
    # 5. GM FEATURE - spotlight on a Grandmaster, not selling a course
    # ------------------------------------------------------------------
    ("gm_feature", [
        "gm avetik",
        "gm marin",
        "gm nikola",
        "gm banusz",
        "gm blohberg",
        "gm bassem",
        "two-time",
        "peak elo",
        "he walks you thr",
        "one of hungary",
        "africa's greatest",
        "africas greatest",
        "6 lessons from",
        "imagine growing",
        "over a decad",
    ]),

    # ------------------------------------------------------------------
    # 6. MOTIVATION / MINDSET
    #    Checked BEFORE lesson_tip, because these words are more specific.
    # ------------------------------------------------------------------
    ("motivation", [
        "you are not lazy",
        "no fairy tales",
        "no gm hacks",
        "no magic short",
        "true strength",
        "consistency",
        "discipline",
        "mindset",
        "hard work",
        "keep going",
        "never give up",
        "more training",
        "if you want to be",
        "lots of players",
        "many players ge",
    ]),

    # ------------------------------------------------------------------
    # 7. LESSON / TIP - teaching something concrete
    # ------------------------------------------------------------------
    ("lesson_tip", [
        "mistake #",
        "mistake number",
        "swipe and learn",
        "swipe",
        "these are the ke",
        "here is the form",
        "here's the form",
        "the value of",
        "pawn chain",
        "keypawn",
        "key pawn",
        "skewer",
        "double attack",
        "back rank",
        "the biggest road",
        "not openings",
        "the key is",
        "how to",
        "you should",
        "always double",
        "endgame",
        "opening",
        "gambit",
        "tactic",
        "blunder",
        "checkmate",
        "the rook",
        "the bishop",
        "the queen",
        "the king",
        "principle",
        "lesson",
        "tip",
        # ADDED from the untagged posts:
        "forked",
        "7q",
        "method",
        "stay calm",
        "important factors",
    ]),
]

DEFAULT_TAG = "other"


# A caption this short cannot be classified by keywords - there are no words.
# Six real posts look like this: "\u2764\ufe0f", "\U0001f914", "Oops \U0001f440".
# One of them scored 3.86% save, so they matter - they just need their own
# label instead of hiding inside "other".
MIN_REAL_CAPTION_CHARS = 18


def _letters_only(text):
    """Count just the letters, so emoji and punctuation do not inflate length."""
    return sum(1 for ch in text if ch.isalpha())


def tag_caption(caption):
    """Return the tag for one caption. The first matching group wins."""
    if not caption:
        return "no_caption"

    text = str(caption).lower()

    # Decided BEFORE the keyword rules: too little text to judge.
    if _letters_only(text) < MIN_REAL_CAPTION_CHARS:
        return "no_caption"

    for tag_name, keywords in TAG_RULES:
        for word in keywords:
            if word in text:
                return tag_name

    return DEFAULT_TAG
