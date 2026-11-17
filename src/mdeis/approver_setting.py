"""One opt-in server setting: `REVIEWERS_ARE_APPROVERS` (T-077, an owner decision of 2026-11-17).

Off (unset, empty, false or anything that is not on): nothing changes. Only a person recorded in
`registry_reviewers` (by the `grant-reviewer` admin step) can clear a hold or decide at the demo.

On: a signed-in person whose verified token carries a school counts as BOTH a registered teacher
and a registered developer, for these steps only: listing and opening measures at the demo,
trying, saving a teacher case, confirming, rejecting, and clearing an automated-review hold. The
person's id and name come only from the verified token. The decision row still records that named
person, and every call is still one action-log row with that person.

What the setting does not touch. The database's approval conditions are unchanged and still
checked (tests passed, a test that did not come from the model, the static check passed, the
automated review not holding, the teacher's decision `confirmed`). The automated review can still
only hold, and a hold is cleared only by an explicit call of a signed-in person with a note: never
by the review and never by the system. A session with no school or no token is refused. A person
whose record an administrator ENDED (a revoke) is still refused: an explicit revoke is stronger
than this setting.

The database's own guard needs a `registry_reviewers` row for the person it records. So when a
person is accepted only because of this setting, the row is made at the moment of the write,
inside the same transaction (see `mdeis.registry.approvers`), and an action-log row says so. This
module only reads the setting and holds the shared wording. It writes nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "REVIEWERS_ARE_APPROVERS"
ON = ("1", "true", "yes", "on")  # the same words the class-view switch accepts
AUTO_NOTE = "auto-registered by REVIEWERS_ARE_APPROVERS"
GRANTED_BY = "auto:REVIEWERS_ARE_APPROVERS"
AUTO_TOOL = "registry_auto_register"


def enabled(env: Mapping | None = None) -> bool:
    """True only when the environment switches the setting on. Read each time it is asked, so a
    change needs no code change and a test can set it. A missing value is off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "false")).strip().lower() in ON


def ended(conn, user_id: str, role: str) -> bool:
    """True when the registry holds a record of this person in this role that an administrator ended
    (and no live one). Read only."""
    row = conn.execute(
        "SELECT bool_or(revoked_at IS NULL) FROM registry_reviewers "
        "WHERE user_id = %s AND role = %s",
        (user_id, role),
    ).fetchone()
    value = row[0] if not isinstance(row, dict) else next(iter(row.values()))
    return value is False  # no row at all gives None; a live row gives True


def ended_text(role: str) -> str:
    """The refusal for an ended record. It keeps the words 'not recorded as a <role>' so that the
    routes type it the same way as the plain refusal."""
    return (
        f"the signed-in approver is not recorded as a {role} in the registry: an administrator "
        f"ended that record, and {FLAG} does not override an ended record"
    )


def name_of(who) -> str | None:
    """The name the verified token gave the signed-in person, or None when it is blank."""
    name = getattr(who, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def school_of(ctx) -> str | None:
    """The school the verified token carries, or None when it has none."""
    school = getattr(ctx, "school", None)
    if isinstance(school, str) and school.strip():
        return school.strip()
    return None
