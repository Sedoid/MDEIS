"""The rulebook of the fictional Baobab College (templates/baobab-v1.yaml), T-070.

The first group needs no database: the file loads, every value Baobab states is the value in the
methodology fixture, and everything Baobab does not define is marked a placeholder and says so in
its own text. The second group (marked `integration`) loads the file through the admin step into a
throwaway schema as a separate version, without making it the active one.

Expected values are typed by hand from data/synthetic/methodology/methodology-baobab.md
(BAO-METH-1.0): scale 0 to 100, bands A 80, B 70, C 60, D 50, E 0, pass mark 50, window of the
last 3 term scores, minimum evidence of 2 scores, moved threshold 10 points.
"""

import re
from pathlib import Path

import psycopg
import pytest
import yaml

from mdeis import admin
from mdeis.core.rules import load_rules, rules_from_content
from mdeis.core.template import load_template
from mdeis.registry.values import template_values

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / "templates"
BAOBAB = T / "baobab-v1.yaml"
V11 = T / "v1.1.yaml"
DOC = ROOT / "data" / "synthetic" / "methodology" / "methodology-baobab.md"
PLACEHOLDER_START = "Placeholder, not defined by Baobab"
OWN = "# FROM BAOBAB BAO-METH-1.0"
PLACEHOLDER = "# PLACEHOLDER: teacher to confirm"


def raw(path=BAOBAB) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def lines_with(key):
    return [
        ln for ln in BAOBAB.read_text(encoding="utf-8").splitlines()
        if ln.lstrip(" -").startswith(key + ":")
    ]  # fmt: skip


# ----------------------------------- the file loads
def test_the_file_loads_with_the_template_loader_as_baobab_college():
    t = load_template(BAOBAB)
    assert (t.institution.name, t.institution.code) == ("Baobab College", "BAO")
    assert t.template_id == "baobab-demo" and t.version == 1 and t.language == "en"
    assert t.methodology_version == "1.0"
    assert t.approved_by is None, "no person has approved these rules"
    assert t.lessons == [] and t.measure_params == []
    assert [r.id for r in t.group_minimum] == ["GM-1"], "no class view rules: Baobab has no group"


def test_every_clause_id_is_unique_and_the_areas_are_the_template_areas():
    ids = [c.id for c in load_template(BAOBAB).clauses()]
    assert len(ids) == len(set(ids)) == 33


