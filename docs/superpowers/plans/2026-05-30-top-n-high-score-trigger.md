# Top-N High-Score Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Only force on-machine initials entry when a score would land in the top N of the stored leaderboard (N configurable, default 10), instead of forcing it on every game.

**Architecture:** A new pure helper module (`src/common/score_threshold.py`) computes the top-N threshold, normalizes the configurable cutoff, and detects leftover seeded slots. `ScoreTrack.py` (common + wpc) seeds the machine high-score table with that threshold at game start instead of zeroing it, and filters phantom seed slots on readback. The cutoff is persisted in the unused `extras.other` field and exposed through the existing claim-methods settings API and admin UI.

**Tech Stack:** MicroPython (RP2350) for on-device code; CPython + pytest for unit tests; vanilla JS/HTML for the admin UI. Build (`dev/build.py`) copies `src/common/*` then overlays the platform dir, flattening into one import namespace.

**Reference spec:** `docs/superpowers/specs/2026-05-30-top-n-high-score-trigger-design.md`

---

## File structure

- **Create** `src/common/score_threshold.py` — pure helpers: `clamp_cutoff`, `compute_threshold`, `is_phantom_slot`. Stdlib only.
- **Create** `dev/tests/test_score_threshold.py` — unit tests for the helpers.
- **Modify** `src/common/SPI_DataStore.py` — `top_n_cutoff` in extras read/write.
- **Modify** `src/wpc/SPI_DataStore.py` — same extras change.
- **Modify** `src/common/ScoreTrack.py` — seed threshold + phantom filter + `_seed_threshold` global.
- **Modify** `src/wpc/ScoreTrack.py` — same for WPC, preserving grand-champ + checksum.
- **Modify** `src/common/backend.py` — `top-n-cutoff` in get/set claim methods.
- **Modify** `src/common/web/html/admin.html` — number input in Score Claim Methods.
- **Modify** `src/common/web/js/admin.js` — plumb the input through load/save.

The on-device modules import hardware (`machine`, `Shadow_Ram_Definitions`) and cannot be imported on CPython, so only the pure helper module is unit-tested. The integration edits are syntax-checked with `python -m py_compile` and verified on-device per the spec's manual checklist (captured in Task 10).

---

### Task 1: Pure helper module (TDD)

**Files:**
- Create: `src/common/score_threshold.py`
- Test: `dev/tests/test_score_threshold.py`

- [ ] **Step 1: Write the failing test**

Create `dev/tests/test_score_threshold.py`:

```python
import sys
from pathlib import Path

SRC_COMMON = Path(__file__).resolve().parents[2] / "src" / "common"
sys.path.insert(0, str(SRC_COMMON))

import score_threshold as st


def board(*scores):
    return [{"score": s} for s in scores]


# --- clamp_cutoff ---
def test_clamp_cutoff_zero_to_default():
    assert st.clamp_cutoff(0) == 10


def test_clamp_cutoff_negative_to_default():
    assert st.clamp_cutoff(-5) == 10


def test_clamp_cutoff_in_range():
    assert st.clamp_cutoff(1) == 1
    assert st.clamp_cutoff(20) == 20


def test_clamp_cutoff_above_max_to_default():
    assert st.clamp_cutoff(21) == 10


def test_clamp_cutoff_non_int():
    assert st.clamp_cutoff(None) == 10


# --- compute_threshold ---
def test_compute_threshold_full_board():
    b = board(100, 90, 80, 70, 60, 50, 40, 30, 20, 10)
    assert st.compute_threshold(b, 10) == 10


def test_compute_threshold_n1():
    assert st.compute_threshold(board(100, 50), 1) == 100


def test_compute_threshold_partial_board_returns_zero():
    b = board(100, 90, 80, 0, 0, 0, 0, 0, 0, 0)
    assert st.compute_threshold(b, 10) == 0


def test_compute_threshold_n_greater_than_len():
    assert st.compute_threshold(board(100, 90), 5) == 0


def test_compute_threshold_all_zero():
    assert st.compute_threshold(board(0, 0, 0), 1) == 0


# --- is_phantom_slot ---
def test_is_phantom_untouched_seed_across_band():
    # lowest slot (threshold) plus the three filler slots above it
    assert st.is_phantom_slot("", 5000, 5000) is True
    assert st.is_phantom_slot("", 5001, 5000) is True
    assert st.is_phantom_slot("", 5002, 5000) is True
    assert st.is_phantom_slot("", 5003, 5000) is True
    assert st.is_phantom_slot("   ", 5000, 5000) is True
    assert st.is_phantom_slot("???", 5003, 5000) is True


def test_is_phantom_just_above_band_not_phantom():
    assert st.is_phantom_slot("", 5004, 5000) is False


def test_is_phantom_real_claimable_blank_not_phantom():
    # beat the threshold but skipped initials -> must stay claimable
    assert st.is_phantom_slot("", 60000, 5000) is False


def test_is_phantom_real_player_not_phantom():
    assert st.is_phantom_slot("ABC", 5000, 5000) is False


def test_is_phantom_threshold_zero_never_phantom():
    assert st.is_phantom_slot("", 0, 0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest dev/tests/test_score_threshold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'score_threshold'`

