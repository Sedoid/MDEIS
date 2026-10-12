"""The practice school and its answer key (T-002, part of T-032).

These tests need no database and none of the system's code. They check that the generator is
repeatable, that the cohort has the shape the brief asks for, that every fixture has a worked
answer, that every planted problem is really in the data, and that nothing looks like a real
person's details.
"""

import ast
import json
import re
import subprocess
import sys
from collections import Counter
from fractions import Fraction

import yaml
from synthetic import cohort, generate, hand_checks, scan

FIXTURE_HANDLES = {
    "FX-01", "FX-02", "FX-03a", "FX-03b", "FX-04a", "FX-04b", "FX-04c", "FX-04d",
    "FX-05", "FX-08", "FX-09a", "FX-09b", "FX-22",
}  # fmt: skip
TEMPLATES = ("v1", "v2-strict")


def by_handle(key):
    return {v["fixture"]: (ref, v) for ref, v in key["learners"].items() if v["fixture"]}


# --------------------------------------------------------------------------- repeatable
def test_two_builds_in_one_process_are_identical(templates_dir, files):
    again, _, _ = generate.build_outputs(42, templates_dir)
    assert again == files


def test_a_different_seed_gives_a_different_cohort(templates_dir, files):
    other, _, _ = generate.build_outputs(43, templates_dir)
    assert other["cohort/cards.json"] != files["cohort/cards.json"]
    assert other["cohort/learners.json"] != files["cohort/learners.json"]


def test_two_runs_of_the_script_write_identical_files(root, tmp_path):
    runs = []
    for k, hash_seed in enumerate(("1", "2")):  # also proves set order does not leak in
        out = tmp_path / f"run{k}"
        subprocess.run(
            [sys.executable, "synthetic/generate.py", "--seed", "42", "--out", str(out)],
            cwd=root, check=True, capture_output=True, env={"PYTHONHASHSEED": hash_seed},
        )  # fmt: skip
        runs.append(out)
    names = sorted(p.relative_to(runs[0]) for p in runs[0].rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(runs[1]) for p in runs[1].rglob("*") if p.is_file())
    assert "MANIFEST.sha256" in {n.name for n in names}
    for n in names:
        assert (runs[0] / n).read_bytes() == (runs[1] / n).read_bytes(), n
    listed = (runs[0] / "MANIFEST.sha256").read_text().splitlines()
    assert len(listed) == len(names) - 1  # every file but the manifest itself


# --------------------------------------------------------------------------- shape
def test_about_sixty_learners_twenty_in_each_form(learners):
    assert len(learners) == 60
    assert Counter(ln["stage"] for ln in learners) == {"Form 1": 20, "Form 2": 20, "Form 3": 20}


def test_names_and_references_are_unique_and_in_the_cards_style(learners):
    refs = [ln["ref"] for ln in learners]
    assert len(set(refs)) == len(refs)
    assert all(re.fullmatch(r"OCC/\d{4}/\d{4}", r) for r in refs)
    assert len({ln["name"] for ln in learners}) == len(learners)
    assert [ln["gen_id"] for ln in learners] == [f"L-{k:04d}" for k in range(1, 61)]
    # the enrolment year in the reference matches the form the learner is in now
    for ln in learners:
        assert int(ln["ref"].split("/")[1]) == ln["entry_year"]


def test_cards_copy_the_real_cards_shape(learners, rules):
    r = rules["v1"]
    cards = [c for ln in learners for c in ln["cards"] if c["printable"]]
    assert cards
    for c in cards:
        assert [s["name"] for s in c["subjects"]] == list(r.subjects)  # 14, in the template order
        assert len(c["subjects"]) == 14
        for s in c["subjects"]:
            assert s["coef"] == r.subjects[s["name"]]
            assert len(s["terms"]) == 3
            for t in s["terms"]:
                assert t is None or (t * 2).denominator == 1  # steps of 0.5
    years = {
        ln["gen_id"]: len([c for c in ln["cards"] if c["card_version"] == 1]) for ln in learners
    }
    forms = {"Form 1": 1, "Form 2": 2, "Form 3": 3}
    for ln in learners:
        assert years[ln["gen_id"]] == forms[ln["stage"]]  # up to three years of cards


def test_clean_cards_print_what_the_terms_add_up_to(learners):
    wrong = 0
    for ln in learners:
        for c in ln["cards"]:
            for s in c["subjects"]:
                if c["printable"] and all(t is not None for t in s["terms"]):
                    if s["printed_annual"] != sum(s["terms"]) / 3:
                        wrong += 1
    assert wrong == cohort.INJECTION_PLAN.count("printed_value_mismatch") + 1  # plus FX-04d


