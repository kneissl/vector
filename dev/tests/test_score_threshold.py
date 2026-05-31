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
    assert st.is_phantom_slot(None, 5000, 5000) is True


def test_is_phantom_just_above_band_not_phantom():
    assert st.is_phantom_slot("", 5004, 5000) is False


def test_is_phantom_real_claimable_blank_not_phantom():
    # beat the threshold but skipped initials -> must stay claimable
    assert st.is_phantom_slot("", 60000, 5000) is False


def test_is_phantom_real_player_not_phantom():
    assert st.is_phantom_slot("ABC", 5000, 5000) is False


def test_is_phantom_threshold_zero_never_phantom():
    assert st.is_phantom_slot("", 0, 0) is False


# --- dedupe_leaderboard ---
def L(initials, score):
    return {"initials": initials, "score": score}


def keyed(entries):
    return [(e["initials"], e["score"]) for e in entries]


def test_dedupe_keeps_highest_per_initials():
    out = st.dedupe_leaderboard([L("ABC", 100), L("ABC", 300), L("ABC", 200)], 20)
    assert keyed(out) == [("ABC", 300)]


def test_dedupe_key_is_case_and_whitespace_insensitive():
    # "abc" and "ABC" map to the same key; the higher score is kept
    out = st.dedupe_leaderboard([L("abc", 100), L("ABC", 300)], 20)
    assert keyed(out) == [("ABC", 300)]


def test_dedupe_blanks_collapse_to_one_highest():
    out = st.dedupe_leaderboard([L("", 5000), L("", 3000), L("   ", 4000)], 20)
    assert keyed(out) == [("", 5000)]


def test_dedupe_distinct_players_equal_scores_both_kept():
    out = st.dedupe_leaderboard([L("ABC", 500), L("XYZ", 500)], 20)
    assert set(keyed(out)) == {("ABC", 500), ("XYZ", 500)}
    assert len(out) == 2


def test_dedupe_skips_none_entries():
    out = st.dedupe_leaderboard([None, L("ABC", 100), None], 20)
    assert keyed(out) == [("ABC", 100)]


def test_dedupe_sorted_descending():
    out = st.dedupe_leaderboard([L("A", 10), L("B", 30), L("C", 20)], 20)
    assert keyed(out) == [("B", 30), ("C", 20), ("A", 10)]


def test_dedupe_truncates_to_count():
    out = st.dedupe_leaderboard([L("A", 10), L("B", 20), L("C", 30)], 2)
    assert keyed(out) == [("C", 30), ("B", 20)]


def test_dedupe_empty_input():
    assert st.dedupe_leaderboard([], 20) == []


def test_dedupe_missing_initials_key_treated_as_blank():
    out = st.dedupe_leaderboard([{"score": 700}, L("", 200)], 20)
    # both have blank key -> collapse; highest (700) kept
    assert [e["score"] for e in out] == [700]