- [ ] **Step 3: Write minimal implementation**

Create `src/common/score_threshold.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest dev/tests/test_score_threshold.py -v`
Expected: PASS — all 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/common/score_threshold.py dev/tests/test_score_threshold.py
git commit -m "feat: add pure helpers for top-N high-score trigger"
```

---

### Task 2: Persist cutoff in common extras record

**Files:**
- Modify: `src/common/SPI_DataStore.py` (extras serialize ~112-129, deserialize ~199-227)

No unit test — `SPI_DataStore` imports hardware. Verified by `py_compile` + on-device (Task 10).

- [ ] **Step 1: Add the helper import**

At the top of `src/common/SPI_DataStore.py`, after the existing imports, add:

```python
from score_threshold import clamp_cutoff
```

- [ ] **Step 2: Add `top_n_cutoff` to the extras deserialize dict**

In the `elif structure_name == "extras":` read branch, the success dict currently is:

```python
            return {
                "other": other,
                "lastIP": lastIP.decode().strip("\0"),
                "message": message.decode().strip("\0"),
                "enter_initials_on_game": bool(enable & 0x01),
                "claim_scores": bool(enable & 0x02),
                "show_ip_address": bool(enable & 0x04),
                "tournament_mode": bool(enable & 0x08),
                "flag5": bool(enable & 0x10),
                "flag6": bool(enable & 0x20),
            }
```

Add `"top_n_cutoff": clamp_cutoff(other),` so it becomes:

```python
            return {
                "other": other,
                "top_n_cutoff": clamp_cutoff(other),
                "lastIP": lastIP.decode().strip("\0"),
                "message": message.decode().strip("\0"),
                "enter_initials_on_game": bool(enable & 0x01),
                "claim_scores": bool(enable & 0x02),
                "show_ip_address": bool(enable & 0x04),
                "tournament_mode": bool(enable & 0x08),
                "flag5": bool(enable & 0x10),
                "flag6": bool(enable & 0x20),
            }
```

- [ ] **Step 3: Add `top_n_cutoff` to the extras fault-default dict**

The `except Exception:` fallback dict in the same read branch currently ends:

```python
                "tournament_mode": False,
                "flag5": False,
                "flag6": False,
            }
```

Add `"top_n_cutoff": 10,` and `"other": 10,` consistency (replace the existing `"other": 1,` line in that fallback dict with `"other": 10,`):

```python
                "other": 10,
                "lastIP": "none",
                "message": "none",
                "enter_initials_on_game": True,
                "claim_scores": False,
                "show_ip_address": True,
                "tournament_mode": False,
                "flag5": False,
                "flag6": False,
                "top_n_cutoff": 10,
            }
```

(Keep `"enable": 5,` as the first key — only change the `"other"` line and add the `"top_n_cutoff"` line.)

- [ ] **Step 4: Pack the cutoff into the `other` field on write**

In the `elif structure_name == "extras":` write branch, the final line is:

```python
        return struct.pack("<II20s20s", enable, record["other"], record["lastIP"].encode(), record["message"].encode())
```

Replace it with (derive the packed `other` from the clamped cutoff):

```python
        other_val = clamp_cutoff(record.get("top_n_cutoff", record.get("other", 10)))
        return struct.pack("<II20s20s", enable, other_val, record["lastIP"].encode(), record["message"].encode())