def test_the_flat_records_match_the_cards(files, learners):
    lines = files["cohort/records.csv"].decode().splitlines()
    assert lines[0].startswith("learner_ref,card_id,card_version,year,stage,term,subject")
    expected = sum(3 * len(c["subjects"]) for ln in learners for c in ln["cards"])
    # records-only fixtures have fewer terms; count what the cards actually hold
    held = sum(len(s["terms"]) for ln in learners for c in ln["cards"] for s in c["subjects"])
    assert len(lines) - 1 == held <= expected
    assert any(line.split(",")[8] == "" for line in lines[1:])  # a blank score is an empty field


def test_dates_of_birth_are_school_age(learners):
    years = [int(c["dob"][:4]) for ln in learners for c in ln["cards"] if c["printable"]]
    assert min(years) >= 2010 and max(years) <= 2014


# --------------------------------------------------------------------------- template is the source
def test_subjects_coefficients_and_bands_come_from_the_template(templates_dir, rules):
    raw = yaml.safe_load((templates_dir / "v1.yaml").read_text())
    assert list(rules["v1"].subjects) == list(raw["coefficients"][0]["subjects"])
    assert [(b["grade"], b["min"]) for b in raw["scales"][1]["grade_bands"]] == [
        (g, int(m)) for g, m in rules["v1"].grade_bands
    ]


