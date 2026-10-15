"""Answers worked out by hand (with a pencil, see data/synthetic/FIXTURES.md) for the fixtures.

These are typed in, not computed. `check(key)` compares them with what the answer key's code says.
If the two ever disagree, one of them is wrong and the generator stops. The fixtures do not use
the random seed for their scores, so the hand answers hold for every seed.

Subject default for the fixtures: level = base + a spread by the subject's place in the template
(0, +0.5, -0.5, +1, -1, +0.5, 0, -0.5 repeating), flat across the three terms. With base 13:
English 13.5, French 12.5, Literature in English 13.5, Mathematics 13.0.

Skill value of literacy in a term = (4 x English + 4 x French + 2 x Literature) / 10   (CA-3)
Level = stage-weighted average of the window (CA-4); gap = distance to the nearest edge (CA-5);
gap direction = last term's gap minus first term's gap against the TR-4 threshold.
"""

# (fixture, template, path in the learner's result, expected text)
HAND = [
    # --- FX-01: English 15,15,15 then 14,13,12; the other subjects flat at 13
    # v1: window 3 = 14,13,12.  change = 12 - 14 = -2.00 (more than 1 down).  level = 39/3 = 13.00
    ("FX-01", "v1", ("subjects", "English", "label"), "declining"),
    ("FX-01", "v1", ("subjects", "English", "change"), "-2.00"),
    ("FX-01", "v1", ("subjects", "English", "level"), "13.00"),
    # v2-strict: window 4 = 15,14,13,12.  change = 12 - 15 = -3.00.  level = 54/4 = 13.50
    ("FX-01", "v2-strict", ("subjects", "English", "label"), "declining"),
    ("FX-01", "v2-strict", ("subjects", "English", "change"), "-3.00"),
    ("FX-01", "v2-strict", ("subjects", "English", "level"), "13.50"),
    # literacy skill 2025/26: T1 (56+50+27)/10 = 13.3, T2 (52+50+27)/10 = 12.9,
    # T3 (48+50+27)/10 = 12.5
    # v1 level = 38.7/3 = 12.90, inside Form 2 literacy 11 to 15, so gap 0
    ("FX-01", "v1", ("areas", "literacy", "level"), "12.90"),
    ("FX-01", "v1", ("areas", "literacy", "gap"), "0.00"),
    # v2: add 2024/25 T3 = (60+50+27)/10 = 13.7.  level = 52.4/4 = 13.10.  Form 2 range 15 to 19:
    # gap 1.90.  first term gap (Form 1 range 14 to 18) 0.30, last 2.50, change 2.20 > 0.25:
    # widening
    ("FX-01", "v2-strict", ("areas", "literacy", "level"), "13.10"),
    ("FX-01", "v2-strict", ("areas", "literacy", "gap"), "1.90"),
    ("FX-01", "v2-strict", ("areas", "literacy", "gap_direction"), "widening"),
    # --- FX-02: two English scores only
    ("FX-02", "v1", ("subjects", "English", "label"), "not_enough_evidence"),
    ("FX-02", "v2-strict", ("subjects", "English", "label"), "not_enough_evidence"),
    # --- FX-03a / FX-03b: Mathematics 10, 12, 14 (out of 20) and 50, 60, 70 (out of 100)
    # 50 x 20/100 = 10, 60 -> 12, 70 -> 14.  change = 14 - 10 = 4.00.  level = 36/3 = 12.00
    ("FX-03a", "v1", ("subjects", "Mathematics", "change"), "4.00"),
    ("FX-03a", "v1", ("subjects", "Mathematics", "level"), "12.00"),
    ("FX-03a", "v1", ("subjects", "Mathematics", "label"), "improving"),
    ("FX-03b", "v1", ("subjects", "Mathematics", "change"), "4.00"),
    ("FX-03b", "v1", ("subjects", "Mathematics", "level"), "12.00"),
    ("FX-03b", "v1", ("subjects", "Mathematics", "label"), "improving"),
    # v2-strict needs 4 scores, there are 3
    ("FX-03a", "v2-strict", ("subjects", "Mathematics", "label"), "not_enough_evidence"),
    # --- FX-04a..d
    ("FX-04a", "v1", ("subjects", "English", "label"), "blocked"),
    ("FX-04a", "v1", ("areas", "literacy", "level_name"), "blocked"),
    ("FX-04b", "v1", ("subjects", "Chemistry", "label"), "blocked"),
    ("FX-04c", "v1", ("subjects", "Mathematics", "label"), "blocked"),
    ("FX-04d", "v1", ("subjects", "History", "label"), "steady"),  # a printed slip blocks nothing
    # --- FX-05: Geography 15, 14, 13 with the remark "Improving; ..."
    ("FX-05", "v1", ("subjects", "Geography", "change"), "-2.00"),
    ("FX-05", "v1", ("subjects", "Geography", "label"), "declining"),
    # --- FX-08 Amina.  Literacy values per term, F2 T3 = (44+42+22)/10 = 10.8,
    # F3 T1 (44+44+23)/10 = 11.1, T2 (46+44+23)/10 = 11.3, T3 (48+44+23)/10 = 11.5
    # v1: level = (11.1+11.3+11.5)/3 = 11.30.  Form 3 range 12 to 16 -> gap 0.70.
    # first gap 12 - 11.1 = 0.90, last 12 - 11.5 = 0.50, change -0.40, not more than 0.5: steady
    ("FX-08", "v1", ("areas", "literacy", "level"), "11.30"),
    ("FX-08", "v1", ("areas", "literacy", "level_name"), "below_expected_range"),
    ("FX-08", "v1", ("areas", "literacy", "gap"), "0.70"),
    ("FX-08", "v1", ("areas", "literacy", "gap_first_term"), "0.90"),
    ("FX-08", "v1", ("areas", "literacy", "gap_last_term"), "0.50"),
    ("FX-08", "v1", ("areas", "literacy", "gap_direction"), "steady"),
    ("FX-08", "v1", ("areas", "literacy", "range"), ["12.00", "16.00"]),
    # her English 11, 11.5, 12: change exactly 1.00, which is not "more than 1": steady
    ("FX-08", "v1", ("subjects", "English", "change"), "1.00"),
    ("FX-08", "v1", ("subjects", "English", "label"), "steady"),
    # v2: window 4 = 10.8 (F2), 11.1, 11.3, 11.5 (F3).  weights 1,2,2,2:
    # level = (10.8 + 2 x 33.9) / 7 = 78.6 / 7 = 11.2286 -> 11.23.  Form 3 range 16 to 20:
    # gap 4.7714 -> 4.77.  first gap (Form 2 range 15 to 19) 4.20, last 4.50, change 0.30 > 0.25
    ("FX-08", "v2-strict", ("areas", "literacy", "level"), "11.23"),
    ("FX-08", "v2-strict", ("areas", "literacy", "gap"), "4.77"),
    ("FX-08", "v2-strict", ("areas", "literacy", "gap_first_term"), "4.20"),
    ("FX-08", "v2-strict", ("areas", "literacy", "gap_last_term"), "4.50"),
    ("FX-08", "v2-strict", ("areas", "literacy", "gap_direction"), "widening"),
    ("FX-08", "v2-strict", ("areas", "literacy", "range"), ["16.00", "20.00"]),
    # her English in v2: 11, 11, 11.5, 12: change 1.00 now more than 0.5: improving
    ("FX-08", "v2-strict", ("subjects", "English", "label"), "improving"),
    # and its level: window 11 (F2), 11, 11.5, 12 with weights 1,2,2,2 = (11 + 2 x 34.5) / 7
    # = 80 / 7 = 11.4286 -> 11.43 (an unweighted mean, 45.5 / 4 = 11.38, would be wrong)
    ("FX-08", "v2-strict", ("subjects", "English", "level"), "11.43"),
    # --- FX-09a: F3 literacy 11.5, 11, 10.5 (all three subjects the same)
    # v1: level 11.00, gap 12 - 11 = 1.00, first 0.50, last 1.50, change 1.00 > 0.5: widening
    ("FX-09a", "v1", ("areas", "literacy", "gap"), "1.00"),
    ("FX-09a", "v1", ("areas", "literacy", "gap_first_term"), "0.50"),
    ("FX-09a", "v1", ("areas", "literacy", "gap_last_term"), "1.50"),
    ("FX-09a", "v1", ("areas", "literacy", "gap_direction"), "widening"),
    # v2: window 11 (F2), 11.5, 11, 10.5.  level = (11 + 2 x 33)/7 = 11.00.  gap 16 - 11 = 5.00
    # first (Form 2 range 15 to 19) 4.00, last 5.50, change 1.50: widening
    ("FX-09a", "v2-strict", ("areas", "literacy", "gap"), "5.00"),
    ("FX-09a", "v2-strict", ("areas", "literacy", "gap_direction"), "widening"),
    # --- FX-09b: F3 literacy 10.5, 11, 11.5
    # v1: gap 1.00, first 1.50, last 0.50, change -1.00: closing
    ("FX-09b", "v1", ("areas", "literacy", "gap"), "1.00"),
    ("FX-09b", "v1", ("areas", "literacy", "gap_first_term"), "1.50"),
    ("FX-09b", "v1", ("areas", "literacy", "gap_last_term"), "0.50"),
    ("FX-09b", "v1", ("areas", "literacy", "gap_direction"), "closing"),
    # v2: window 11 (F2), 10.5, 11, 11.5.  level 11.00, gap 5.00.  first gap (Form 2)
    # 15 - 11 = 4.00,
    # last 16 - 11.5 = 4.50, change 0.50 > 0.25: widening (the change of stage moves the range)
    ("FX-09b", "v2-strict", ("areas", "literacy", "gap"), "5.00"),
    ("FX-09b", "v2-strict", ("areas", "literacy", "gap_first_term"), "4.00"),
    ("FX-09b", "v2-strict", ("areas", "literacy", "gap_last_term"), "4.50"),
    ("FX-09b", "v2-strict", ("areas", "literacy", "gap_direction"), "widening"),
]

