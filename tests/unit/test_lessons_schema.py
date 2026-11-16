"""The edit contract: what the model may output, and the hand-written validator (T-060, slice 1).

No JSON-schema library is on the approved list, so the validator is plain code. The schema is also
held as a dict, and a small reader in this file (just the parts of JSON Schema the dict uses) checks
that the dict and the validator give the same answer on every case below.
"""

import copy
import json
import re

import pytest
from lessonkit import edit_mapping

from mdeis.lessons.edits import EDIT_SCHEMA, EDIT_TYPES, Edit, parse_edits


def output(*edits):
    return {"edits": list(edits)}


def accepted(raw):
    return parse_edits(raw).error is None


# --------------------------------------------------------------------------- the accepted shapes
def test_a_valid_edit_list_is_accepted_as_a_dict_or_as_json_text():
    raw = output(edit_mapping())
    for given in (raw, json.dumps(raw)):
        result = parse_edits(given)
        assert result.error is None
        assert result.edits == (
            Edit(
                id="e1",
                type="reword",
                anchor_quote="Ask what the passage is about.",
                replacement="Ask what the passage tells us and why.",
                finding_ids=("CF-a1b2c3d4",),
                resource_ids=(),
                suggestion="Give learners a question that asks for a reason.",
            ),
        )


def test_an_empty_edit_list_is_valid_and_means_no_edits():
    result = parse_edits({"edits": []})
    assert result.error is None and result.edits == ()


def test_the_three_edit_types_of_this_version_are_the_only_ones():
    assert EDIT_TYPES == ("reword", "add_example", "add_check")
    for kind in EDIT_TYPES:
        assert accepted(output(edit_mapping(type=kind)))


@pytest.mark.parametrize("kind", ["resequence", "add_prerequisite", "delete", "rewrite", "", None])
def test_a_type_that_is_not_in_this_version_is_refused(kind):
    assert not accepted(output(edit_mapping(type=kind)))


# --------------------------------------------------------------------------- the refused shapes
def cases():
    good = edit_mapping()
    missing = {f"missing {k}": output({x: v for x, v in good.items() if x != k}) for k in good}
    return {
        **missing,
        "an extra key on an edit": output({**good, "note": "x"}),
        "an extra top-level key": {"edits": [good], "revised_text": "the whole text"},
        "no edits key": {},
        "top level is a list": [good],
        "edits is not a list": {"edits": good},
        "an edit that is not an object": output("e1"),
        "id with a capital and a dash": output({**good, "id": "E-1"}),
        "id with no number": output({**good, "id": "e"}),
        "id with four digits": output({**good, "id": "e1234"}),
        "id that is a number": output({**good, "id": 1}),
        "resource ids that are not empty": output({**good, "resource_ids": ["RS-1"]}),
        "finding id with too few hex digits": output({**good, "finding_ids": ["CF-123"]}),
        "finding id in lower case prefix": output({**good, "finding_ids": ["cf-a1b2c3d4"]}),
        "finding id with capital hex": output({**good, "finding_ids": ["CF-A1B2C3D4"]}),
        "finding id with nine hex digits": output({**good, "finding_ids": ["CF-a1b2c3d4e"]}),
        "finding id with a non hex letter": output({**good, "finding_ids": ["CF-a1b2c3dz"]}),
        "no finding ids": output({**good, "finding_ids": []}),
        "the same finding id twice": output({**good, "finding_ids": ["CF-a1b2c3d4"] * 2}),
        "finding ids as a string": output({**good, "finding_ids": "CF-a1b2c3d4"}),
        "an empty anchor": output({**good, "anchor_quote": ""}),
        "an empty suggestion": output({**good, "suggestion": ""}),
        "a replacement that is a number": output({**good, "replacement": 7}),
        "a suggestion that is null": output({**good, "suggestion": None}),
        "an unknown type": output({**good, "type": "resequence"}),
        "two edits with the same id": output(good, {**good, "anchor_quote": "other"}),
    }


@pytest.mark.parametrize("name", list(cases()))
def test_a_wrong_shape_is_one_invalid_result_with_no_edits_and_a_reason(name):
    result = parse_edits(cases()[name])
    assert result.edits == ()
    assert isinstance(result.error, str) and result.error


@pytest.mark.parametrize("text", ["", "not json", "{", "{\"edits\": [}", "null", "7", "[]"])
def test_text_that_is_not_a_json_object_is_one_invalid_result(text):
    result = parse_edits(text)
    assert result.edits == () and result.error


def test_json_text_with_a_wrong_shape_is_refused_like_the_dict():
    assert not accepted(json.dumps(output({**edit_mapping(), "type": "resequence"})))


# --------------------------------------------------------------------------- schema and code agree
def conforms(value, schema):
    """The part of JSON Schema the contract uses: type, enum, pattern, min and max, required,
    additionalProperties, items, uniqueItems."""
    kinds = {"object": dict, "array": list, "string": str}
    if "type" in schema:
        if not isinstance(value, kinds[schema["type"]]):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return False
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False
        distinct = {json.dumps(v, sort_keys=True) for v in value}
        if schema.get("uniqueItems") and len(distinct) != len(value):
            return False
        if "items" in schema and not all(conforms(v, schema["items"]) for v in value):
            return False
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if not all(conforms(v, properties[k]) for k, v in value.items() if k in properties):
            return False
    return True


def test_the_schema_dict_and_the_validator_agree_on_every_case():
    everything = {"a valid edit": output(edit_mapping()), "no edits": {"edits": []}, **cases()}
    for name, raw in everything.items():
        by_schema = conforms(copy.deepcopy(raw), EDIT_SCHEMA)
        by_code = parse_edits(raw).error is None
        # Two ids the same is the one rule JSON Schema cannot say, so the schema alone passes it.
        if name == "two edits with the same id":
            assert by_schema and not by_code
        else:
            assert by_schema == by_code, name


def test_the_schema_holds_no_limit_of_the_template_as_a_literal():
    text = json.dumps(EDIT_SCHEMA)
    assert "maxLength" not in text
    assert '"maxItems": 0' in text  # resource_ids only: there is no library in this version
    assert "max_edits" not in text and "max_edit_chars" not in text