```

- [ ] **Step 5: Syntax-check**

Run: `python -m py_compile src/common/SPI_DataStore.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/common/SPI_DataStore.py
git commit -m "feat: persist top_n_cutoff in common extras record"
```

---

### Task 3: Persist cutoff in wpc extras record

**Files:**
- Modify: `src/wpc/SPI_DataStore.py` (extras serialize ~115-131, deserialize ~202-230)

- [ ] **Step 1: Add the helper import**

At the top of `src/wpc/SPI_DataStore.py`, after the existing imports, add:

```python
from score_threshold import clamp_cutoff
```

- [ ] **Step 2: Add `top_n_cutoff` to the extras deserialize dict**

The wpc success dict currently is:

```python
            return {
                "other": other,
                "lastIP": lastIP.decode().strip("\0"),
                "message": message.decode().strip("\0"),
                "enter_initials_on_game": bool(enable & 0x01),
                "claim_scores": bool(enable & 0x02),
                "show_ip_address": bool(enable & 0x04),
                "tournament_mode": bool(enable & 0x08),
                "WPCTimeOn": bool(enable & 0x10),
                "MM_Always": bool(enable & 0x20)
            }
```

Add `"top_n_cutoff": clamp_cutoff(other),`:

```python
            return {
                "other": other,
                "top_n_cutoff": clamp_cutoff(other),
                "lastIP": lastIP.decode().strip("\0"),
                "message": message.decode().strip("\0"),
                "enter_initials_on_game": bool(enable & 0x01),
                "claim_scores": bool(enable & 0x02),
                "show_ip_address": bool(enable & 0x04),
                "tournament_mode": bool(enable & 0x08),
                "WPCTimeOn": bool(enable & 0x10),
                "MM_Always": bool(enable & 0x20)
            }
```

- [ ] **Step 3: Add `top_n_cutoff` to the extras fault-default dict**

The wpc fallback dict currently ends:

```python
                "tournament_mode": False,
                "WPCTimeOn": False,
                "MM_Always": False,
            }
```

Change the existing `"other": 1,` line in that fallback dict to `"other": 10,` and add `"top_n_cutoff": 10,`:

```python
                "other": 10,
                "lastIP": "none",
                "message": "none",
                "enter_initials_on_game": True,
                "claim_scores": False,
                "show_ip_address": True,
                "tournament_mode": False,
                "WPCTimeOn": False,
                "MM_Always": False,
                "top_n_cutoff": 10,
            }
```

(Keep `"enable": 5,` as the first key.)

- [ ] **Step 4: Pack the cutoff into the `other` field on write**

The wpc write branch final line is:

```python
        return struct.pack("<II20s20s", enable, record["other"], record["lastIP"].encode(), record["message"].encode())
```

Replace with:

```python
        other_val = clamp_cutoff(record.get("top_n_cutoff", record.get("other", 10)))
        return struct.pack("<II20s20s", enable, other_val, record["lastIP"].encode(), record["message"].encode())
```

- [ ] **Step 5: Syntax-check**

Run: `python -m py_compile src/wpc/SPI_DataStore.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/wpc/SPI_DataStore.py
git commit -m "feat: persist top_n_cutoff in wpc extras record"
```

---

### Task 4: Seed threshold + phantom filter in common ScoreTrack

**Files:**
- Modify: `src/common/ScoreTrack.py` (imports ~9-15; globals ~19-23; `_read_machine_score` ~99-135; `_remove_machine_scores` ~224-251)

- [ ] **Step 1: Import the helpers**

After the existing `import` block at the top of `src/common/ScoreTrack.py` (after `from Shadow_Ram_Definitions import shadowRam`), add:

```python
from score_threshold import SEED_BAND, clamp_cutoff, compute_threshold, is_phantom_slot
```

- [ ] **Step 2: Add the `_seed_threshold` module global**

Near the other module globals (after `top_scores = []`), add:

```python
_seed_threshold = 0  # Nth-place score seeded into the machine table at game start
```

- [ ] **Step 3: Add the phantom filter in `_read_machine_score`**

In `_read_machine_score`, the initials loop contains:

```python
                if high_scores[idx][0] in ["???", "", None, "   "]:  # no player, allow claim
                    high_scores[idx][0] = ""
```

Immediately after those two lines (still inside the `for idx in range(4):` initials loop), add:

```python
                if is_phantom_slot(high_scores[idx][0], high_scores[idx][1], _seed_threshold):
                    high_scores[idx][1] = 0  # untouched seeded threshold slot, not a real score
