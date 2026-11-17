"""Which sections the build reads, which it skips, and why (T-071). Pure code: the Baobab fixture
and templates/baobab-v1.yaml are read from disk, there is no database and no model.

Expected values are read by hand from the fixture: Baobab BAO-METH-1.0, version 1.0, defines its
measures in 6.2 (band), 6.3 (spread) and 6.4 (change); baobab-v1.yaml says methodology_version 1.0
and its clauses carry method_ref 6.2 (SC-3), 6.4 (TR-2), 6.1 (SC-2) and 5 (TR-1, TR-3)."""

import copy

from build_kit import RULES, baobab_content, inputs, occ_content, parsed

from mdeis.ingest import methodology
from mdeis.measure_build import engine


def ids(listed):
    return [s["section_id"] for s in listed]


def test_the_document_id_and_version_are_read_from_the_title_block():
    assert engine.document_info(parsed().sections, RULES) == ("BAO-METH-1.0", "1.0")


def test_a_document_without_a_version_gives_none_not_a_guess():
    stripped = [s for s in parsed().sections if s.id]
    assert engine.document_info(stripped, RULES) == (None, None)


def test_a_first_methodology_has_every_numbered_section_added_and_only_measures_are_built():
    plan = engine.plan(inputs(), RULES)
    assert plan["status"] == "ok"
    assert plan["document_ref"] == "BAO-METH-1.0" and plan["document_version"] == "1.0"
    built, skipped = ids(plan["sections"]), ids(plan["skipped"])
    assert "6.3" in built, "the steady rule has no template clause: it is built"
    assert not set(built) & set(skipped)
    assert {"6.2", "6.4"} <= set(skipped), "SC-3 and TR-2 are the clauses of the same version"
    # a section that is not a measure is neither built nor skipped, and is listed as such
    assert "3.2" in plan["not_measures"] and "1" in plan["not_measures"]
    assert all(s["change"] == "added" for s in plan["sections"])


def test_a_skipped_section_names_its_clause_the_fixed_tool_and_the_build_anyway_option():
    plan = engine.plan(inputs(), RULES)
    by_id = {s["section_id"]: s for s in plan["skipped"]}
    assert by_id["6.4"]["clause_ids"] == ["TR-2"] and by_id["6.4"]["tool"] == "compute_trend"
    assert by_id["6.2"]["clause_ids"] == ["SC-3"] and by_id["6.2"]["tool"] == "get_criterion"
    assert all(s["build_anyway"] is False and s["reason"] for s in plan["skipped"])
    assert plan["build_anyway"] == []


def test_build_it_anyway_builds_the_named_section_and_records_the_choice():
    plan = engine.plan(inputs(), RULES, build_anyway=["6.4"])
    assert "6.4" in ids(plan["sections"]) and "6.4" not in ids(plan["skipped"])
    assert plan["build_anyway"] == ["6.4"]
    assert "6.2" in ids(plan["skipped"]), "only the named section is released"


def test_build_it_anyway_for_a_section_that_is_not_skipped_changes_nothing():
    plain = engine.plan(inputs(), RULES)
    anyway = engine.plan(inputs(), RULES, build_anyway=["6.3", "9.9"])
    assert ids(plain["sections"]) == ids(anyway["sections"])
    assert anyway["build_anyway"] == [], "only a section that was skipped can be built anyway"


def test_nothing_is_skipped_when_the_template_is_for_another_version_of_the_document():
    other = baobab_content()
    other["methodology_version"] = "2.0"
    plan = engine.plan(inputs(other), RULES)
    assert plan["skipped"] == [] and {"6.2", "6.3", "6.4"} <= set(ids(plan["sections"]))


def test_nothing_is_skipped_when_the_document_has_no_version():
    sections = [s for s in parsed().sections if s.id]
    plan = engine.plan(inputs(sections=sections), RULES)
    assert plan["document_version"] is None and plan["skipped"] == []