# ----------------------------------- what Baobab states
def test_the_values_baobab_states_are_the_values_in_the_rulebook():
    t = load_template(BAOBAB)
    scale = next(r for r in t.scales if r.type is not None)
    assert (scale.type, scale.max) == ("out_of", 100)
    bands = next(r for r in t.scales if r.grade_bands is not None).grade_bands
    assert [(b.grade, b.min) for b in bands] == [
        ("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 0),
    ]  # fmt: skip
    assert next(r for r in t.scales if r.pass_mark is not None).pass_mark == 50
    trend = {}
    for r in t.trend:
        trend.update(r.model_dump(exclude={"id", "text", "method_ref"}, exclude_none=True))
    assert (trend["min_scores"], trend["window_terms"]) == (2, 3)
    assert trend["move_threshold"] == 10, "Baobab section 6.4: 10 points, not bent to fit the tool"


def test_the_values_come_through_the_rules_reader_the_tools_use():
    t = load_template(BAOBAB)
    rules = rules_from_content(t.model_dump(mode="json"), "baobab-v1", "a" * 64)
    assert rules.scale_max == 100
    assert [(g, int(low)) for g, low in rules.grade_bands] == [
        ("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 0),
    ]  # fmt: skip
    assert (rules.min_scores, rules.window_terms, int(rules.move_threshold)) == (2, 3, 10)
    assert rules.clause["move"] == "TR-2" and rules.clause["evidence"] == "TR-1"
    assert (rules.min_group, rules.min_cell, rules.bin_order) == (10, None, None)


def test_each_value_baobab_states_is_in_the_methodology_document():
    doc = DOC.read_text(encoding="utf-8")
    for fragment in (
        "| Score scale | 0 to 100, whole numbers |",
        "| Pass mark | 50 |",
        "| Window | The last 3 term scores in one subject |",
        "| Minimum evidence | 2 scores |",
        "| Moved threshold | 10 points |",
        "| Steady band | 8 points |",
        "| A | 80 to 100 |",
        "| B | 70 to 79 |",
        "| C | 60 to 69 |",
        "| D | 50 to 59 |",
        "| E | 0 to 49 |",
    ):
        assert fragment in doc, fragment


def test_every_method_ref_is_a_real_section_of_the_document():
    doc = DOC.read_text(encoding="utf-8")
    t = load_template(BAOBAB)
    refs = {c.id: c.method_ref for c in t.clauses() if c.method_ref}
    assert refs == {
        "SC-1": "5", "SC-2": "6.1", "SC-3": "6.2", "TR-1": "5", "TR-2": "6.4", "TR-3": "5",
    }  # fmt: skip
    for clause, ref in refs.items():
        assert re.search(rf"^#{{2,3}} {re.escape(ref)}[. ]", doc, re.M), (clause, ref)
    assert t.clause("EX-1").method_ref is None, "Baobab does not mention absences"


def test_the_sentences_say_the_numbers_the_rules_hold():
    t = load_template(BAOBAB)
    assert t.clause("SC-1").text == "Term scores are out of 100."
    assert "50 or more out of 100" in t.clause("SC-3").text
    assert "at least 2 term scores" in t.clause("TR-1").text
    assert "last 3 term scores" in t.clause("TR-1").text
    assert "10 points out of 100" in t.clause("TR-2").text
    assert t.clause("SC-2").text == (
        "80 to 100 is band A, 70 to 79 is B, 60 to 69 is C, 50 to 59 is D, 0 to 49 is E."
    )


def test_no_rule_text_uses_a_word_the_wording_rule_bans():
    t = load_template(BAOBAB)
    banned = [w for r in t.wording for w in (r.banned or [])]
    assert banned
    for c in t.clauses():
        if c.area == "WD":
            continue
        for word in banned:
            assert not re.search(rf"\b{re.escape(word)}\b", c.text, re.I), (c.id, word)


# ----------------------------------- the known difference
def test_the_difference_between_baobab_and_the_fixed_move_rule_is_stated_and_not_hidden():
    text = load_template(BAOBAB).clause("TR-2").text
    assert "10 points or more as moved" in text, "Baobab 6.4, as the document says it"
    assert "more than 10 points as moved" in text, "what the fixed trend tool does"
    assert "10 points or more" in DOC.read_text(encoding="utf-8")
    header = BAOBAB.read_text(encoding="utf-8")
    assert "B-06" in header and "MORE THAN" in header
    # the fixed tool still counts a move only when the change is more than the threshold
    for name in ("issues.py", "trend.py"):
        source = (ROOT / "src" / "mdeis" / "core" / name).read_text(encoding="utf-8")
        assert "change > rules.move_threshold" in source, name


# ----------------------------------- what is not Baobab's
PLACEHOLDER_RULES = [
    "WT-1", "SW-1", "CA-1", "CA-2", "CA-3", "CA-4", "CA-5", "SK-1", "SK-2", "LV-1", "LV-2",
    "LV-3", "LV-4", "TR-4", "EX-1", "GM-1",
] + [f"ST-{n}" for n in range(1, 9)]  # fmt: skip


def test_every_rule_baobab_does_not_define_says_so_in_its_own_text_once():
    t = load_template(BAOBAB)
    for rule_id in PLACEHOLDER_RULES:
        text = t.clause(rule_id).text
        assert text.startswith(PLACEHOLDER_START), rule_id
        assert text.count("Baobab") == 1, f"{rule_id} must not claim Baobab said anything"


def test_the_rules_baobab_does_define_do_not_start_with_the_placeholder_words():
    t = load_template(BAOBAB)
    for rule_id in ("SC-1", "SC-2", "SC-3", "TR-1", "TR-2", "TR-3"):
        assert not t.clause(rule_id).text.startswith(PLACEHOLDER_START), rule_id
    assert "placeholder" in t.clause("TR-3").text.lower(), "the held-subject part is not Baobab's"


def test_every_value_in_the_file_carries_its_own_mark():
    for key in ("type", "max", "pass_mark", "min_scores", "window_terms", "move_threshold"):
        for line in lines_with(key):
            if key == "type" and "out_of" not in line:
                continue
            assert OWN in line, line
    for key in ("min_terms", "missing_term", "gap_direction_threshold", "absences",
                "min_group_size", "foundation_areas", "template_id", "version", "language",
                "start_date", "review_date"):  # fmt: skip
        for line in lines_with(key):
            assert PLACEHOLDER in line, line
    header = BAOBAB.read_text(encoding="utf-8")
    for needed in ("coefficients:", "stage_weights:", "calculations:", "stage_expectations:",
                   "levels:", "wording:"):  # fmt: skip
        before = header.split("\n" + needed)[0].splitlines()[-4:]
        assert any(PLACEHOLDER in ln for ln in before), needed


def test_one_range_is_used_for_every_form_and_area_so_no_difference_can_be_read_into_them():
    ranges = {tuple(r.range) for r in load_template(BAOBAB).stage_expectations}
    assert ranges == {(50, 80)}


def test_the_steady_band_is_left_out_because_no_clause_of_this_schema_holds_it():
    data = raw()
    assert "measure_params" not in data
    held = [v for vals in template_values(data).values() for v in vals]
    assert "8" not in held, "no template value is Baobab's steady band of 8"


def test_the_other_four_rulebooks_do_not_mention_baobab_and_the_two_schools_differ():
    for name in ("v1.yaml", "v1.1.yaml", "v2-strict.yaml", "v2.1-strict.yaml"):
        assert "Baobab" not in (T / name).read_text(encoding="utf-8"), name
    ours, other = load_template(BAOBAB), load_template(V11)
    assert next(r.max for r in other.scales if r.max) == 20
    assert next(r.max for r in ours.scales if r.max) == 100
    assert next(r.pass_mark for r in other.scales if r.pass_mark) == 10
    assert next(r.pass_mark for r in ours.scales if r.pass_mark) == 50
    assert next(r.min_scores for r in other.trend if r.min_scores) == 3
    assert next(r.min_scores for r in ours.trend if r.min_scores) == 2


# ----------------------------------- the database (integration)
@pytest.fixture
def url(monkeypatch, built_schema):
    _, scoped = built_schema
    monkeypatch.setenv("DATABASE_URL", scoped)
    return scoped


def load(*args):
    return admin.main(["load-criteria", *map(str, args), "--no-embed", "--by", "Ada Teacher"])


@pytest.mark.integration
def test_it_loads_as_a_separate_version_and_does_not_change_the_active_rulebook(url):
    assert load(V11) == 0
    with psycopg.connect(url) as c:
        assert load_rules(c).version == "v1.1"
        before = c.execute(
            "SELECT version_name, sha256, active FROM templates ORDER BY version_name"
        ).fetchall()
    assert load(BAOBAB, "--no-activate") == 0
    with psycopg.connect(url) as c:
        assert load_rules(c).version == "v1.1", "the active rulebook is the one it was"
        rows = c.execute(
            "SELECT version_name, active, approved_by, institution_code, methodology_version "
            "FROM templates ORDER BY version_name"
        ).fetchall()
        assert rows == [
            ("baobab-v1", False, None, "BAO", "1.0"),
            ("v1.1", True, None, "OCC", "2.0"),
        ]
        n = c.execute("SELECT count(*) FROM clauses WHERE version_name = 'baobab-v1'").fetchone()
        assert n[0] == len(load_template(BAOBAB).clauses())
        again = c.execute(
            "SELECT version_name, sha256, active FROM templates WHERE version_name = 'v1.1'"
        ).fetchall()
        assert again == [r for r in before if r[0] == "v1.1"], "v1.1 was not touched"
        baobab = load_rules(c, "baobab-v1")
        assert baobab.scale_max == 100 and baobab.min_scores == 2 and baobab.window_terms == 3
        assert int(baobab.move_threshold) == 10 and baobab.clause["move"] == "TR-2"


@pytest.mark.integration
def test_the_four_existing_rulebooks_still_load_beside_it_and_none_is_changed(url):
    four = [T / n for n in ("v2-strict.yaml", "v1.yaml", "v2.1-strict.yaml", "v1.1.yaml")]
    assert load(*four) == 0
    names = ["v1", "v1.1", "v2-strict", "v2.1-strict"]
    query = (
        "SELECT version_name, sha256, content::text, active FROM templates "
        "WHERE version_name = ANY(%s)"
    )

    def by_name(c):  # by name, not by position: the database's text order puts punctuation oddly
        return {r[0]: r[1:] for r in c.execute(query, (names,)).fetchall()}

    with psycopg.connect(url) as c:
        before = by_name(c)
    assert sorted(before) == names
    assert {n: before[n][2] for n in names} == {
        "v1": False, "v1.1": True, "v2-strict": False, "v2.1-strict": False,
    }  # fmt: skip
    assert load(BAOBAB, "--no-activate") == 0
    with psycopg.connect(url) as c:
        assert by_name(c) == before, "the four are exactly as they were; v1.1 is still active"
        assert c.execute("SELECT count(*) FROM templates").fetchone()[0] == len(names) + 1
        assert load_rules(c).version == "v1.1"


@pytest.mark.integration
def test_loading_it_again_changes_nothing(url):
    assert load(V11, BAOBAB, "--no-activate") == 0
    with psycopg.connect(url) as c:
        first = c.execute(
            "SELECT version_name, sha256, content::text FROM templates ORDER BY version_name"
        ).fetchall()
    assert load(BAOBAB, "--no-activate") == 0
    with psycopg.connect(url) as c:
        second = c.execute(
            "SELECT version_name, sha256, content::text FROM templates ORDER BY version_name"
        ).fetchall()
    assert second == first


@pytest.mark.integration
def test_its_clauses_are_stored_with_their_methodology_section_and_values(url):
    assert load(BAOBAB) == 0
    with psycopg.connect(url) as c:
        rows = {
            r[0]: r[1:]
            for r in c.execute(
                "SELECT clause_id, area, method_ref, params FROM clauses "
                "WHERE version_name = 'baobab-v1' AND clause_id IN ('TR-1', 'TR-2', 'SC-3')"
            )
        }
    assert rows["TR-2"][:2] == ("TR", "6.4") and rows["TR-2"][2]["move_threshold"] == 10
    assert rows["TR-1"][2]["min_scores"] == 2 and rows["TR-1"][2]["window_terms"] == 3
    assert rows["SC-3"][:2] == ("SC", "6.2") and rows["SC-3"][2]["pass_mark"] == 50


@pytest.mark.integration
def test_a_template_with_a_measure_params_section_loads_and_its_rules_are_stored_and_citable(
    url, tmp_path
):
    data = raw(V11)
    data["measure_params"] = [
        {
            "id": "MP-1", "name": "moved_at_or_above", "value_type": "integer", "value": 10,
            "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.4",
            "confirmed_by": None,
            "text": "The pattern is moved up when the change is 10 points or more.",
        },
        {
            "id": "MP-2", "name": "checklist_order", "value_type": "names",
            "value": ["Starting", "Practising"], "unit": "none",
            "source_document": "SAV-METH-1.0", "method_ref": "3", "confirmed_by": "Ada Teacher",
            "text": "Checklist levels: Starting and Practising, in that order.",
        },
    ]  # fmt: skip
    path = tmp_path / "mp-test.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert load(path, "--no-activate") == 0
    with psycopg.connect(url) as c:
        got = {
            r[0]: r[1:]
            for r in c.execute(
                "SELECT clause_id, area, text, method_ref, params FROM clauses "
                "WHERE version_name = 'mp-test' AND area = 'MP'"
            )
        }
        content = c.execute(
            "SELECT content FROM templates WHERE version_name = 'mp-test'"
        ).fetchone()[0]
    assert sorted(got) == ["MP-1", "MP-2"]
    area, text, ref, params = got["MP-1"]
    assert (area, ref) == ("MP", "6.4") and text.endswith("10 points or more.")
    assert params["value"] == 10 and params["confirmed_by"] is None and params["unit"] == "points"
    assert got["MP-2"][3]["value"] == ["Starting", "Practising"]
    assert got["MP-2"][3]["confirmed_by"] == "Ada Teacher"
    # the static check's duplicate rule now sees the value from the stored template
    assert template_values(content)["MP-1"] == ["10"]
    assert "MP-2" not in template_values(content)