# (fixture, expected issue kinds with how many of each; the same under both templates)
HAND_ISSUES = {
    "FX-01": {},
    "FX-02": {},
    "FX-03a": {},
    "FX-03b": {"denominator_unexpected": 1},
    "FX-04a": {"missing_term": 1},
    "FX-04b": {"value_out_of_range": 1},
    "FX-04c": {"duplicate_card": 1},
    # History printed 13.50 (terms 12.5 x 3 average 12.50).  Weighted 27.00 follows from it.
    # Recomputed total = 52+54+50+28+24+27+26+25+13+27+25+14+12+13.5 = 390.50;
    # printed 392.50 (+1 x 2).
    # Average 390.50/30 = 13.02 recomputed, 392.50/30 = 13.08 printed.
    "FX-04d": {"printed_value_mismatch": 3},
    "FX-05": {"note_conflict": 1},
    "FX-08": {},
    "FX-09a": {},
    "FX-09b": {},
    "FX-22": {},
}

# printed text of the FX-04d mismatches: field -> (printed, recomputed)
HAND_MISMATCH_FX_04D = {
    "annual_score": ("13.50", "12.50"),
    "total_weighted": ("392.50", "390.50"),
    "annual_average": ("13.08", "13.02"),
}


def _walk(result, path):
    for step in path:
        result = result[step]
    return result


def check(key):
    """Compare the key with the hand answers. Returns a list of mismatch descriptions."""
    by_handle = {v["fixture"]: v for v in key["learners"].values() if v["fixture"]}
    problems = []
    for handle, template, path, expected in HAND:
        got = _walk(by_handle[handle]["by_template"][template], path)
        if got != expected:
            problems.append(
                f"{handle} {template} {'.'.join(path)}: key says {got!r}, hand {expected!r}"
            )
    for handle, kinds in HAND_ISSUES.items():
        for template in key["templates"]:
            issues = by_handle[handle]["by_template"][template]["issues"]
            found = {}
            for i in issues:
                found[i["kind"]] = found.get(i["kind"], 0) + 1
            if found != kinds:
                problems.append(f"{handle} {template} issues: key {found}, hand {kinds}")
    issues = by_handle["FX-04d"]["by_template"]["v1"]["issues"]
    got = {i["field"]: (i["printed"], i["recomputed"]) for i in issues}
    if got != HAND_MISMATCH_FX_04D:
        problems.append(f"FX-04d printed values: key {got}, hand {HAND_MISMATCH_FX_04D}")
    return problems
