"""The registry of generated measures (T-052, slice 1).

A methodology can ask for a measure the fixed tools do not compute. A function is generated for it,
checked, tested, reviewed and shown to a teacher. Only an approved version is ever run for a user.
This package holds the record of that path and the rules of it. It runs no code.

Fixed versus generated. A generated function can only compute: records and template values in,
exact numbers out. It never reaches a fixed tool's powers. A fixed tool is anything that reads
records, applies a gate, writes, draws, exports, approves or releases. Nothing here is a tool the
model can call, and nothing in this package can be reached from the orchestrator or the gateway.

Who decides:
  * the automated review can only HOLD (`record_review` records pass or hold, once). It never
    approves, never replaces a deterministic check and never clears a failed one;
  * a hold is cleared only by a signed-in developer who is recorded in `registry_reviewers`;
  * only the teacher's decision (`record_teacher_decision`) can make a version `approved`, by a
    signed-in teacher who is recorded in `registry_reviewers`;
  * the signed-in person is read from the context. No function has an argument that names one.
The database holds the same rules as constraints (migration 008): the last lock.
"""

from .store import (
    advance_status,
    clear_hold,
    create_version,
    record_review,
    record_teacher_decision,
    record_tests,
    retire,
)

__all__ = [
    "advance_status",
    "clear_hold",
    "create_version",
    "record_review",
    "record_teacher_decision",
    "record_tests",
    "retire",
]
