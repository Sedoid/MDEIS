"""Template patches: the data a build proposes, and how a confirmed one becomes a new template
version. Pure: no database, no file, no model. The admin step (`apply.py`) reads and writes.

A patch is the proposed `measure_params` clauses and the version of the template they were checked
against, stored as data with the sha256 of its canonical text. Nothing here edits a loaded
template version: a merge builds the content of a NEW version from the base content and the
patches.

What a merge changes, and only this: the clauses added to `measure_params` (each `confirmed_by` is
the name of the teacher who confirmed the measure, copied from the registry row, never from the
patch text), one more `change_log` line, and the version number. It adds no approver, and it
refuses a patch that already names someone.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json

import yaml

from ..core.template import TemplateError, parse_template


class PatchRefused(ValueError):
    """A patch cannot be used. The message says why, in plain words."""


def canonical_text(patch: dict) -> str:
    """The patch as one canonical JSON text: sorted keys, no spaces, no escaping of letters."""
    return json.dumps(patch, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def patch_sha256(patch: dict) -> str:
    return hashlib.sha256(canonical_text(patch).encode("utf-8")).hexdigest()


def verify(patch: dict) -> None:
    """Refuse a stored patch whose body no longer has the hash it was stored with."""
    if patch_sha256(patch["body"]) != patch.get("sha256"):
        raise PatchRefused(f"{patch.get('patch_id')}: the patch text no longer matches its hash")


def next_version(declared) -> int:
    """One more than the largest declared template version number among `declared`."""
    numbers = []
    for item in declared:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return max(numbers, default=0) + 1


def merge_patches(
    base: dict, patch_list: list[dict], *, new_version: int, today: str, note: str
) -> dict:
    """The content of a new template version: `base` plus the clauses of the patches.

    Each patch is {patch_id, body: {measure_params: [...]}, teacher}. Raises PatchRefused. The
    result is checked by the template's own validator, so a rule it would refuse stops the merge.
    """
    if not patch_list:
        raise PatchRefused("there is no patch to apply")
    try:
        dt.date.fromisoformat(today)
    except (TypeError, ValueError):
        raise PatchRefused("the date must be an ISO date such as 2026-11-20") from None
    merged = copy.deepcopy(base)
    added = list(merged.get("measure_params") or [])
    taken = {r.get("id") for r in added}
    for patch in patch_list:
        who = patch.get("teacher")
        if not isinstance(who, str) or not who.strip():
            raise PatchRefused(f"{patch.get('patch_id')}: no confirming teacher is recorded")
        for rule in copy.deepcopy(patch["body"]["measure_params"]):
            if rule.get("confirmed_by") is not None:
                raise PatchRefused(
                    f"{patch.get('patch_id')}: {rule.get('id')} already has confirmed_by set; "
                    "the name is copied from the registry, never from the patch"
                )
            if rule.get("id") in taken:
                raise PatchRefused(f"{patch.get('patch_id')}: {rule.get('id')} is already in use")
            taken.add(rule.get("id"))
            rule["confirmed_by"] = who
            added.append(rule)
    merged["measure_params"] = added
    merged["version"] = new_version
    merged["change_log"] = [
        *merged["change_log"],
        {"version": new_version, "date": today, "note": note},
    ]
    try:
        parse_template(merged)
    except TemplateError as error:
        raise PatchRefused(str(error)) from error
    return merged


def render_yaml(content: dict, header: list[str]) -> str:
    """The file text of a template: the header as comment lines, then the content as YAML."""
    top = "".join(f"# {line}\n" for line in header)
    body = yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
    return top + body


def describe_added(base: dict, merged: dict) -> list[str]:
    """One plain line for each clause `merged` has and `base` has not, for the admin to read."""
    before = {r.get("id") for r in base.get("measure_params") or []}
    lines = []
    for rule in merged.get("measure_params") or []:
        if rule["id"] in before:
            continue
        value = rule["value"]
        shown = ", ".join(value) if isinstance(value, list) else value
        lines.append(
            f"{rule['id']} {rule['name']} = {shown} {rule['unit']} "
            f"(from {rule['source_document']} section {rule['method_ref']}; "
            f"confirmed by {rule['confirmed_by']})"
        )
    return lines
