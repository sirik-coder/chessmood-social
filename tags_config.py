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
    ]),
]

DEFAULT_TAG = "other"


def tag_caption(caption):
    """Return the tag for one caption. The first matching group wins."""
    if not caption:
        return DEFAULT_TAG

    text = str(caption).lower()

    for tag_name, keywords in TAG_RULES:
        for word in keywords:
            if word in text:
                return tag_name

    return DEFAULT_TAG
