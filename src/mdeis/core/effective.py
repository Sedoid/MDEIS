"""Which records count, once the approver's answers are applied.

An answer never edits a stored score. It changes which scores the tools use:

    missing_term       confirm_absent  the term stays blank and no longer blocks the subject
                       use_value       the approver's value stands in for the term
    value_out_of_range exclude_record  the score is left out (the term counts as blank)
                       use_value       the approver's value stands in for it
    duplicate_card     keep_as_is      the first card of the year is used, the other is ignored
                       exclude_record  the later card is used, the first is ignored
    a warning          keep_as_is      the warning is acknowledged; the scores count as they are

The latest answer to a problem is the one that counts (the answers table only ever grows).
"""

from fractions import Fraction

from .numbers import exact

ANSWERS = {
    "missing_term": {"confirm_absent", "use_value"},
    "value_out_of_range": {"exclude_record", "use_value"},
    "duplicate_card": {"keep_as_is", "exclude_record"},
    "unmapped_subject": {"exclude_record"},
}
WARNING_ANSWERS = {"keep_as_is"}


def allowed_answers(kind: str) -> set:
    return ANSWERS.get(kind, WARNING_ANSWERS)


def blocked_subjects(issues, answers, rules) -> set:
    """Subjects with an unanswered blocking problem. A duplicated year blocks every subject."""
    blocked = set()
    for issue in issues:
        if issue["severity"] != "blocking" or issue["issue_id"] in answers:
            continue
        if issue["kind"] == "duplicate_card":
            blocked |= set(rules.subjects)
        elif issue["subject"] in rules.subjects:
            blocked.add(issue["subject"])
    return blocked


def effective_records(cards, issues, answers, rules) -> list:
    """The records the tools use: one card per year, with the approver's answers applied."""
    by_issue = {i["fingerprint"]: i for i in issues}
    by_year = {}
    for card in cards:
        by_year.setdefault(card["year"], []).append(card)
    chosen = []
    for year, group in sorted(by_year.items()):
        pick = group[0]
        if len(group) > 1:
            duplicate = next(
                (i for i in issues if i["kind"] == "duplicate_card" and i["year"] == year), None
            )
            answer = answers.get(duplicate["issue_id"]) if duplicate else None
            if answer and answer["resolution"] == "exclude_record":
                pick = group[-1]
        chosen.append(pick)
    records = []
    for card in chosen:
        for subject, entry in sorted(card["subjects"].items()):
            if subject not in rules.subjects:
                continue
            for term, item in sorted(entry["terms"].items()):
                score = item["score"]
                answer = _answer_for(by_issue, answers, card, subject, term)
                if answer is not None:
                    score = _applied(answer, score)
                records.append(
                    {
                        "record_id": item["record_id"],
                        "year": card["year"],
                        "term": term,
                        "stage": card["stage"],
                        "subject": subject,
                        "score": score,
                        "denominator": card["denominator"],
                    }
                )
    return records


def _answer_for(by_issue, answers, card, subject, term):
    """The approver's answer to a missing or out-of-range score on this term, if there is one."""
    for issue in by_issue.values():
        if (
            issue["kind"] in ("missing_term", "value_out_of_range")
            and issue["card_id"] == card["card_id"]
            and issue["subject"] == subject
            and issue["term"] == term
        ):
            return answers.get(issue["issue_id"])
    return None


def _applied(answer, score):
    if answer["resolution"] == "use_value" and answer["value"] is not None:
        return Fraction(exact(answer["value"]))
    if answer["resolution"] in ("exclude_record", "confirm_absent"):
        return None
    return score
