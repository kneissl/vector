"""Pure helpers for the top-N high-score trigger.

No hardware/MicroPython imports — safe to import on CPython for unit tests and
bundled into every platform build via src/common.
"""

DEFAULT_CUTOFF = 10
MAX_CUTOFF = 20  # leaders store capacity
SEED_BAND = 3  # filler slots seeded just above the threshold (4-slot table -> +1,+2,+3)


def is_rejected_blank(initials, claim_enabled):
    """True when an unclaimed (blank) score should be kept off the leaderboard.

    A score that reached the board without initials only earns a slot when web
    claiming is enabled (so a player can claim it later). With claiming off, a
    blank score is dropped rather than occupying a slot it can never fill.
    Real (non-blank) scores are always kept.
    """
    return not (initials or "").strip() and not claim_enabled


def pad_initials(initials):
    """Normalize initials to exactly 3 characters, right-padded with spaces.

    The machine high-score table has three fixed initial positions; a shorter
    string (a player who entered one or two letters and left the rest blank)
    must be space-padded, not rejected. Empty/None -> three spaces (the game
    dislikes truly empty slots); longer than 3 is truncated.
    """
    return ((initials or "") + "   ")[:3]


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
    entry = leaders[n - 1]
    if not entry:  # corrupt/blank record can deserialize to None
        return 0
    score = entry.get("score", 0)
    return score if score > 0 else 0


PLACEHOLDER_STEP = 100  # not-full board: tiny beatable placeholders (<1000, ignored at readback)


def build_seed_table(leaders, cutoff, slots=4):
    """Build the honest machine high-score seed table.

    Returns `slots` (initials, score) pairs in non-increasing score order:
    the real top leaderboard entries in the upper slots (so the machine's
    per-level awards fire for genuine placement), and below them either the
    top-N threshold score with blank initials in the lowest slot (gating
    initials entry on making the top N) with band fillers in between, or —
    while the board is not yet full (threshold 0) — tiny beatable
    placeholders so every score still qualifies for initials entry while
    landing honestly relative to the real scores. Corrupt (None) and
    zero-score records are skipped.
    """
    threshold = compute_threshold(leaders, cutoff)
    real = [e for e in leaders if e and e.get("score", 0) > 0]

    honest_count = min(slots - 1, cutoff - 1, len(real))
    table = [((e.get("initials") or ""), e.get("score", 0)) for e in real[:honest_count]]

    if threshold > 0:
        # filler phantoms between the honest slots and the threshold slot,
        # clamped so the table never increases downward (tied leader scores)
        for index in range(honest_count, slots - 1):
            score = threshold + (slots - 1 - index)
            if table and score > table[-1][1]:
                score = table[-1][1]
            table.append(("", score))
        table.append(("", threshold))
    else:
        # board not yet full: 500/400/300/200 style placeholders (4-slot table)
        for index in range(honest_count, slots):
            table.append(("", (slots + 1 - index) * PLACEHOLDER_STEP))
    return table


def matches_seeded_slot(initials, score, seeded):
    """True when a read-back machine slot is an untouched seeded entry.

    `seeded` is the (initials, score) list written at game start. Initials
    compare whitespace-insensitively (the machine pads to 3 chars; readback
    may strip); None initials read as blank. A matching slot is not a new
    player score and must not reach the leaderboard/claim/push paths.
    """
    key = (initials or "").strip()
    for seed_initials, seed_score in seeded:
        if score == seed_score and key == (seed_initials or "").strip():
            return True
    return False


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


def dedupe_leaderboard(entries, count):
    """Reduce leaderboard entries to one best score per initials.

    The dedup key is the entry's initials, stripped and upper-cased; all blank
    initials (empty/whitespace/missing) collapse into a single "" bucket. For
    each key the highest-scoring entry is kept. A surviving blank entry is then
    dropped if a non-blank entry shares its exact score: the blank is the
    unclaimed version of an already-claimed score (e.g. re-read from the machine
    high-score table every boot), not a distinct achievement. Returns up to
    `count` entries sorted by score descending. None entries are skipped (the
    leaders record deserializer can return None).
    """
    best = {}
    for entry in entries:
        if entry is None:
            continue
        key = (entry.get("initials") or "").strip().upper()
        if key not in best or entry.get("score", 0) > best[key].get("score", 0):
            best[key] = entry

    blank = best.get("")
    if blank is not None:
        nonblank_scores = {e.get("score", 0) for k, e in best.items() if k}
        if blank.get("score", 0) in nonblank_scores:
            del best[""]

    ordered = sorted(best.values(), key=lambda e: e.get("score", 0), reverse=True)
    return ordered[:count]