```

- [ ] **Step 4: Replace `_remove_machine_scores` with the seeding version**

Replace the entire existing function:

```python
def _remove_machine_scores():
    """remove machine scores"""
    if S.gdata["HighScores"]["Type"] == 1 and DataStore.read_record("extras", 0)["enter_initials_on_game"]:  # system 11 type 1
        log.log("SCORE: Remove machine scores type 1")
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * 4
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * 3
            for i in range(4):
                shadowRam[score_start + i] = 0  # score
            for i in range(3):
                shadowRam[initial_start + i] = 0x3F  # intials
            shadowRam[score_start + 2] = 5 - index

    elif S.gdata["HighScores"]["Type"] == 3 and DataStore.read_record("extras", 0)["enter_initials_on_game"]:  # system 11, type 3
        log.log("SCORE: Remove machine scores type 3")
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * 4
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * 3

            for i in range(4):
                shadowRam[score_start + i] = 0
            shadowRam[score_start + 2] = 5 - index
            for i in range(3):
                shadowRam[initial_start + i] = 0x00

    elif S.gdata["HighScores"]["Type"] == 9:
        log.log("SCORE: Remove machine scores system 9")
        place_machine_scores()
```

with:

```python
def _remove_machine_scores():
    """Prep the machine high-score table for forced initials entry.

    When a top-N threshold applies, seed the four slots so the lowest slot equals
    the Nth-place leaderboard score and the three slots above it are
    threshold+1/+2/+3 (sentinel initials) — keeping the table validly descending
    while gating prompts on beating the threshold. When the board is not yet full
    (threshold 0) or on-machine entry is off, fall back to zeroing the slots so
    every score qualifies (original behavior).
    """
    global _seed_threshold

    if not DataStore.read_record("extras", 0)["enter_initials_on_game"]:
        _seed_threshold = 0
        if S.gdata["HighScores"]["Type"] == 9:
            log.log("SCORE: Remove machine scores system 9")
            place_machine_scores()
        return

    cutoff = clamp_cutoff(DataStore.read_record("extras", 0)["top_n_cutoff"])
    leaders = [DataStore.read_record("leaders", i) for i in range(DataStore.memory_map["leaders"]["count"])]
    _seed_threshold = compute_threshold(leaders, cutoff)

    if S.gdata["HighScores"]["Type"] == 1:  # system 11 type 1
        log.log("SCORE: Seed machine scores type 1, threshold=%d" % _seed_threshold)
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * 4
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * 3
            if _seed_threshold > 0:
                # index 0 (1st place) = threshold+3 ... index 3 (lowest) = threshold
                shadowRam[score_start : score_start + 4] = _int_to_bcd(_seed_threshold + (SEED_BAND - index))
                for i in range(3):
                    shadowRam[initial_start + i] = 0x3F  # sentinel initials -> reads blank
            else:
                for i in range(4):
                    shadowRam[score_start + i] = 0  # score
                for i in range(3):
                    shadowRam[initial_start + i] = 0x3F  # intials
                shadowRam[score_start + 2] = 5 - index

    elif S.gdata["HighScores"]["Type"] == 3:  # system 11, type 3
        log.log("SCORE: Seed machine scores type 3, threshold=%d" % _seed_threshold)
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * 4
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * 3
            if _seed_threshold > 0:
                shadowRam[score_start : score_start + 4] = _int_to_bcd(_seed_threshold + (SEED_BAND - index))
                for i in range(3):
                    shadowRam[initial_start + i] = 0x00  # sentinel initials -> reads blank
            else:
                for i in range(4):
                    shadowRam[score_start + i] = 0
                shadowRam[score_start + 2] = 5 - index
                for i in range(3):
                    shadowRam[initial_start + i] = 0x00

    elif S.gdata["HighScores"]["Type"] == 9:
        log.log("SCORE: Remove machine scores system 9")
        place_machine_scores()
```

- [ ] **Step 5: Syntax-check**

Run: `python -m py_compile src/common/ScoreTrack.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/common/ScoreTrack.py
git commit -m "feat: seed top-N threshold in common ScoreTrack"
```

---

### Task 5: Seed threshold + phantom filter in wpc ScoreTrack

**Files:**
- Modify: `src/wpc/ScoreTrack.py` (imports top; globals; `_read_machine_score` ~158-175; `_remove_machine_scores` ~291-320)

- [ ] **Step 1: Import the helpers**

After the existing import block at the top of `src/wpc/ScoreTrack.py` (after `from Shadow_Ram_Definitions import shadowRam`), add:

```python
from score_threshold import SEED_BAND, clamp_cutoff, compute_threshold, is_phantom_slot
```

- [ ] **Step 2: Add the `_seed_threshold` module global**

Near the other module globals (alongside `top_scores`), add:

```python
_seed_threshold = 0  # Nth-place score seeded into the machine table at game start
```

- [ ] **Step 3: Add the phantom filter in `_read_machine_score`**

In `_read_machine_score`, the table-slot initials loop contains:

```python
                if high_scores[idx][0] in ["???", "", None, "   "]:  # no player, allow claim
                    high_scores[idx][0] = ""
