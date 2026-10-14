"""What several tools need: one learner's situation, and the form results are returned in."""

import hashlib
import json
from dataclasses import dataclass

from ..core import store
from ..core.effective import allowed_answers, blocked_subjects, effective_records
from ..core.issues import detect
from ..core.numbers import two_places
from ..core.records import load_cards
from ..core.rules import Rules, load_rules
from ..core.trend import on_scale, when
from .context import Refusal, ToolContext

INTERNAL_ISSUE_KEYS = {"fingerprint", "learner_id"}


@dataclass
class Situation:
    """Everything worked out about one learner under one rulebook version."""

    learner_id: str
    rules: Rules
    cards: list
    issues: list  # each with its issue_id
    answers: dict  # issue_id -> the approver's latest answer
    blocked: set  # subjects with an unanswered blocking problem
    records: list  # the records the tools use, answers applied


def situation(ctx: ToolContext, learner_id: str, template_version: str | None) -> Situation:
    if not store.learner_exists(ctx.conn, learner_id):
        raise Refusal(f"there is no learner {learner_id}")
    rules = load_rules(ctx.conn, template_version)
    cards = load_cards(ctx.conn, learner_id)
    issues = detect(learner_id, cards, rules)
    store.sync_issues(ctx.conn, learner_id, issues, ctx.run_id)
    answers = store.load_answers(ctx.conn, learner_id)
    return Situation(
        learner_id=learner_id,
        rules=rules,
        cards=cards,
        issues=issues,
        answers=answers,
        blocked=blocked_subjects(issues, answers, rules),
        records=effective_records(cards, issues, answers, rules),
    )


def active_methodology(conn) -> str | None:
    """The document id of the methodology in force, or None when none has been uploaded."""
    row = conn.execute(
        "SELECT document_id FROM documents WHERE kind = 'methodology' AND active"
    ).fetchone()
    return row[0] if row else None


def sources_of(conn, found: Situation, record_ids=None) -> list:
    """The report-card files these records came from, oldest first.

    Each is `{document_id, file, academic_year, stage}`. `file` is the name the card was uploaded
    under, which is what a person recognises; record numbers mean nothing to them. With no
    `record_ids`, every card of the learner. It is added to a result after the hash is taken, so it
    changes no hash.
    """
    wanted = None if record_ids is None else set(record_ids)
    cards = {}
    for card in found.cards:
        if not card["document_id"]:
            continue
        ids = {t["record_id"] for e in card["subjects"].values() for t in e["terms"].values()}
        if wanted is None or ids & wanted:
            cards[card["document_id"]] = card
    if not cards:
        return []
    names = dict(
        conn.execute(
            "SELECT document_id, original_name FROM documents WHERE document_id = ANY(%s)",
            (list(cards),),
        ).fetchall()
    )
    ordered = sorted(cards.values(), key=lambda c: (c["year_start"], c["document_id"]))
    return [
        {
            "document_id": c["document_id"],
            "file": names.get(c["document_id"], c["document_id"]),
            "academic_year": c["year"],
            "stage": c["stage"],
        }
        for c in ordered
    ]


def public_issue(issue: dict, answers: dict) -> dict:
    """An issue as the caller sees it: without the internal fingerprint, with its answer if any."""
    out = {k: v for k, v in issue.items() if k not in INTERNAL_ISSUE_KEYS}
    answer = answers.get(issue["issue_id"])
    out["answered"] = answer["resolution"] if answer else None
    out["possible_answers"] = sorted(allowed_answers(issue["kind"]))
    return out


def public_records(found: Situation) -> list:
    """The records in use, scores on the rulebook's scale, oldest first."""
    out = []
    for record in sorted(found.records, key=lambda r: (*when(r), r["subject"])):
        value = on_scale(record, found.rules)
        out.append(
            {
                "record_id": record["record_id"],
                "year": record["year"],
                "term": record["term"],
                "stage": record["stage"],
                "subject": record["subject"],
                "score": None if value is None else two_places(value),
            }
        )
    return out


def with_hash(result: dict) -> dict:
    """Add `result_hash`: a hash of the result's own content, so the same input gives the same hash.

    It leaves out anything the database numbers (issue numbers, the diary line) and any time.
    """
    content = _without_database_numbers(result)
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    result["result_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return result


NOT_HASHED = {"result_hash", "logged_event_id", "issue_id"}


def _without_database_numbers(value):
    if isinstance(value, dict):
        return {k: _without_database_numbers(v) for k, v in value.items() if k not in NOT_HASHED}
    if isinstance(value, list):
        return [_without_database_numbers(v) for v in value]
    return value
