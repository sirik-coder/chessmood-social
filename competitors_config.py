"""
competitors_config.py
=====================

The list of Instagram accounts we watch for inspiration.

YOU CAN EDIT THIS FILE. Add or remove usernames any time.
You never need to touch inspire.py.

RULES
-----
- Use the username only, no @ sign, no link.
- The account must be a Business or Creator account.
  Personal accounts return nothing. The script will tell you which ones fail.
- Keep the list under about 20 names, so we stay inside Instagram's limits.

WHY TWO GROUPS
--------------
"chess" shows you what your market already does.
"teaching" shows you how other people teach hard things.

The second group is the useful one. Copying chess accounts makes ChessMood
look like everyone else. Borrowing the STRUCTURE of a good teaching post
from another subject is how you get something new.
"""

WATCH_LIST = [

    # --- Chess: your market ---
    ("wwwchesscom",       "chess"),      # FIXED: was "chesscom", which does not exist
    ("chessable",         "chess"),
    ("gothamchess",       "chess"),
    ("chessbaseindia",    "chess"),
    ("annacramling",      "chess"),      # 2nd try: "anna.cramling" and "annascamling" both failed

    # Daniel Naroditsky died in October 2025. The account will not post new
    # work, so it cannot show what is working now. Left here, switched off,
    # because removing it is Sirik's call and not the script's.
    # ("danielnaroditsky",  "chess"),

    # --- Teaching, other subjects: your inspiration ---
    ("duolingo",          "teaching"),   # makes daily practice feel like a game
    ("brilliantorg",      "teaching"),   # teaches maths and logic visually
    ("morningbrew",       "teaching"),   # makes dry information enjoyable
    ("thefuturishere",    "teaching"),   # FIXED: was "thefutur" - wrong handle
    ("aliabdaal",         "teaching"),   # FIXED: was "ali.abdaal" - no dot
    ("sketchplanations",  "teaching"),   # one idea, one picture
]

# How many recent posts to ask for from each account.
# Instagram limits this. 25 is a safe number.
POSTS_PER_ACCOUNT = 25

# A post is an "outlier" when it gets this many times more likes
# than that account's normal post. 2.0 means "twice as good as usual".
OUTLIER_THRESHOLD = 2.0