```

Immediately after those two lines (still inside the `for idx in range(1, 5):` initials loop), add:

```python
                if is_phantom_slot(high_scores[idx][0], high_scores[idx][1], _seed_threshold):
                    high_scores[idx][1] = 0  # untouched seeded threshold slot, not a real score
```

- [ ] **Step 4: Replace `_remove_machine_scores` with the seeding version**

Replace the entire existing function:

```python
def _remove_machine_scores(GrandChamp="Max"):
    """remove machine scores to prep for forced intial entry  - WPC"""
    if S.gdata["HighScores"]["Type"] == 10:
        log.log("SCORE: Remove machine scores type 10")
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * S.gdata["HighScores"]["ScoreSpacing"]
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * S.gdata["HighScores"]["InitialSpacing"]

            for i in range(S.gdata["HighScores"]["BytesInScore"]):
                shadowRam[score_start + i] = 0  # score

            shadowRam[score_start + S.gdata["HighScores"]["BytesInScore"] - 2] = 0x10 * (6 - index)

            for i in range(3):
                shadowRam[initial_start + i] = 0x41  # initials all 'A'

        # set grand champion score to max, so all players will be int he normal 1-4 places
        # Or near zero for reset leaderboard function
        if "GrandChampScoreAdr" in S.gdata["HighScores"]:
            score_start = S.gdata["HighScores"]["GrandChampScoreAdr"]
            if GrandChamp == "Max":
                # set grand champion score to max
                for i in range(S.gdata["HighScores"]["BytesInScore"]):
                    shadowRam[score_start + i] = 0x99
            elif GrandChamp == "Zero":
                # set grand champ to min
                for i in range(S.gdata["HighScores"]["BytesInScore"]):
                    shadowRam[score_start + i] = 0x00
                shadowRam[score_start + S.gdata["HighScores"]["BytesInScore"] - 2] = 0x90

        fix_high_score_checksum()
```

with:

```python
def _remove_machine_scores(GrandChamp="Max"):
    """Prep the machine high-score table for forced initials entry - WPC.

    Seed the four slots so the lowest equals the top-N threshold and the three
    above it are threshold+1/+2/+3 (sentinel initials) — a validly descending
    table that only prompts players who BEAT the threshold. Fall back to zeroing
    when the board is not yet full. The GrandChamp="Zero" path (leaderboard
    reset) always zeroes.
    """
    global _seed_threshold

    if GrandChamp == "Zero":
        _seed_threshold = 0
    else:
        cutoff = clamp_cutoff(DataStore.read_record("extras", 0)["top_n_cutoff"])
        leaders = [DataStore.read_record("leaders", i) for i in range(DataStore.memory_map["leaders"]["count"])]
        _seed_threshold = compute_threshold(leaders, cutoff)

    if S.gdata["HighScores"]["Type"] == 10:
        log.log("SCORE: Seed machine scores type 10, threshold=%d" % _seed_threshold)
        for index in range(4):
            score_start = S.gdata["HighScores"]["ScoreAdr"] + index * S.gdata["HighScores"]["ScoreSpacing"]
            initial_start = S.gdata["HighScores"]["InitialAdr"] + index * S.gdata["HighScores"]["InitialSpacing"]

            if _seed_threshold > 0:
                # index 0 (1st place) = threshold+3 ... index 3 (lowest) = threshold
                shadowRam[score_start : score_start + S.gdata["HighScores"]["BytesInScore"]] = _int_to_bcd(_seed_threshold + (SEED_BAND - index))
                for i in range(3):
                    shadowRam[initial_start + i] = 0x20  # space sentinel -> reads blank
            else:
                for i in range(S.gdata["HighScores"]["BytesInScore"]):
                    shadowRam[score_start + i] = 0  # score
                shadowRam[score_start + S.gdata["HighScores"]["BytesInScore"] - 2] = 0x10 * (6 - index)
                for i in range(3):
                    shadowRam[initial_start + i] = 0x41  # initials all 'A'

        # set grand champion score to max, so all players land in the normal 1-4 places
        # Or near zero for reset leaderboard function
        if "GrandChampScoreAdr" in S.gdata["HighScores"]:
            score_start = S.gdata["HighScores"]["GrandChampScoreAdr"]
            if GrandChamp == "Max":
                for i in range(S.gdata["HighScores"]["BytesInScore"]):
                    shadowRam[score_start + i] = 0x99
            elif GrandChamp == "Zero":
                for i in range(S.gdata["HighScores"]["BytesInScore"]):
                    shadowRam[score_start + i] = 0x00
                shadowRam[score_start + S.gdata["HighScores"]["BytesInScore"] - 2] = 0x90

        fix_high_score_checksum()
