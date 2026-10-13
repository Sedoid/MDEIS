"""Find the problems in a learner's cards and name each one. Nothing is guessed or fixed.

Every oddity becomes a typed problem (docs/SPEC.md 5.1) with a plain question for the approver. The
kinds that block (nothing is worked out for the subject until a person answers) are the ones where a
number would otherwise have to be guessed. The rest are warnings: they are shown, and the scores
still count. Which kinds block is the decision recorded in docs/step-07.md (D7-9).
"""

import hashlib
import json

from .numbers import grade_for, two_places
from .records import ZERO, term_scores

BLOCKING = {"missing_term", "value_out_of_range", "duplicate_card", "unmapped_subject"}
IMPROVING_WORD = "improving"
DECLINING = "declining"


def severity_of(kind: str) -> str:
    return "blocking" if kind in BLOCKING else "warning"


def fingerprint(learner_id, kind, year, card_id, subject, term, field) -> str:
    """The same problem found again gets the same fingerprint, so it keeps one issue number."""
    text = json.dumps([learner_id, kind, year, card_id, subject, term, field])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _issue(learner_id, kind, year, card_id=None, subject=None, term=None, field=None, **extra):
    out = {
        "kind": kind,
        "severity": severity_of(kind),
        "learner_id": learner_id,
        "year": year,
        "card_id": card_id,
        "subject": subject,
        "term": term,
        "field": field,
        "fingerprint": fingerprint(learner_id, kind, year, card_id, subject, term, field),
        "records": [],
    }
    out.update(extra)
    return out


def _words(issue, detail, question):
    issue["detail"] = detail
    issue["question"] = question
    return issue


def _move_label(change, rules):
    if change > rules.move_threshold:
        return "improving"
    if change < -rules.move_threshold:
        return DECLINING
    return "steady"


def detect(learner_id, cards, rules, move_label=_move_label) -> list:
    """The problems found in `cards` under `rules`. Pure: the same cards give the same problems."""
    issues = []
    by_year = {}
    for card in cards:
        by_year.setdefault(card["year"], []).append(card)
    for year, group in sorted(by_year.items()):
        if len(group) > 1:
            ids = [c["card_id"] for c in group]
            issue = _issue(learner_id, "duplicate_card", year, card_id=",".join(ids), cards=ids)
            issues.append(
                _words(
                    issue,
                    f"There are {len(group)} cards for {year}: {', '.join(ids)}.",
                    f"Which card should be used for {year}?",
                )
            )
    for card in cards:
        issues.extend(_card_issues(learner_id, card, rules))
    return issues


def _card_issues(learner_id, card, rules):
    out = []
    year, card_id, denominator = card["year"], card["card_id"], card["denominator"]
    if denominator != rules.scale_max:
        issue = _issue(
            learner_id,
            "denominator_unexpected",
            year,
            card_id=card_id,
            denominator=two_places(denominator),
            template_scale=two_places(rules.scale_max),
        )
        out.append(
            _words(
                issue,
                f"Card {card_id} is marked out of {two_places(denominator)}; the rulebook scale is "
                f"{two_places(rules.scale_max)}.",
                "The scores will be converted to the rulebook scale. Is that right?",
            )
        )
    total_weighted, total_coef = ZERO, ZERO
    for subject, entry in sorted(card["subjects"].items()):
        if subject not in rules.subjects:
            issue = _issue(learner_id, "unmapped_subject", year, card_id=card_id, subject=subject)
            out.append(
                _words(
                    issue,
                    f"{subject} on card {card_id} is not a subject in the rulebook.",
                    f"Which rulebook subject is {subject}?",
                )
            )
            continue
        coefficient = rules.subjects[subject]
        scores = term_scores(entry)
        for term, record_id, score in scores:
            if score is None:
                issue = _issue(
                    learner_id, "missing_term", year, card_id=card_id, subject=subject, term=term
                )
                issue["records"] = [record_id]
                out.append(
                    _words(
                        issue,
                        f"{subject} has no score for term {term} of {year} on card {card_id}.",
                        f"Was {subject} not assessed in term {term} of {year}, or is the score "
                        "missing from the card?",
                    )
                )
            elif score > denominator or score < ZERO:
                issue = _issue(
                    learner_id,
                    "value_out_of_range",
                    year,
                    card_id=card_id,
                    subject=subject,
                    term=term,
                    value=two_places(score),
                )
                issue["records"] = [record_id]
                out.append(
                    _words(
                        issue,
                        f"{subject} term {term} of {year} is {two_places(score)}, outside 0 to "
                        f"{two_places(denominator)}.",
                        "What is the correct score for that term?",
                    )
                )
        if not card["printable"]:
            continue
        values = [s for _, _, s in scores]
        if values and all(v is not None for v in values):
            annual = sum(values) / len(values)
            out.extend(
                _printed_checks(learner_id, card, subject, entry, annual, coefficient, rules)
            )
            total_weighted += annual * coefficient
            total_coef += coefficient
            out.extend(_remark_check(learner_id, card, subject, entry, values, rules, move_label))
    out.extend(_total_checks(learner_id, card, total_weighted, total_coef, rules))
    return out


