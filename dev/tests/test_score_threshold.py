import sys
from pathlib import Path

SRC_COMMON = Path(__file__).resolve().parents[2] / "src" / "common"
sys.path.insert(0, str(SRC_COMMON))

import score_threshold as st


def board(*scores):
    return [{"score": s} for s in scores]


# --- pad_initials ---
def test_pad_initials_single_letter():
    # the Taxi bug: "G"/"H" must reach the machine as 3 chars, not be blanked
    assert st.pad_initials("G") == "G  "


def test_pad_initials_two_letters():
    assert st.pad_initials("GG") == "GG "


def test_pad_initials_three_letters_unchanged():
    assert st.pad_initials("ABC") == "ABC"


def test_pad_initials_empty_becomes_spaces():
    assert st.pad_initials("") == "   "


def test_pad_initials_none_becomes_spaces():
    assert st.pad_initials(None) == "   "


def test_pad_initials_already_padded_idempotent():
    assert st.pad_initials("G  ") == "G  "


def test_pad_initials_truncates_overlong():
    assert st.pad_initials("ABCD") == "ABC"


# --- is_rejected_blank ---
def test_rejected_blank_empty_claim_off():
    # a no-initials score with claiming off earns no leaderboard slot
    assert st.is_rejected_blank("", False) is True


def test_rejected_blank_empty_claim_on():
    # claiming on: an unclaimed blank is kept so it can be claimed later
    assert st.is_rejected_blank("", True) is False


def test_rejected_blank_whitespace_claim_off():
    assert st.is_rejected_blank("   ", False) is True


def test_rejected_blank_none_claim_off():
    assert st.is_rejected_blank(None, False) is True


def test_rejected_blank_real_initials_always_kept():
    assert st.is_rejected_blank("ABC", False) is False
    assert st.is_rejected_blank("G", False) is False


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


def test_compute_threshold_none_entry_returns_zero():
    # a corrupt leaders slot can deserialize to None; must not raise
    assert st.compute_threshold([{"score": 100}, {"score": 90}, None], 3) == 0


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


def test_dedupe_blank_dropped_when_nonblank_shares_score():
    # the Taxi ghost: a claimed "G" row and an unclaimed blank re-read from the
    # machine at the identical score -> the blank is the unclaimed duplicate, drop it
    out = st.dedupe_leaderboard([L("G", 1747430), L("", 1747430)], 20)
    assert keyed(out) == [("G", 1747430)]


def test_dedupe_blank_kept_when_no_nonblank_shares_score():
    # a genuine unclaimed score with no claimed twin must survive
    out = st.dedupe_leaderboard([L("G", 1747430), L("", 900000)], 20)
    assert set(keyed(out)) == {("G", 1747430), ("", 900000)}


def test_dedupe_blank_dropped_prefers_highest_blank_first():
    # blanks collapse to the highest, then that blank is dropped if a non-blank
    # matches it; a lower blank with no twin is lost (existing collapse behavior)
    out = st.dedupe_leaderboard([L("G", 1747430), L("", 1747430), L("", 900000)], 20)
    assert keyed(out) == [("G", 1747430)]


# --- build_seed_table ---
def full_board():
    # 10 real entries, descending
    return [L(f"P{i}{i}{i}"[:3], (10 - i) * 1_000_000) for i in range(10)]


# Default award_slots=1: only the champion stays unbeatable (that slot gates the
# per-level credit award); the rest of the table is floored at the entry
# threshold so SEVERAL players in one game can each cascade into a low slot and
# be prompted for initials.
def test_seed_table_champion_honest_rest_floored():
    out = st.build_seed_table(full_board(), 10, slots=4)
    assert out == [
        ("P00", 10_000_000),  # champion stays unbeatable -> gates the award
        ("", 1_000_002),      # slots 2-4 floored just above the 10th-place score
        ("", 1_000_001),      # so multiple players can enter in one game
        ("", 1_000_000),      # lowest slot == 10th place (entry threshold)
    ]


def test_seed_table_low_slots_clustered_for_multiplayer_entry():
    # the three low slots sit within one band of the threshold, so up to three
    # players can cascade into them in a single game
    out = st.build_seed_table(full_board(), 10, slots=4)
    low = [s for _, s in out[1:]]
    assert len(low) == 3
    assert max(low) - min(low) <= st.SEED_BAND


def test_seed_table_award_slots_two_keeps_top_two():
    out = st.build_seed_table(full_board(), 10, slots=4, award_slots=2)
    assert out[0] == ("P00", 10_000_000)
    assert out[1] == ("P11", 9_000_000)
    assert out[3] == ("", 1_000_000)  # two floored slots -> two players can enter