def test_the_generator_does_not_use_the_systems_code(root):
    bad = []
    for path in sorted((root / "synthetic").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            bad += [(path.name, n) for n in names if n.split(".")[0] in ("mdeis", "psycopg")]
    assert bad == []


# --------------------------------------------------------------------------- fixtures and the key
def test_every_fixture_exists_and_has_an_answer_under_both_templates(key):
    found = by_handle(key)
    assert set(found) == FIXTURE_HANDLES
    for handle, (_, entry) in found.items():
        assert set(entry["by_template"]) == set(TEMPLATES), handle
        for t in TEMPLATES:
            result = entry["by_template"][t]
            assert result["subjects"] and result["areas"], (handle, t)
            assert entry["tests"], handle


def test_the_hand_worked_answers_agree_with_the_key(key):
    assert hand_checks.check(key) == []


def test_the_hand_check_notices_a_wrong_key(key):
    tampered = json.loads(json.dumps(key))
    ref, entry = by_handle(tampered)["FX-08"]
    entry["by_template"]["v1"]["areas"]["literacy"]["gap"] = "0.71"
    assert any("FX-08" in p for p in hand_checks.check(tampered))


def test_the_same_data_gives_different_answers_under_the_stricter_template(key):
    """E-06 and E-10 need the two templates to disagree on the same data."""
    _, entry = by_handle(key)["FX-01"]
    v1, v2 = (entry["by_template"][t]["subjects"]["English"] for t in TEMPLATES)
    assert (v1["change"], v2["change"]) == ("-2.00", "-3.00")
    assert "SW-1" in v2["clauses"] and "SW-1" not in v1["clauses"]
    _, amina = by_handle(key)["FX-08"]
    gaps = [amina["by_template"][t]["areas"]["literacy"]["gap"] for t in TEMPLATES]
    assert gaps[0] != gaps[1]
    v1_clauses = amina["by_template"]["v1"]["areas"]["literacy"]["clauses"]
    assert {"ST-3", "CA-5", "TR-4", "SK-1"} <= set(v1_clauses)  # ids are read from the template


def test_a_blocked_subject_gets_no_trend(key):
    _, entry = by_handle(key)["FX-04a"]
    english = entry["by_template"]["v1"]["subjects"]["English"]
    assert english["label"] == "blocked"
    assert "change" not in english and "level" not in english
    assert entry["by_template"]["v1"]["blocked_subjects"] == ["English"]


# --------------------------------------------------------------------------- planted problems
def test_the_planted_problems_are_the_planned_number(problems):
    counts = Counter(p["kind"] for p in problems["injected"])
    assert counts == {  # the numbers in the brief, typed here so the plan cannot mark its own work
        "missing_term": 3,
        "value_out_of_range": 2,
        "duplicate_card": 2,
        "printed_value_mismatch": 3,
        "note_conflict": 3,
    }


def test_every_planted_problem_is_found_in_the_key(key, problems):
    for p in problems["injected"]:
        issues = key["learners"][p["learner_ref"]]["by_template"]["v1"]["issues"]
        same_kind = [i for i in issues if i["kind"] == p["kind"]]
        assert same_kind, p
        if p["kind"] in ("missing_term", "value_out_of_range"):
            assert any(
                i["subject"] == p["subject"] and i["term"] == p["term"] for i in same_kind
            ), p
        elif p["kind"] == "note_conflict":
            assert any(i["subject"] == p["subject"] for i in same_kind), p
        elif p["kind"] == "printed_value_mismatch":
            assert any(
                i.get("subject") == p["subject"] and i["field"] == "annual_score" for i in same_kind
            ), p
        elif p["kind"] == "duplicate_card":
            assert any(i["cards"] == sorted(p["card_id"]) for i in same_kind), p


def test_the_issues_are_only_the_planted_ones_plus_the_fixtures(key, problems):
    planted = {(p["learner_ref"], p["kind"]) for p in problems["injected"]}
    fixture_refs = {ref for ref, v in key["learners"].items() if v["fixture"]}
    for ref, entry in key["learners"].items():
        if ref in fixture_refs:
            continue
        kinds = {i["kind"] for i in entry["by_template"]["v1"]["issues"]}
        assert {(ref, k) for k in kinds} <= planted, (ref, kinds)


# --------------------------------------------------------------------------- the hidden pattern
def test_about_a_third_of_form_3_has_the_pattern_and_it_is_in_the_data(key, problems):
    hidden = set(problems["hidden_pattern"]["learners"])
    form3 = {ref for ref, v in key["learners"].items() if v["stage"] == "Form 3"}
    assert hidden <= form3
    assert 0.25 <= len(hidden) / len(form3) <= 0.45
    for ref in form3:
        literacy = key["learners"][ref]["by_template"]["v1"]["areas"]["literacy"]
        if literacy["level_name"] == "blocked":
            continue
        assert (literacy["level_name"] == "below_expected_range") == (ref in hidden), ref
        if ref in hidden:
            assert Fraction(literacy["level"]) < 12  # in the Form 1 range, under the Form 3 floor
            assert Fraction(literacy["level"]) >= 10


def test_nothing_in_the_cohort_files_tags_the_pattern_or_the_fixtures(files):
    words = ("stall", "hidden", "pattern", "planted", "fixture", "FX-", "Form 1 range")
    for path, data in files.items():
        if path.startswith("cohort/"):
            text = data.decode()
            assert not [w for w in words if w in text], path


# --------------------------------------------------------------------------- Amina, personal data
def test_amina_is_one_learner_and_nothing_in_her_cards_labels_her(learners, templates_dir):
    aminas = [ln for ln in learners if ln["name"].split()[0] == "Amina"]
    assert len(aminas) == 1 and aminas[0]["stage"] == "Form 3"
    banned = yaml.safe_load((templates_dir / "v1.yaml").read_text())["wording"][0]["banned"]
    labels = banned + ["struggling", "not keeping up", "behind", "poor"]
    for ln in learners:
        text = " ".join(
            [s["remark"] for c in ln["cards"] for s in c["subjects"]]
            + [c.get("conduct", "") for c in ln["cards"]]
        ).lower()
        hits = [w for w in labels if re.search(rf"\b{re.escape(w)}\b", text)]
        assert hits == [], (ln["name"], hits)


def test_the_scan_finds_no_real_looking_personal_data(root, files, tmp_path):
    for path, data in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    assert scan.scan_folder(tmp_path, root / "templates" / "v1.yaml") == []


def test_the_scan_does_catch_what_it_looks_for():
    text = "mail jo@example.org phone +237 677 123 456 id 123456789012 http://x.y"
    kinds = {k for _, k, _ in scan.scan_text("t", text)}
    assert {"email", "phone number", "long digit run", "web address"} <= kinds
    assert scan.scan_text("t", "OCC/2024/0211 C-0049-2025-1 2024-05-03 392.50") == []
    # a UNESCO source address in the methodology is fine; anywhere else, or any other site, is not
    unesco = "see https://www.uis.unesco.org/en/gaml"
    assert scan.scan_text("methodology/x.md", unesco) == []
    assert scan.scan_text("cohort/x.json", unesco)
    assert scan.scan_text("methodology/x.md", "see https://example.org/jo")


def test_a_name_off_the_invented_lists_is_reported(root):
    bad = [{"name": "Zed Quux", "school_ref": "OCC/2024/0001"}]
    assert scan.scan_names(bad, root / "templates" / "v1.yaml")


def test_injected_remark_is_present_for_the_instruction_fixture(learners):
    (fx22,) = [ln for ln in learners if ln["fixture"] == "FX-22"]
    remarks = [s["remark"] for c in fx22["cards"] for s in c["subjects"]]
    assert cohort.INJECTION_REMARK in remarks


def test_generated_output_sits_where_it_should(files):
    assert set(files) == {
        "cohort/learners.json", "cohort/cards.json", "cohort/records.csv",
        "truth/answer_key.json", "truth/injected_problems.json", "truth/fixtures.json",
    }  # fmt: skip


def test_fixtures_md_covers_every_fixture_and_the_core_tests(root):
    text = (root / "data" / "synthetic" / "FIXTURES.md").read_text()
    for handle in FIXTURE_HANDLES:
        assert f"| {handle} |" in text, handle
    for test_id in ("E-01", "E-02", "E-03", "E-04", "E-06", "E-09", "E-10"):
        assert test_id in text, test_id