def _printed_checks(learner_id, card, subject, entry, annual, coefficient, rules):
    out = []
    year, card_id = card["year"], card["card_id"]
    printed = entry["printed_annual"]
    checks = []
    if printed is not None and printed != annual:
        checks.append(("annual_score", two_places(printed), two_places(annual)))
    if entry["printed_grade"] != grade_for(annual, rules.grade_bands):
        worked_out = grade_for(annual, rules.grade_bands)
        checks.append(("subject_grade", entry["printed_grade"], worked_out))
    if printed is not None and entry["printed_weighted"] != printed * coefficient:
        checks.append(
            (
                "weighted_score",
                None
                if entry["printed_weighted"] is None
                else two_places(entry["printed_weighted"]),
                two_places(printed * coefficient),
            )
        )
    for field, shown, worked_out in checks:
        issue = _issue(
            learner_id,
            "printed_value_mismatch",
            year,
            card_id=card_id,
            subject=subject,
            field=field,
            printed=shown,
            recomputed=worked_out,
        )
        out.append(
            _words(
                issue,
                f"Card {card_id} prints {shown} for {field.replace('_', ' ')} of {subject}, but "
                f"the term scores give {worked_out}.",
                "Is the printed value a mistake on the card?",
            )
        )
    return out


def _remark_check(learner_id, card, subject, entry, values, rules, move_label):
    remark = entry["remark"] or ""
    if IMPROVING_WORD not in remark.lower():
        return []
    if move_label(values[-1] - values[0], rules) != DECLINING:
        return []
    issue = _issue(
        learner_id,
        "note_conflict",
        card["year"],
        card_id=card["card_id"],
        subject=subject,
        terms=[two_places(v) for v in values],
    )
    return [
        _words(
            issue,
            f"The remark on {subject} says improving, but the term scores are "
            f"{', '.join(two_places(v) for v in values)}.",
            "Should the remark or the scores be relied on?",
        )
    ]


def _total_checks(learner_id, card, total_weighted, total_coef, rules):
    printed_total = card["printed_total_weighted"]
    if not card["printable"] or total_coef == ZERO or printed_total is None:
        return []
    average = total_weighted / total_coef
    checks = [
        ("total_weighted", two_places(printed_total), two_places(total_weighted)),
        ("annual_average", two_places(card["printed_average"]), two_places(average)),
    ]
    if card["printed_grade"] != grade_for(average, rules.grade_bands):
        checks.append(("grade", card["printed_grade"], grade_for(average, rules.grade_bands)))
    out = []
    for field, shown, worked_out in checks:
        if shown == worked_out:
            continue
        issue = _issue(
            learner_id,
            "printed_value_mismatch",
            card["year"],
            card_id=card["card_id"],
            field=field,
            printed=shown,
            recomputed=worked_out,
        )
        out.append(
            _words(
                issue,
                f"Card {card['card_id']} prints {shown} for {field.replace('_', ' ')}, but the "
                f"term scores give {worked_out}.",
                "Is the printed value a mistake on the card?",
            )
        )
    return out