```

- [ ] **Step 5: Syntax-check**

Run: `python -m py_compile src/wpc/ScoreTrack.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/wpc/ScoreTrack.py
git commit -m "feat: seed top-N threshold in wpc ScoreTrack"
```

---

### Task 6: Expose cutoff through the claim-methods API

**Files:**
- Modify: `src/common/backend.py` (`app_getScoreCap` GET return ~1153-1157; `app_setScoreCap` SET body ~1184-1189)

- [ ] **Step 1: Add `top-n-cutoff` to the GET response**

In the `/api/settings/get_claim_methods` handler, the return is:

```python
    record = ds_read_record("extras", 0)
    return {
        "on-machine": record["enter_initials_on_game"],
        "web-ui": record["claim_scores"],
    }
```

Change it to:

```python
    record = ds_read_record("extras", 0)
    return {
        "on-machine": record["enter_initials_on_game"],
        "web-ui": record["claim_scores"],
        "top-n-cutoff": record["top_n_cutoff"],
    }
```

- [ ] **Step 2: Accept `top-n-cutoff` in the SET handler**

In the `/api/settings/set_claim_methods` handler, the body handling is:

```python
    json_data = request.data
    record = ds_read_record("extras", 0)
    if "on-machine" in json_data:
        record["enter_initials_on_game"] = bool(json_data["on-machine"])
    if "web-ui" in json_data:
        record["claim_scores"] = bool(json_data["web-ui"])
    ds_write_record("extras", record, 0)
```

Change it to (the serialize path clamps the value, so store the raw int):

```python
    json_data = request.data
    record = ds_read_record("extras", 0)
    if "on-machine" in json_data:
        record["enter_initials_on_game"] = bool(json_data["on-machine"])
    if "web-ui" in json_data:
        record["claim_scores"] = bool(json_data["web-ui"])
    if "top-n-cutoff" in json_data:
        record["top_n_cutoff"] = int(json_data["top-n-cutoff"])
    ds_write_record("extras", record, 0)
```

- [ ] **Step 3: Document the new field in the docstrings**

In the GET handler docstring, change the example body from:

```python
            {
                "on-machine": true,
                "web-ui": false
            }
```

to:

```python
            {
                "on-machine": true,
                "web-ui": false,
                "top-n-cutoff": 10
            }
```

In the SET handler docstring, add this entry to the `body:` list under `request:`, after the `web-ui` entry:

```python
        - name: top-n-cutoff
          type: int
          required: false
          description: Only force on-machine initials entry for scores in the top N (1-20, default 10)
```

- [ ] **Step 4: Syntax-check + regen API docs guard**

Run: `python -m py_compile src/common/backend.py`
Expected: no output, exit 0.

Run: `python -m pytest dev/tests/test_gen_api_docs.py -v`
Expected: PASS (the API-doc generator still parses `backend.py`). If this test regenerates/compares docs and fails because docs are stale, run the generator it names (`python tools/gen_api_docs.py`) and re-run the test.

- [ ] **Step 5: Commit**

```bash
git add src/common/backend.py docs/
git commit -m "feat: expose top-n-cutoff via claim-methods API"
```

---

### Task 7: Add the cutoff input to the admin UI

**Files:**
- Modify: `src/common/web/html/admin.html` (Score Claim Methods fieldset ~230-249)

- [ ] **Step 1: Add the number input inside the score-claim-methods fieldset**

The fieldset currently ends with the web-ui label:

```html
      <label>
        <input
          id="web-ui-toggle"
          type="checkbox"
          role="switch"
          name="web"
          disabled
        />
        <b>Web Interface:</b> for a short time after playing; players may claim
        scores via web interface
      </label>
    </fieldset>
