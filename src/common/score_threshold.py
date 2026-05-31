"""Pure helpers for the top-N high-score trigger.

No hardware/MicroPython imports — safe to import on CPython for unit tests and
bundled into every platform build via src/common.
"""

DEFAULT_CUTOFF = 10
MAX_CUTOFF = 20  # leaders store capacity
SEED_BAND = 3  # filler slots seeded just above the threshold (4-slot table -> +1,+2,+3)


def clamp_cutoff(value, leaders_count=MAX_CUTOFF):
    """Normalize a stored cutoff to a valid top-N value.

    Any value outside 1..leaders_count (including 0 / legacy storage and
    out-of-range values) resolves to DEFAULT_CUTOFF.
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CUTOFF
    if 1 <= value <= leaders_count:
        return value
    return DEFAULT_CUTOFF


def compute_threshold(leaders, n):
    """Return the score at rank n (the top-N qualifying threshold).

    `leaders` is the leaderboard list (dicts with a "score" key) in descending
    order. Returns 0 when the board has fewer than n real (score > 0) entries,
    signalling the caller to zero the machine table so every score qualifies.
    """
    if n < 1 or n > len(leaders):
        return 0
    score = leaders[n - 1].get("score", 0)
    return score if score > 0 else 0


def is_phantom_slot(initials, score, threshold):
    """True when a machine high-score slot is an untouched seeded slot.

    Seeded slots occupy the band [threshold, threshold + SEED_BAND] (the lowest
    slot at the threshold plus filler slots just above it) and carry blank
    initials. A real qualifying score is orders of magnitude larger than the
    threshold, so it never lands in the band — preserving the "allow claim"
    feature for blank real scores far above the threshold.
    """
    if threshold <= 0:
        return False
    if not (threshold <= score <= threshold + SEED_BAND):
        return False
    if initials is None:
        return True
    return initials.strip() == "" or initials == "???"
