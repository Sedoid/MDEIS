"""The fixed lists of the registry: statuses, legal moves, and the words the database also checks.

Pure data and one pure function. The schema (migration 008) holds the same lists; a unit test reads
the migration and fails if the two ever differ.

    proposed > testing > awaiting_review > awaiting_demo > approved
    proposed, testing, awaiting_review or awaiting_demo > rejected or retired
    approved > retired

`awaiting_review` is the automated review. It can only pass or hold. `approved` is reached only by
the teacher's decision (`store.record_teacher_decision`); `advance_status` cannot move a version
there.
"""

PROPOSED = "proposed"
TESTING = "testing"
AWAITING_REVIEW = "awaiting_review"
AWAITING_DEMO = "awaiting_demo"
APPROVED = "approved"
REJECTED = "rejected"
RETIRED = "retired"

STATUSES = (PROPOSED, TESTING, AWAITING_REVIEW, AWAITING_DEMO, APPROVED, REJECTED, RETIRED)

LEGAL = frozenset(
    {
        (PROPOSED, TESTING),
        (TESTING, AWAITING_REVIEW),
        (AWAITING_REVIEW, AWAITING_DEMO),
        (AWAITING_DEMO, APPROVED),
        (APPROVED, RETIRED),
        (PROPOSED, REJECTED),
        (TESTING, REJECTED),
        (AWAITING_REVIEW, REJECTED),
        (AWAITING_DEMO, REJECTED),
        (PROPOSED, RETIRED),
        (TESTING, RETIRED),
        (AWAITING_REVIEW, RETIRED),
        (AWAITING_DEMO, RETIRED),
    }
)

# What `advance_status` may do. Approval is the teacher's decision, and retiring has its own
# function.
ADVANCE_TARGETS = (TESTING, AWAITING_REVIEW, AWAITING_DEMO, REJECTED)

# Where a test result can be recorded: while the tests run, and at the demo when a teacher adds a
# case.
RESULT_STATUSES = (TESTING, AWAITING_DEMO)

# Where each test came from. The model wrote `build` and `break_it` tests. A `document_example` test
# carries a quote from a worked example in the methodology. A `teacher` test is a case a signed-in
# teacher supplied at the demo. An approval needs at least one of the last two.
TEST_SOURCES = ("build", "break_it", "teacher", "document_example")
INDEPENDENT_SOURCES = ("teacher", "document_example")
BUILD_SOURCES = ("build", "break_it", "document_example")  # added by the system while testing
DEMO_SOURCES = ("teacher",)  # added at the demo, by a registered teacher

PASS = "pass"
HOLD = "hold"
REVIEW_RESULTS = (PASS, HOLD)

CONFIRMED = "confirmed"
DECISIONS = (CONFIRMED, REJECTED)

RETIRE_REASONS = ("superseded", "section_removed", "withdrawn")
INPUT_SCOPES = ("learner", "learner_skill")

DEVELOPER = "developer"
TEACHER = "teacher"


def is_legal(current: str, target: str) -> bool:
    """True when a version may move from `current` to `target`."""
    return (current, target) in LEGAL