```

Insert a new label block immediately before the closing `</fieldset>`:

```html
      <label>
        <input
          id="web-ui-toggle"
          type="checkbox"
          role="switch"
          name="web"
          disabled
        />
        <b>Web Interface:</b> for a short time after playing; players may claim
        scores via web interface
      </label>
      <label>
        <b>Top-N cutoff:</b> only collect initials on-machine for scores in the
        top
        <input
          id="top-n-cutoff-input"
          type="number"
          name="top-n-cutoff"
          min="1"
          max="20"
          value="10"
          style="width: 4rem"
          disabled
        />
        scores
      </label>
    </fieldset>
```

- [ ] **Step 2: Syntax sanity check**

Run: `python -c "import xml.dom.minidom, sys; print('html present:', 'top-n-cutoff-input' in open('src/common/web/html/admin.html').read())"`
Expected: `html present: True`

- [ ] **Step 3: Commit**

```bash
git add src/common/web/html/admin.html
git commit -m "feat: add top-N cutoff input to admin page"
```

---

### Task 8: Plumb the cutoff input through admin.js

**Files:**
- Modify: `src/common/web/js/admin.js` (`getScoreClaimMethods` ~27-60)

- [ ] **Step 1: Load, save, and enable/disable the cutoff input**

The `getScoreClaimMethods` function currently is:

```javascript
async function getScoreClaimMethods() {
  const response = await window.smartFetch(
    "/api/settings/get_claim_methods",
    null,
    false,
  );
  const data = await response.json();

  const onMachineToggle = await window.waitForElementById("on-machine-toggle");
  const webUIToggle = await window.waitForElementById("web-ui-toggle");

  onMachineToggle.checked = data["on-machine"];
  onMachineToggle.disabled = false;

  webUIToggle.checked = data["web-ui"];
  webUIToggle.disabled = false;

  // Helper function to add event listener to claim method toggle
  function addClaimMethodToggleListener(toggle) {
    toggle.addEventListener("change", async () => {
      const data = {
        "on-machine": onMachineToggle.checked ? 1 : 0,
        "web-ui": webUIToggle.checked ? 1 : 0,
      };
      await window.smartFetch("/api/settings/set_claim_methods", data, true);
    });
  }

  // Apply listener to both toggles
  addClaimMethodToggleListener(onMachineToggle);
  addClaimMethodToggleListener(webUIToggle);
}
```

Replace it with:

```javascript
async function getScoreClaimMethods() {
  const response = await window.smartFetch(
    "/api/settings/get_claim_methods",
    null,
    false,
  );
  const data = await response.json();

  const onMachineToggle = await window.waitForElementById("on-machine-toggle");
  const webUIToggle = await window.waitForElementById("web-ui-toggle");
  const cutoffInput = await window.waitForElementById("top-n-cutoff-input");

  onMachineToggle.checked = data["on-machine"];
  onMachineToggle.disabled = false;

  webUIToggle.checked = data["web-ui"];
  webUIToggle.disabled = false;

  cutoffInput.value = data["top-n-cutoff"];
  // cutoff only applies when on-machine entry is enabled
  cutoffInput.disabled = !onMachineToggle.checked;

  async function saveClaimMethods() {
    const payload = {
      "on-machine": onMachineToggle.checked ? 1 : 0,
      "web-ui": webUIToggle.checked ? 1 : 0,
      "top-n-cutoff": parseInt(cutoffInput.value, 10),
    };
    await window.smartFetch("/api/settings/set_claim_methods", payload, true);
  }

  onMachineToggle.addEventListener("change", async () => {
    cutoffInput.disabled = !onMachineToggle.checked;
    await saveClaimMethods();
  });
  webUIToggle.addEventListener("change", saveClaimMethods);
  cutoffInput.addEventListener("change", async () => {
    // clamp to 1..20 before saving
    let v = parseInt(cutoffInput.value, 10);
    if (isNaN(v) || v < 1) v = 1;
    if (v > 20) v = 20;
    cutoffInput.value = v;
    await saveClaimMethods();
  });
}
```

- [ ] **Step 2: Sanity check**

Run: `node --check src/common/web/js/admin.js`
Expected: no output, exit 0. (If `node` is unavailable, confirm the file contains `top-n-cutoff-input` and `saveClaimMethods` via grep.)

- [ ] **Step 3: Commit**

```bash
git add src/common/web/js/admin.js
git commit -m "feat: plumb top-N cutoff input through admin.js"
```

---

### Task 9: Bump versions for the CI guard

The `dev/ci/version_bump_guard` CI gate requires a version bump for every touched
scope. This change touches `src/` (→ `src/common/SharedState.py`) and `src/wpc/`
(→ `src/wpc/systemConfig.py`). Other systems' dirs were not modified, so their
`systemConfig.py` files do **not** need bumping.

**Files:**
- Modify: `src/common/SharedState.py:1`
- Modify: `src/wpc/systemConfig.py:5`

- [ ] **Step 1: Bump the common VectorVersion (patch)**

In `src/common/SharedState.py`, change:

```python
VectorVersion = "1.11.22"
```

to:

```python
VectorVersion = "1.11.23"
```

- [ ] **Step 2: Bump the WPC SystemVersion (patch)**

In `src/wpc/systemConfig.py`, change:

```python
SystemVersion = "1.7.10"
```

to:

```python
SystemVersion = "1.7.11"
```

- [ ] **Step 3: Commit**

```bash
git add src/common/SharedState.py src/wpc/systemConfig.py
git commit -m "chore: bump common + wpc versions for top-N trigger"
```

> Note: if the base branch has advanced past these versions by the time you
> implement, set each value to one patch above the current base-branch value
> instead of the literals above (the guard only requires head > base).

---

### Task 10: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full CPython test suite**

Run: `python -m pytest dev/tests/ -v`
Expected: PASS, including the 15 new `test_score_threshold.py` tests and the existing suite.

- [ ] **Step 2: Syntax-check every modified on-device module**

Run:
```bash
python -m py_compile \
  src/common/score_threshold.py \
  src/common/SPI_DataStore.py src/wpc/SPI_DataStore.py \
  src/common/ScoreTrack.py src/wpc/ScoreTrack.py \
  src/common/backend.py