def test_a_template_for_another_school_skips_nothing_of_this_document():
    # v1.1 says methodology_version 2.0; its TR-1..3 and EX-1 carry bare numbers (6.4, 6.3 ...).
    plan = engine.plan(inputs(occ_content(), version_name="v1.1"), RULES)
    assert plan["skipped"] == []


def test_a_measure_params_clause_never_stands_in_for_a_section():
    content = baobab_content()
    content["measure_params"] = [{
        "id": "MP-1", "name": "steady_at_most", "value_type": "integer", "value": 8,
        "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.3",
        "confirmed_by": "Ms Teacher",
        "text": "The pattern is steady when the spread is at most 8 points.",
    }]  # fmt: skip
    plan = engine.plan(inputs(content), RULES)
    assert "6.3" in ids(plan["sections"]), "a generated clause is not a fixed tool"


def test_a_later_version_builds_only_what_the_stored_diff_names():
    issues = [
        {"kind": methodology.ADDED, "ref_id": "6.3"},
        {"kind": methodology.CHANGED, "ref_id": "6.4"},
        {"kind": methodology.CHANGED, "ref_id": "3.2"},
        {"kind": methodology.REMOVED, "ref_id": "6.9"},
        {"kind": methodology.REF_CHANGED, "ref_id": "TR-2"},
    ]
    other = baobab_content()
    other["methodology_version"] = "0.9"
    plan = engine.plan(inputs(other, issues=issues, first=False), RULES)
    assert sorted(ids(plan["sections"])) == ["6.3", "6.4"]
    changes = {s["section_id"]: s["change"] for s in plan["sections"]}
    assert changes == {"6.3": "added", "6.4": "changed"}
    assert plan["not_measures"] == ["3.2"], "a changed section that is not a measure is listed"
    assert plan["removed"] == ["6.9"]


def test_an_unchanged_later_version_has_nothing_to_build():
    plan = engine.plan(inputs(issues=[], first=False), RULES)
    assert plan["sections"] == [] and plan["skipped"] == [] and plan["removed"] == []


def test_a_section_points_at_the_sections_that_define_its_values():
    plan = engine.plan(inputs(), RULES, build_anyway=["6.4"])
    moved = next(s for s in plan["sections"] if s["section_id"] == "6.4")
    assert moved["references"] == ["3.2"]
    assert moved["reference_text"].keys() == {"3.2"}
    assert "Moved threshold | 10 points" in moved["reference_text"]["3.2"]
    assert moved["text"].startswith("**Inputs.**") and moved["sha256"]


def test_a_pointer_to_a_section_that_does_not_exist_or_to_itself_is_left_out():
    sections = [
        methodology.Section("1", "A", 1, 1, "Defined as x = 1. See section 9 and section 1 and "
                            "sections 2 and 3.", "a" * 64),
        methodology.Section("2", "B", 1, 1, "b", "b" * 64),
        methodology.Section("3", "C", 1, 1, "c", "c" * 64),
    ]  # fmt: skip
    refs = engine.references("1", sections, RULES)
    assert refs == ["2", "3"]


def test_the_plan_is_the_same_every_time_and_changes_nothing_it_was_given():
    given = inputs()
    before = copy.deepcopy(given.content)
    first, second = engine.plan(given, RULES), engine.plan(given, RULES)
    assert first == second
    assert given.content == before


def test_the_plan_carries_what_the_graph_needs_so_the_orchestrator_reads_no_file():
    from build_kit import SANDBOX

    view = engine.plan(inputs(), RULES, sandbox=SANDBOX)["build"]
    assert view["repair_rounds"] == RULES.repair_rounds
    assert view["known_settings"] == [h.setting for h in RULES.homes]
    assert "points" in view["units"] and "none" in view["units"]
    assert view["record_fields"] == ["score"]
    assert view["sandbox"]["entry_function"] == "measure"
    assert view["sandbox"]["values_parameter"] == "params"
    assert view["sandbox"]["allowed_modules"]["fractions"] == ["Fraction"]
    assert view["sandbox"]["allowed_literals"] == [0, 1]
    assert engine.plan(inputs(), RULES)["build"]["sandbox"] is None
