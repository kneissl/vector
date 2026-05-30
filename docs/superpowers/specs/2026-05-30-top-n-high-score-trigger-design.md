# Top-N High-Score Trigger — Design

**Date:** 2026-05-30
**Status:** Approved (pending spec review)
**Scope:** `src/common` (System 9/11) and `src/wpc` (Williams WPC)

## Problem

When the **On-Machine** score-claim method (`enter_initials_on_game`) is enabled, vector
forces the physical machine to prompt for player initials on **every game**, regardless of
how good the score is. It does this by zeroing the machine's native high-score table at game
start (`_remove_machine_scores()`), so any score beats the table and the machine runs its own
initials-entry sequence.

We want the machine to prompt for initials **only when the player's score would land in the
top N of vector's stored leaderboard** (N configurable, default 10).

## Goal

Replace the "zero the table" trigger with a "seed the Nth-place score as a threshold" trigger,
so the machine's own high-score logic only fires for top-N-worthy scores. Make N configurable
via the admin UI.

## Non-Goals

- No change to the `em` or `data_east` platforms in this work.
- No change to the web-UI claim path (`claim_scores`) behavior.
- No new attract-mode display logic; restored end-of-game scores are unchanged.

## Background — current flow

In `src/common/ScoreTrack.py` (`src/wpc/ScoreTrack.py` is analogous):

- `CheckForNewScores()` runs every 5 s as a state machine.
- Game start (`nState 1 → 2`): calls `_remove_machine_scores()`, which **zeros** the machine's
  4 high-score slots (and, on WPC, sets grand champion to max + fixes the checksum). Seeded
  initials use a blank/sentinel marker (`0x3F` common, `'A'` WPC).
- Game end (`nState 2 → 1`): `_read_machine_score(True)` reads the 4 slots (now containing the
  game's players' scores + freshly typed initials), each is passed to `update_leaderboard()`,
  the game is pushed to the claimable-scores list, and `place_machine_scores()` restores the
  real top-4 scores into machine memory.
- The leaderboard (`leaders` store) holds 20 entries; size is `DataStore.memory_map["leaders"]["count"]`.

`_read_machine_score()` already zeroes any slot scoring `< 1000`, and `update_leaderboard()`
already rejects blank/sentinel initials (`"@@@"`, `"   "`, `"???"`).

## Design

### 1. Core mechanism — seed threshold instead of zeroing

When `enter_initials_on_game` is ON, at game start seed the machine's high-score slot(s) with
the **Nth-place leaderboard score** as the threshold rather than zeroing:

- Compute the threshold from the `leaders` store: the score at rank N (`read_record("leaders", N-1)`).
- Write that threshold into the machine high-score slot(s) so the machine's native logic only
  prompts players who **beat** it (= only top-N-worthy scores).
- Seeded-slot initials keep the existing blank/sentinel marker (`0x3F` common, `'A'` WPC), so
  slots a player did not beat remain recognizable as "no player."
- **Fallback (board not full):** if there are fewer than N real entries in `leaders`, or the
  Nth score is `0`, **zero the slots exactly as today** — the board isn't full, so every score
  qualifies. Same fallback when the cutoff is "off" (see §3).
- **WPC specifics:** keep grand-champion-to-max behavior and the trailing
  `fix_high_score_checksum()` call. Only the four ranked slots' score values change (zero →
  threshold).

Decision logic is extracted into a pure helper for testing:

```
compute_threshold(leaders, n) -> int
    # leaders: list of {"score": int, ...} read from the store (len == leaders count)
    # returns leaders[n-1]["score"] if the board has >= n real (score > 0) entries,
    # else 0  (caller interprets 0 as "zero the table / force every game")
```

### 2. Clean readback — no phantom claimable scores

With threshold seeding, a slot a player did **not** beat now holds a **non-zero** score (the
threshold) but still carries the sentinel/blank initials. Today's readback only zeroes slots
scoring `< 1000`, so these phantom slots could otherwise leak into the leaderboard and the
claimable-scores list (letting someone claim a score they never earned).

Fix: in `_read_machine_score`, treat **any slot whose initials are still the blank/sentinel
marker as no-player (score → 0)**, regardless of score value. This generalizes the existing
`< 1000` rule. Unentered seeded slots then collapse to 0 exactly like today's zeroed slots:

- `update_leaderboard()` ignores them (existing blank-initials rejection).
- `_place_game_in_claim_list()` does not offer phantom claims.

Real qualifying players overwrote the sentinel with typed initials and pass through untouched.

Decision logic extracted as:

```
is_phantom_slot(initials) -> bool   # True if initials is the platform blank/sentinel marker
```

### 3. Configurable cutoff N — storage, API, UI

**Storage.** Reuse the unused `other` field in the `extras` record (a 32-bit int that is
round-tripped in `SPI_DataStore.py` but referenced nowhere else). Surface it as
`top_n_cutoff` in the `extras` read/write paths (both `src/common/SPI_DataStore.py` and
`src/wpc/SPI_DataStore.py`):

- Read: `top_n_cutoff = other`.
- Write: pack `top_n_cutoff` back into `other`.
- Default: **10**.
- Semantics: `0` (including legacy units where `other` has always been 0) = **off** → force
  every game (today's behavior). `1…20` = top-N threshold.
- Validation/clamp on write via:

```
clamp_cutoff(value) -> int   # 0 stays 0 (off); otherwise clamp to 1..leaders_count (20)
```

**API.** Extend the existing settings endpoints in `src/common/backend.py`:

- `GET /api/settings/get_claim_methods` → add `top-n-cutoff` to the response body.
- `POST /api/settings/set_claim_methods` → accept and persist `top-n-cutoff` (clamped).

**UI.** In `src/common/web/html/admin.html`, Score Claim Methods section, add a number input
beside the existing On-Machine toggle, label e.g. "Only collect initials for top __ scores",
wired through the existing settings save flow in `src/common/web/js/admin.js`. The input is
**always visible but disabled** when the On-Machine toggle is off (clearer that the option
exists than hiding it).

## Components touched

| File | Change |
|------|--------|
| `src/common/ScoreTrack.py` | Seed threshold in `_remove_machine_scores`; sentinel filter in `_read_machine_score`; use pure helpers |
| `src/wpc/ScoreTrack.py` | Same as above, preserving grand-champ + checksum behavior |
| `src/common/SPI_DataStore.py` | `top_n_cutoff` ↔ `other` in extras read/write; default 10 |
| `src/wpc/SPI_DataStore.py` | Same extras change |
| `src/common/backend.py` | `top-n-cutoff` in get/set claim-methods endpoints |
| `src/common/web/html/admin.html` | Number input in Score Claim Methods |
| `src/common/web/js/admin.js` | Plumb input through settings load/save |
| `dev/tests/` | Unit tests for the pure helpers |

A pure-helper module shared by both platforms (or duplicated per platform, matching the repo's
existing per-platform duplication) will be decided during planning; the helpers must be
importable on CPython without hardware modules.

## Testing

ScoreTrack imports MicroPython/hardware modules and has no existing unit-test harness; existing
tests are CPython build/config validators. Strategy:

**Unit tests (CPython, `dev/tests/`):**
- `compute_threshold(leaders, n)`: full board, partial board (fewer than N real entries),
  N=1, N greater than count, ties at the cutoff, all-zero board.
- `is_phantom_slot(initials)`: each platform's blank/sentinel marker, real initials, empty.
- `clamp_cutoff(value)`: 0 stays 0; negative; 1; 20; above 20.

**Manual on-device verification (in spec checklist — cannot be unit-tested without hardware):**
1. With On-Machine on and cutoff 10 on a board with 20 entries: a score below the 10th-place
   value does **not** prompt for initials; a score above it **does**, and the entry appears in
   the leaderboard.
2. Board with fewer than 10 entries: every score still prompts (fallback).
3. Cutoff set to 0 / off: behaves like today (every game prompts).
4. After a game, the claimable-scores list contains only real entered scores — no phantom
   threshold-valued entries.
5. WPC: grand champion and high-score checksum remain valid after seeding.

## Edge cases

- **Board not full / Nth score is 0:** fall back to zeroing (force every game).
- **Cutoff off (0) or legacy storage:** force every game.
- **Cutoff > leaders count:** clamped to 20 on write.
- **Player scores exactly the threshold:** "beats the table" is per the machine's own
  comparison; this matches the machine's native qualification semantics and is acceptable.
- **Fewer qualifying players than slots:** leftover seeded slots carry sentinel initials and
  are dropped by the readback filter (§2).
