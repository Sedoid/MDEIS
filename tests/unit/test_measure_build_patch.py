"""Template patches (T-071): a patch is data (proposed measure_params clauses), stored with a hash,
and loaded as a NEW template version by the admin step. These tests cover the pure part: the
merge, the hash, the refusals and the file text. No database.

Expected values are hand-made: merging one clause into baobab-v1 adds that clause, one change_log
line and a version number one higher, and changes nothing else."""

import copy

import pytest
import yaml
from build_kit import MOVED_QUOTE, baobab_content, settled_for_moved

from mdeis.core.template import parse_template
from mdeis.measure_build import patch as patches

NOTE = "Added by a confirmed measure."


def one_patch(teacher="Ms Teacher", patch_id="TP-000001"):
    rules = settled_for_moved()["mp_rules"]
    body = {"base_template_version": "baobab-v1", "measure_params": rules}
    return {
        "patch_id": patch_id, "measure_params": rules, "teacher": teacher,
        "sha256": patches.patch_sha256(body), "body": body,
    }  # fmt: skip


def merge(patch_list=None, base=None, version=2, today="2026-11-20"):
    return patches.merge_patches(
        base if base is not None else baobab_content(),
        patch_list if patch_list is not None else [one_patch()],
        new_version=version, today=today, note=NOTE,
    )


def test_a_merge_adds_the_clauses_one_change_log_line_and_a_version_and_nothing_else():
    base = baobab_content()
    merged = merge(base=copy.deepcopy(base))
    differing = {k for k in base if base[k] != merged[k]} | (set(merged) - set(base))
    assert differing == {"version", "change_log", "measure_params"}
    assert merged["version"] == base["version"] + 1
    assert merged["change_log"] == [
        *base["change_log"], {"version": 2, "date": "2026-11-20", "note": NOTE},
    ]  # fmt: skip
    (rule,) = merged["measure_params"]
    assert rule["id"] == "MP-1" and rule["value"] == 10 and rule["unit"] == "points"
    assert rule["text"] == MOVED_QUOTE and rule["source_document"] == "BAO-METH-1.0"


def test_the_teacher_name_is_copied_from_the_registry_row_into_confirmed_by():
    (rule,) = merge([one_patch(teacher="Ms Teacher")])["measure_params"]
    assert rule["confirmed_by"] == "Ms Teacher"
    template = parse_template(merge([one_patch(teacher="Mr Other")]))
    assert template.measure_params[0].confirmed_by == "Mr Other"


def test_the_merged_template_is_one_the_template_loader_accepts():
    template = parse_template(merge())
    assert [r.id for r in template.measure_params] == ["MP-1"]
    assert template.version == 2 and template.approved_by is None, "no approver is added"


def test_the_base_it_was_given_is_not_changed():
    base = baobab_content()
    before = copy.deepcopy(base)
    merge(base=base)
    assert base == before


def test_a_patch_that_already_carries_a_confirmation_is_refused():
    edited = one_patch()
    edited["measure_params"][0]["confirmed_by"] = "Someone Who Typed This"
    with pytest.raises(patches.PatchRefused, match="confirmed_by"):
        merge([edited])


def test_a_patch_whose_text_no_longer_matches_its_hash_is_refused():
    patch = one_patch()
    patch["body"]["measure_params"][0]["value"] = 9  # edited by hand after it was stored
    with pytest.raises(patches.PatchRefused, match="hash"):
        patches.verify(patch)


def test_a_hash_is_of_the_canonical_text_so_key_order_does_not_matter():
    a = {"b": 1, "a": [1, 2], "base_template_version": "x"}
    b = {"base_template_version": "x", "a": [1, 2], "b": 1}
    assert patches.canonical_text(a) == patches.canonical_text(b)
    assert patches.patch_sha256(a) == patches.patch_sha256(b)
    assert len(patches.patch_sha256(a)) == 64


def test_two_patches_with_the_same_clause_number_are_refused():
    with pytest.raises(patches.PatchRefused, match="MP-1"):
        merge([one_patch(patch_id="TP-000001"), one_patch(patch_id="TP-000002")])


def test_two_patches_that_give_one_setting_twice_are_refused_by_the_template_rules():
    second = one_patch(patch_id="TP-000002")
    second["measure_params"][0]["id"] = "MP-2"
    second["measure_params"][0]["text"] = "The pattern is moved up when the change is 10 points."
    with pytest.raises(patches.PatchRefused, match="twice"):
        merge([one_patch(), second])


def test_a_batch_of_two_patches_is_one_version():
    second = one_patch(patch_id="TP-000002", teacher="Mr Other")
    second["measure_params"][0].update(
        id="MP-2", name="steady_at_most", value=8,
        text="The pattern is steady when the spread is at most 8 points.",
    )  # fmt: skip
    merged = merge([one_patch(), second])
    assert [r["id"] for r in merged["measure_params"]] == ["MP-1", "MP-2"]
    assert [r["confirmed_by"] for r in merged["measure_params"]] == ["Ms Teacher", "Mr Other"]
    assert len(merged["change_log"]) == len(baobab_content()["change_log"]) + 1
    parse_template(merged)


def test_a_rule_the_template_validator_refuses_stops_the_merge_with_its_reason():
    bad = one_patch()
    bad["measure_params"][0]["text"] = "The pattern is moved up when the change is large."
    with pytest.raises(patches.PatchRefused, match="does not say the value"):
        merge([bad])


def test_a_merge_needs_at_least_one_patch_and_a_valid_date():
    with pytest.raises(patches.PatchRefused):
        merge([])
    with pytest.raises(patches.PatchRefused, match="date"):
        merge(today="20 November")


def test_the_next_version_is_one_more_than_the_largest_loaded_number():
    assert patches.next_version(["1", "2", "10", "x", None]) == 11
    assert patches.next_version([]) == 1


def test_the_file_text_loads_back_into_the_same_template():
    merged = merge()
    text = patches.render_yaml(merged, header=["Made by apply-template-patch."])
    assert text.startswith("# Made by apply-template-patch.")
    again = parse_template(yaml.safe_load(text))
    assert again.model_dump(mode="json") == parse_template(merged).model_dump(mode="json")


def test_every_added_value_is_listed_for_the_admin_to_read():
    lines = patches.describe_added(baobab_content(), merge())
    assert len(lines) == 1
    for word in ("MP-1", "moved_at_or_above", "10", "points", "Ms Teacher", "BAO-METH-1.0", "6.4"):
        assert word in lines[0]
