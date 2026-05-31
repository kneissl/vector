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

- Read the effective cutoff N from settings (`clamp_cutoff(extras.top_n_cutoff)`, §3).
- Compute the threshold from the `leaders` store via `compute_threshold(leaders, N)`.
- If the threshold is `> 0`, write that threshold value into **all four** machine high-score
  slots (the lowest slot is what the machine compares against; filling all four keeps the table
  validly descending with the lowest == threshold), so the machine's native logic only prompts
  players who **beat** it (= only top-N-worthy scores). Seeded-slot initials keep the existing
  blank/sentinel marker (`0x3F` common, `'A'` WPC). Store the threshold in the module global
  `_seed_threshold` for readback (§2).
- **Fallback (board not full):** `compute_threshold` returns `0` when there are fewer than N
  real entries in `leaders`. In that case **zero the slots exactly as today** (every score
  qualifies) and set `_seed_threshold = 0`.
- **WPC specifics:** keep grand-champion-to-max behavior and re-run `fix_high_score_checksum()`
  after writing the threshold BCD. Only the four ranked slots' score values change (zero →
  threshold).
- **Hardware risk (manual-verify):** some machines may require strictly descending high-score
  slots. Filling all four with the identical threshold assumes equal values are accepted. If a
  target machine rejects equal slots, fall back to seeding only the lowest slot at the threshold
  and the upper three at real leaderboard scores above it. Validated on-device (see Testing).

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

**Important constraint — do not break the existing "allow claim" feature.** `_read_machine_score`
deliberately keeps high-score slots that have a real score but blank initials (the
`if high_scores[idx][0] in ["???", "", None, "   "]: high_scores[idx][0] = ""` branch, commented
"no player, allow claim"). This is how a genuine top score whose player walked away without
typing initials becomes claimable later via the web UI. We must preserve it. A player who *beats*
the threshold and then skips initials entry leaves a slot with `score > threshold` and blank
initials — that must stay claimable.

So the phantom test is **both** conditions, not just blank initials: a slot is a leftover seed
(and gets zeroed) only when its initials are blank **and** its score equals the seeded threshold.
Unentered seeds always read back at exactly the threshold value; a real qualifying score is always
strictly greater than the threshold (the machine only inserts scores that *beat* the lowest slot),
so the two never collide.

Fix: track the seeded threshold in a module-level global `_seed_threshold` (set when
`_remove_machine_scores` seeds, `0` when it falls back to zeroing). In `_read_machine_score`, after
the existing blank-initials normalization, zero the score of any slot where
`is_phantom_slot(initials, score, _seed_threshold)` is true. When `_seed_threshold` is `0`
(fallback/zeroed table) the test is always false, so behavior is identical to today.

Decision logic extracted as a pure helper:

```
is_phantom_slot(initials, score, threshold) -> bool
    # True iff threshold > 0 and score == threshold and initials is blank ("" / "???" / "   ")
    # i.e. an untouched seeded slot. False for real (score > threshold) claimable blanks.
```

### 3. Configurable cutoff N — storage, API, UI

**Storage.** Reuse the unused `other` field in the `extras` record (a 32-bit int that is
round-tripped in `SPI_DataStore.py` but referenced nowhere else). Surface it as
`top_n_cutoff` in the `extras` read/write paths (both `src/common/SPI_DataStore.py` and
`src/wpc/SPI_DataStore.py`):

- Read: `top_n_cutoff = other`.
- Write: pack `clamp_cutoff(top_n_cutoff)` back into `other`.
- Default / effective value: **10**. The cutoff is always an active top-N threshold in
  `1…leaders_count (20)`; there is no separate "force every game" mode (the On-Machine toggle
  already governs whether on-machine claiming happens at all).
- **Backward compatibility:** existing units have `other == 0` (blanked storage). A stored `0`
  (or any out-of-range value) is interpreted as the default **10**. This means existing
  On-Machine units switch from "prompt every game" to "prompt for top 10" on upgrade — which is
  exactly the requested behavior, applied globally. Document this in release notes.
- Validation/normalization helper:

```
clamp_cutoff(value, leaders_count=20) -> int
    # returns value if 1 <= value <= leaders_count, else 10 (covers 0, negatives, > count)
```

Both the read path (deriving the effective cutoff) and the write path use `clamp_cutoff`, so a
legacy `0` consistently resolves to `10` everywhere.

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
| `src/common/score_threshold.py` | New pure module: `compute_threshold`, `is_phantom_slot`, `clamp_cutoff` |
| `dev/tests/test_score_threshold.py` | Unit tests for the pure helpers |

**Helper module placement (decided):** `dev/build.py` copies `src/common/*` into the build dir
first, then overlays the platform directory (`src/wpc`, etc.) on top, flattening everything into
one directory. So a new pure module `src/common/score_threshold.py` is bundled into *every*
platform build and can be `import`ed by both `src/common/ScoreTrack.py` and
`src/wpc/ScoreTrack.py` (which is overlaid on top but lands in the same flat build dir). The
module imports only Python stdlib — no `machine`/`Shadow_Ram_Definitions` — so it is importable
on CPython for unit tests.

## Testing

ScoreTrack imports MicroPython/hardware modules and has no existing unit-test harness; existing
tests are CPython build/config validators. Strategy:

**Unit tests (CPython, `dev/tests/test_score_threshold.py`):**
- `compute_threshold(leaders, n)`: full board, partial board (fewer than N real entries),
  N=1, N greater than count, all-zero board, board with trailing zero scores.
- `is_phantom_slot(initials, score, threshold)`: untouched seed (blank + score==threshold),
  real claimable blank (blank + score>threshold → not phantom), real player (initials + any
  score → not phantom), threshold==0 (never phantom).
- `clamp_cutoff(value)`: 0 → 10; negative → 10; 1 → 1; 20 → 20; 21 → 10; default arg.

**Manual on-device verification (cannot be unit-tested without hardware):**
1. On-Machine on, cutoff 10, board with 20 entries: a score below the 10th-place value does
   **not** prompt for initials; a score above it **does**, and the entry appears in the leaderboard.
2. Board with fewer than 10 entries: every score still prompts (fallback path).
3. Player beats threshold but skips initials entry: score is still claimable via web UI (the
   "allow claim" feature is preserved — §2).
4. After a game, the claimable-scores list contains only real entered/qualifying scores — no
   phantom threshold-valued entries.
5. WPC: grand champion and high-score checksum remain valid after seeding (machine boots and
   shows correct high scores in attract mode).
6. Equal-slot acceptance: confirm the machine accepts four equal threshold slots and still
   prompts correctly (see Hardware risk in §1).

## Edge cases

- **Board not full / fewer than N real entries:** `compute_threshold` returns 0 → fall back to
  zeroing (every score qualifies).
- **Legacy storage (`other == 0`) or out-of-range cutoff:** `clamp_cutoff` resolves to the
  default 10. Existing On-Machine units move to top-10 behavior on upgrade.
- **Cutoff > leaders count or < 1:** normalized to 10 by `clamp_cutoff`.
- **Player beats then skips initials:** slot has `score > threshold` + blank initials → NOT a
  phantom; stays claimable.
- **Player scores exactly the threshold:** does not beat the lowest slot, so the machine does
  not prompt — matches native qualification semantics.
- **Fewer qualifying players than slots:** leftover seeded slots read back at exactly the
  threshold with blank initials → zeroed by the readback filter (§2).