```
Expected: no output, exit 0.

- [ ] **Step 3: On-device manual verification (record results in the PR)**

Flash a System 11 unit (`buildsys11.bat`) and a WPC unit (`buildwpc.bat`), then verify the spec's manual checklist:
1. On-Machine on, cutoff 10, full (20-entry) board: a score below the 10th-place value does NOT prompt for initials; a score above it DOES and lands on the leaderboard.
2. Board with fewer than 10 entries: every score still prompts (fallback path).
3. Player beats threshold but skips initials entry: the score is still claimable via the web UI ("allow claim" preserved).
4. After a game, the claimable-scores list shows only real entered/qualifying scores — no phantom threshold-valued entries.
5. WPC: grand champion + high-score checksum remain valid; machine boots and shows correct attract-mode high scores.
6. Descending-table acceptance: confirm the machine prompts correctly with the `threshold+3 … threshold` filler table (lowest slot == threshold). A player scoring below the 10th-place value should NOT be prompted; a player above it should be.
7. Admin UI: the cutoff input loads the stored value, disables when On-Machine is off, and persists edits (reload the page and confirm).

- [ ] **Step 4: Finalize the branch**

Use the superpowers:finishing-a-development-branch skill to decide merge/PR/cleanup.

---

## Notes for the implementer

- **Import resolution:** `from score_threshold import ...` works on-device because `dev/build.py` flattens `src/common/*` and the platform dir into one directory. In unit tests the module is reached via the `sys.path.insert` in `test_score_threshold.py`.
- **`other` field repurposing:** the `other` extras field was round-tripped but referenced nowhere else; it now backs `top_n_cutoff`. The `"other"` key is left in the read dict for compatibility but is no longer authoritative.
- **Backward compatibility:** existing units store `other == 0`, which `clamp_cutoff` resolves to 10 — so On-Machine units move from "prompt every game" to "prompt for top 10" on upgrade. Call this out in release notes.
- **Why phantom detection uses the band `[threshold, threshold+3]`:** seeded slots occupy the lowest slot (`threshold`) plus three filler slots (`threshold+1/+2/+3`) just above it. A real qualifying score is orders of magnitude larger than the threshold (the machine only inserts scores that beat its lowest slot, and real pinball scores never land within 3 points of it). Combined with the blank-initials check, this zeroes only untouched seeds and never a genuine claimable score.
- **Why filler slots instead of real higher leaderboard scores:** real scores carry real initials, survive the readback filter, and would leak into the "recent game"/claim list as stale entries. Synthetic `threshold+k` sentinels read back as phantoms and get dropped.
