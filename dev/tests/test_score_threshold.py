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