def test_seed_table_partial_board_floor_below_champion():
    # board not full: champion on top, lower slots floored at the lowest existing
    # real score (a valid high-score magnitude the game honors, unlike a tiny
    # placeholder it would ignore)
    out = st.build_seed_table([L("ABC", 5000), L("DEF", 4000)], 10, slots=4)
    assert out[0] == ("ABC", 5000)
    assert out[-1] == ("", 4000)
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))


def test_seed_table_empty_board_all_placeholders():
    # no real scores: tiny placeholders so the very first scores qualify
    out = st.build_seed_table([L("", 0)] * 10, 10, slots=4)
    assert out == [("", 500), ("", 400), ("", 300), ("", 200)]


def test_seed_table_single_real_score_floors_low():
    # only a champion exists: floor the rest low so OTHER players can still enter
    out = st.build_seed_table([L("ABC", 5000)] + [L("", 0)] * 9, 10, slots=4)
    assert out[0] == ("ABC", 5000)
    assert all(s < 1000 for _, s in out[1:])  # low placeholders, all beatable
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))


def test_seed_table_users_taxi_board_champion_plus_floor():
    # the real board: champion gates the award, slots 2-4 floored at 86,300 so a
    # multi-player game lets up to three players enter and none below the champion
    # collects a credit
    leaders = [
        L("RAT", 5157770),
        L("TPK", 4234400),
        L("G", 1747430),
        L("H", 122560),
        L("AAA", 86300),
    ]
    out = st.build_seed_table(leaders, 10, slots=4)
    assert out[0] == ("RAT", 5157770)
    assert out[-1] == ("", 86300)  # floor = lowest existing score
    assert all(s <= 86300 + st.SEED_BAND for _, s in out[1:])  # 2-4 all beatable
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))


def test_seed_table_cutoff_3_floor_at_third():
    out = st.build_seed_table(full_board(), 3, slots=4)
    assert out[0] == ("P00", 10_000_000)
    assert out[-1] == ("", 8_000_000)  # 3rd-place score is the entry floor
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))


def test_seed_table_cutoff_1_degenerates_to_band():
    out = st.build_seed_table(full_board(), 1, slots=4)
    assert out == [
        ("", 10_000_003),
        ("", 10_000_002),
        ("", 10_000_001),
        ("", 10_000_000),
    ]


def test_seed_table_non_increasing_with_ties():
    leaders = [L("AAA", 5000), L("BBB", 4000), L("CCC", 4000)] + [L("XXX", 100)] * 7
    out = st.build_seed_table(leaders, 3, slots=4)
    scores = [s for _, s in out]
    assert all(a >= b for a, b in zip(scores, scores[1:]))
    assert out[0] == ("AAA", 5000)


def test_seed_table_wpc_five_slots_champion_plus_floor():
    out = st.build_seed_table(full_board(), 10, slots=5)
    assert len(out) == 5
    assert out[0] == ("P00", 10_000_000)
    assert out[-1] == ("", 1_000_000)
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))
    assert out[1:] == [
        ("", 1_000_003),
        ("", 1_000_002),
        ("", 1_000_001),
        ("", 1_000_000),
    ]


def test_seed_table_none_leader_entry_skipped():
    # corrupt record deserializes to None -> skipped, must not raise; champion
    # and threshold still computed from the real entries
    leaders = [L("AAA", 5000), None] + [L("XXX", 1000)] * 8
    out = st.build_seed_table(leaders, 10, slots=4)
    assert out[0] == ("AAA", 5000)
    assert out[-1] == ("", 1000)
    assert all(a[1] >= b[1] for a, b in zip(out, out[1:]))


# --- matches_seeded_slot ---
def test_matches_seeded_slot_exact_honest_entry():
    seeded = [("ABC", 5_000_000), ("", 1_000_000)]
    assert st.matches_seeded_slot("ABC", 5_000_000, seeded) is True


def test_matches_seeded_slot_blank_threshold_entry():
    seeded = [("ABC", 5_000_000), ("", 1_000_000)]
    assert st.matches_seeded_slot("", 1_000_000, seeded) is True


def test_matches_seeded_slot_new_score_same_initials():
    seeded = [("ABC", 5_000_000)]
    assert st.matches_seeded_slot("ABC", 6_000_000, seeded) is False


def test_matches_seeded_slot_same_score_different_player():
    seeded = [("ABC", 5_000_000)]
    assert st.matches_seeded_slot("XYZ", 5_000_000, seeded) is False


def test_matches_seeded_slot_whitespace_insensitive():
    # machine pads initials to 3 chars with spaces
    seeded = [("AB ", 5_000_000)]
    assert st.matches_seeded_slot("AB", 5_000_000, seeded) is True


def test_matches_seeded_slot_none_initials_treated_blank():
    seeded = [("", 1_000_000)]
    assert st.matches_seeded_slot(None, 1_000_000, seeded) is True


def test_matches_seeded_slot_empty_seed_list():
    assert st.matches_seeded_slot("ABC", 5_000_000, []) is False
