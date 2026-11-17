"""The admin step `apply-template-patch`: confirmed patches become a NEW template version.

    python -m mdeis.admin apply-template-patch --patch TP-000001 [--patch TP-000002 ...] \
        --version-name baobab-v2 --by "Name" [--template-dir DIR] [--no-activate] [--no-embed]

It refuses a patch that is not `proposed`, whose measure version was not confirmed by a registered
teacher, whose text no longer matches its hash, or that was checked against a template other than
the active one. It builds the content of the new version from the ACTIVE template and the stored
patches (never from anything typed), checks it with the template's own validator, writes the file,
loads it through the ordinary rulebook loader (one logged load, with the file hash), and marks the
patches applied. The name written into each new clause is the confirming teacher's, read from the
registry row. `--by` is a typed name that says who ran the step; it is not an approver.

A loaded version never changes, so the new file never replaces one. Every added value is printed and
written to the action log. Making the new version active makes every unreleased draft `stale_pin`;
load a batch of confirmed measures at a time that suits the school, not in the middle of drafting.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from psycopg.rows import dict_row

from .. import rulebook
from ..db import diary
from . import patch

TEMPLATE_DIR_ENV = "TEMPLATE_DIR"
VERSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ApplyRefused(Exception):
    """The step cannot be done. The message says why, in plain words. It has been logged."""


def _refuse(url, inputs: dict, reason: str, by: str):
    diary.write(
        "apply_template_patch",
        inputs={**inputs, "by": by},
        outputs={"reason": reason},
        outcome="refused",
        url=url,
    )
    raise ApplyRefused(reason)


def load_patches(conn, patch_ids: list[str]) -> list[dict]:
    """The stored patches, with the confirming teacher's name from the registry, in the order
    asked."""
    rows = conn.cursor(row_factory=dict_row).execute(
        "SELECT p.patch_id, p.status, p.measure_id, p.version, p.base_template_version, "
        "p.patch_text, p.patch_sha256, v.teacher_decision, v.teacher_name "
        "FROM template_patches p JOIN measure_versions v "
        "ON v.measure_id = p.measure_id AND v.version = p.version "
        "WHERE p.patch_id = ANY(%s) FOR UPDATE OF p",
        (patch_ids,),
    ).fetchall()
    by_id = {r["patch_id"]: r for r in rows}
    return [by_id[i] for i in patch_ids if i in by_id]


def apply_patches(
    conn,
    patch_ids: list[str],
    version_name: str,
    by: str,
    template_dir: Path,
    *,
    url: str | None = None,
    today: dt.date | None = None,
    activate: bool = True,
) -> dict:
    """Do the step. Returns what was done; raises ApplyRefused (logged) if it cannot be."""
    inputs = {"patches": list(patch_ids), "version_name": version_name}
    name = (by or "").strip()
    if not name:
        _refuse(url, inputs, "the step needs the name of the person who runs it", by)
    if not patch_ids or len(set(patch_ids)) != len(patch_ids):
        _refuse(url, inputs, "name each patch once, with --patch", name)
    if not VERSION_NAME.match(version_name or ""):
        _refuse(url, inputs, "a version name is letters, digits, dots, dashes or underscores", name)
    target = Path(template_dir) / f"{version_name}.yaml"
    taken = conn.execute(
        "SELECT 1 FROM templates WHERE version_name = %s", (version_name,)
    ).fetchone()
    if taken or target.exists():
        _refuse(url, inputs, f"{version_name} already exists: a loaded version never changes", name)

    found = load_patches(conn, patch_ids)
    if len(found) != len(patch_ids):
        known = {p["patch_id"] for p in found}
        missing = [i for i in patch_ids if i not in known]
        _refuse(url, inputs, f"there is no patch {', '.join(missing)}", name)
    stored = []
    for row in found:
        if row["status"] != "proposed":
            _refuse(url, inputs, f"{row['patch_id']} is {row['status']}, not proposed", name)
        if row["teacher_decision"] != "confirmed" or not row["teacher_name"]:
            _refuse(
                url, inputs,
                f"{row['patch_id']}: measure {row['measure_id']} was not confirmed by a registered "
                "teacher, so its values cannot be loaded", name,
            )  # fmt: skip
        stored.append({
            "patch_id": row["patch_id"], "body": json.loads(row["patch_text"]),
            "sha256": row["patch_sha256"], "teacher": row["teacher_name"],
            "measure_id": row["measure_id"], "base": row["base_template_version"],
        })  # fmt: skip
    active = conn.cursor(row_factory=dict_row).execute(
        "SELECT version_name, template_id, content FROM templates WHERE active"
    ).fetchone()
    if active is None:
        _refuse(url, inputs, "no template version is active", name)
    try:
        for item in stored:
            patch.verify(item)
            if item["base"] != active["version_name"]:
                raise patch.PatchRefused(
                    f"{item['patch_id']} was checked against {item['base']}, but the active "
                    f"template is {active['version_name']}: build the measure again"
                )
        declared = [
            r[0]
            for r in conn.execute(
                "SELECT declared_version FROM templates WHERE template_id = %s",
                (active["template_id"],),
            ).fetchall()
        ]
        new_number = patch.next_version(declared)
        measures = ", ".join(sorted({i["measure_id"] for i in stored}))
        note = (
            f"Added measure parameters from {', '.join(patch_ids)} (measure {measures}). "
            f"Each value was confirmed by the teacher named in its clause."
        )
        merged = patch.merge_patches(
            active["content"], stored, new_version=new_number,
            today=(today or dt.date.today()).isoformat(), note=note,
        )  # fmt: skip
    except patch.PatchRefused as error:
        _refuse(url, inputs, str(error), name)

    lines = patch.describe_added(active["content"], merged)
    header = [
        f"Template version {version_name}, made by apply-template-patch from "
        f"{active['version_name']}.",
        f"Patches: {', '.join(patch_ids)}. Run by: {name}.",
        "Only measure_params clauses, one change_log line and the version number were added.",
    ]
    text = patch.render_yaml(merged, header)
    target.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        loaded = rulebook.load_file(conn, target, activate=activate, url=url, by=name)
    except rulebook.RulebookError as error:
        target.unlink(missing_ok=True)
        _refuse(url, inputs, str(error), name)
    for item in stored:
        conn.execute(
            "UPDATE template_patches SET status = 'applied', applied_by = %s, applied_at = now(), "
            "applied_as = %s WHERE patch_id = %s",
            (name, version_name, item["patch_id"]),
        )
    conn.commit()
    diary.write(
        "apply_template_patch",
        inputs={**inputs, "by": name},
        outputs={
            "file": str(target), "sha256": digest, "version": loaded.version,
            "clauses": loaded.clauses, "active": loaded.active, "added": lines,
            "declared_version": new_number,
        },
        outcome="ok",
        url=url,
    )  # fmt: skip
    return {"path": str(target), "sha256": digest, "added": lines, "loaded": loaded,
            "declared_version": new_number}  # fmt: skip
