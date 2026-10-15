"""The answer key's calculator. Written from the methodology text (6.3 to 6.7, 8.1 to 8.4) and the
template values, NOT from src/mdeis. It uses exact fractions, so a change of exactly 1.0 is exactly
1.0. The system under test never sees this code.

A record is a dict: learner_id, card_id, year ("2025/2026"), stage ("Form 3"), term (1 to 3),
subject, score (Fraction or None for a blank term), denominator (Fraction, 20 on the school's
cards).
"""

from fractions import Fraction

from .maths import (
    distance_to_range,
    grade_for,
    level_name,
    two_places,
    weighted_average,
)


def when(rec):
    """Chronological sort key: school year, then term."""
    return (int(rec["year"][:4]), rec["term"])


def on_scale(rec, rules):
    """The score converted to the template's scale (out of 20), or None if the term is blank."""
    if rec["score"] is None:
        return None
    return rec["score"] * rules.scale_max / rec["denominator"]


def move_label(change, rules):
    """TR-2: more than the threshold is a move, otherwise steady."""
    if change > rules.move_threshold:
        return "improving"
    if change < -rules.move_threshold:
        return "declining"
    return "steady"


def txt(x):
    return two_places(x)


# --------------------------------------------------------------------------- issues from cards
def card_issues(cards, rules):
    """Typed issues (SPEC 5.1) a careful reader finds in a learner's cards.

    Severity is the key's own reading: missing_term, value_out_of_range and duplicate_card block
    (nothing is guessed); printed_value_mismatch and note_conflict are warnings. The spec does not
    fix severities; FIXTURES.md says so.
    """
    issues = []
    by_year = {}
    for c in cards:
        by_year.setdefault((c["learner_id"], c["year"]), []).append(c)
    for (learner, year), group in sorted(by_year.items()):
        if len(group) > 1:
            issues.append(
                {
                    "kind": "duplicate_card",
                    "severity": "blocking",
                    "learner_id": learner,
                    "year": year,
                    "subjects": sorted(rules.subjects),
                    "cards": sorted(c["card_id"] for c in group),
                }
            )
    for c in cards:
        if c.get("denominator", rules.scale_max) != rules.scale_max:
            issues.append(
                {
                    "kind": "denominator_unexpected",
                    "severity": "warning",
                    "learner_id": c["learner_id"],
                    "year": c["year"],
                    "card_id": c["card_id"],
                    "denominator": str(c["denominator"]),
                    "template_scale": str(rules.scale_max),
                }
            )
        total_weighted = Fraction(0)
        total_coef = Fraction(0)
        for s in c["subjects"]:
            name = s["name"]
            terms = s["terms"]
            for t, score in enumerate(terms, start=1):
                base = {
                    "learner_id": c["learner_id"],
                    "year": c["year"],
                    "card_id": c["card_id"],
                    "subject": name,
                    "term": t,
                }
                if score is None:
                    issues.append({**base, "kind": "missing_term", "severity": "blocking"})
                elif score > c.get("denominator", rules.scale_max) or score < 0:
                    issues.append(
                        {
                            **base,
                            "kind": "value_out_of_range",
                            "severity": "blocking",
                            "value": txt(score),
                        }
                    )
            if not c.get("printable", True):
                continue
            if all(x is not None for x in terms):
                annual = sum(terms) / len(terms)
                printed = s["printed_annual"]
                if printed != annual:
                    issues.append(
                        {
                            "kind": "printed_value_mismatch",
                            "severity": "warning",
                            "learner_id": c["learner_id"],
                            "year": c["year"],
                            "card_id": c["card_id"],
                            "subject": name,
                            "field": "annual_score",
                            "printed": txt(printed),
                            "recomputed": txt(annual),
                        }
                    )
                if s["printed_grade"] != grade_for(annual, rules.grade_bands):
                    issues.append(
                        {
                            "kind": "printed_value_mismatch",
                            "severity": "warning",
                            "learner_id": c["learner_id"],
                            "year": c["year"],
                            "card_id": c["card_id"],
                            "subject": name,
                            "field": "subject_grade",
                            "printed": s["printed_grade"],
                            "recomputed": grade_for(annual, rules.grade_bands),
                        }
                    )
                weighted = s["printed_annual"] * rules.subjects[name]
                if s["printed_weighted"] != weighted:
                    issues.append(
                        {
                            "kind": "printed_value_mismatch",
                            "severity": "warning",
                            "learner_id": c["learner_id"],
                            "year": c["year"],
                            "card_id": c["card_id"],
                            "subject": name,
                            "field": "weighted_score",
                            "printed": txt(s["printed_weighted"]),
                            "recomputed": txt(weighted),
                        }
                    )
                recomputed_weighted = annual * rules.subjects[name]
                total_weighted += recomputed_weighted
                total_coef += rules.subjects[name]
            if remark_says_improving(s["remark"]) and all(x is not None for x in terms):
                if move_label(terms[-1] - terms[0], rules) == "declining":
                    issues.append(
                        {
                            "kind": "note_conflict",
                            "severity": "warning",
                            "learner_id": c["learner_id"],
                            "year": c["year"],
                            "card_id": c["card_id"],
                            "subject": name,
                            "terms": [txt(x) for x in terms],
                        }
                    )
        issues.extend(totals_mismatches(c, rules, total_weighted, total_coef))
    return sorted(
        issues,
        key=lambda i: (
            i["kind"],
            i["learner_id"],
            i["year"],
            i.get("card_id", ""),
            i.get("subject", ""),
            i.get("term", 0),
            i.get("field", ""),
        ),
    )


def remark_says_improving(remark):
    return "improving" in remark.lower()


def totals_mismatches(card, rules, total_weighted, total_coef):
    """Printed TOTAL, ANNUAL AVERAGE and grade against the values recomputed from the term scores.

    Only subjects whose three terms are all present take part (the same rows the card totals).
    """
    out = []
    if total_coef == 0 or card.get("printed_total_weighted") is None:
        return out
    avg = total_weighted / total_coef
    checks = [
        ("total_weighted", card["printed_total_weighted"], total_weighted),
        ("annual_average", card["printed_average"], avg),
    ]
    for field, printed, recomputed in checks:
        if two_places(printed) != two_places(recomputed):
            out.append(
                {
                    "kind": "printed_value_mismatch",
                    "severity": "warning",
                    "learner_id": card["learner_id"],
                    "year": card["year"],
                    "card_id": card["card_id"],
                    "field": field,
                    "printed": two_places(printed),
                    "recomputed": two_places(recomputed),
                }
            )
    printed_grade = card["printed_grade"]
    if printed_grade != grade_for(avg, rules.grade_bands):
        out.append(
            {
                "kind": "printed_value_mismatch",
                "severity": "warning",
                "learner_id": card["learner_id"],
                "year": card["year"],
                "card_id": card["card_id"],
                "field": "grade",
                "printed": printed_grade,
                "recomputed": grade_for(avg, rules.grade_bands),
            }
        )
    return out


# --------------------------------------------------------------------------- subject and skill
def blocked_subjects(issues):
    return sorted(
        {
            i["subject"]
            for i in issues
            if i["severity"] == "blocking" and "subject" in i and i["kind"] != "duplicate_card"
        }
    )


def subject_result(records, subject, rules, blocked):
    """The trend for one subject (6.4, TR-1, TR-2, CA-4). `records` are the learner's records."""
    recs = sorted((r for r in records if r["subject"] == subject), key=when)
    out = {"subject": subject, "clauses": [rules.clause["evidence"], rules.clause["move"]]}
    if subject in blocked:
        return {**out, "label": "blocked", "reason": "unresolved blocking issue"}
    values = [(r, on_scale(r, rules)) for r in recs if r["score"] is not None]
    if len(values) < rules.min_scores:
        return {**out, "label": "not_enough_evidence", "scores_available": len(values)}
    window = values[-rules.window_terms :]
    first, last = window[0][1], window[-1][1]
    level = weighted_average([(rules.stage_weights[r["stage"]], v) for r, v in window])
    change = last - first
    stages = {r["stage"] for r, _ in window}
    return {
        **out,
        "label": move_label(change, rules),
        "window": [
            {"year": r["year"], "term": r["term"], "stage": r["stage"], "score": txt(v)}
            for r, v in window
        ],
        "records_in_window": len(window),
        "records_available": len(values),
        "first": txt(first),
        "last": txt(last),
        "change": txt(change),
        "change_exact": str(change),
        "level": txt(level),
        "level_exact": str(level),
        "clauses": out["clauses"]
        + (
            [rules.clause["window_average"], rules.clause["stage_weights"]]
            if len(stages) > 1
            else []
        ),
    }


def skill_values(records, area, rules):
    """Skill value per term (8.4): coefficient-weighted average of the area's subjects.

    Returns a chronological list of (year, term, stage, value or None). None if any subject of the
    area has no score in that term.
    """
    keys = sorted(
        {(r["year"], r["term"], r["stage"]) for r in records}, key=lambda k: (int(k[0][:4]), k[1])
    )
    out = []
    for year, term, stage in keys:
        pairs = []
        complete = True
        for subj in rules.areas[area]:
            rec = [
                r
                for r in records
                if r["subject"] == subj and r["year"] == year and r["term"] == term
            ]
            val = on_scale(rec[0], rules) if rec else None
            if val is None:
                complete = False
                break
            pairs.append((rules.subjects[subj], val))
        out.append((year, term, stage, weighted_average(pairs) if complete else None))
    return out


def skill_result(records, area, rules, blocked):
    """Level, gap and direction of the gap for one learning area (6.7, CA-3, CA-4, CA-5, TR-4)."""
    out = {"area": area, "clauses": [rules.area_clause[area], rules.clause["evidence"]]}
    if any(s in blocked for s in rules.areas[area]):
        return {**out, "level_name": "blocked", "reason": "a subject of this area is blocked"}
    vals = [v for v in skill_values(records, area, rules) if v[3] is not None]
    if len(vals) < rules.min_scores:
        return {**out, "level_name": "not_enough_evidence", "scores_available": len(vals)}
    window = vals[-rules.window_terms :]
    level = weighted_average([(rules.stage_weights[stage], v) for _, _, stage, v in window])
    last_stage = window[-1][2]
    low, high = rules.ranges[(last_stage, area)]
    first_year, first_term, first_stage, first_value = window[0]
    last_year, last_term, _, last_value = window[-1]
    first_low, first_high = rules.ranges[(first_stage, area)]
    gap_first = distance_to_range(first_value, first_low, first_high)
    gap_last = distance_to_range(last_value, low, high)
    diff = gap_last - gap_first
    if diff > rules.gap_threshold:
        direction = "widening"
    elif diff < -rules.gap_threshold:
        direction = "closing"
    else:
        direction = "steady"
    clauses = out["clauses"] + [
        rules.range_clause[(last_stage, area)],
        rules.clause["gap"],
        rules.clause["gap_direction"],
    ]
    if first_stage != last_stage:
        clauses.append(rules.range_clause[(first_stage, area)])
    return {
        **out,
        "stage": last_stage,
        "range": [txt(low), txt(high)],
        "window": [
            {"year": y, "term": t, "stage": st, "skill_value": txt(v)} for y, t, st, v in window
        ],
        "level": txt(level),
        "level_exact": str(level),
        "level_name": level_name(level, low, high),
        "gap": txt(distance_to_range(level, low, high)),
        "gap_exact": str(distance_to_range(level, low, high)),
        "gap_first_term": txt(gap_first),
        "gap_last_term": txt(gap_last),
        "gap_direction": direction,
        "skill_change": txt(last_value - first_value),
        "skill_trend": move_label(last_value - first_value, rules),
        "clauses": clauses,
    }


def learner_expected(cards, rules):
    """Everything the key says about one learner under one template."""
    primary = [c for c in cards if c.get("card_version", 1) == 1]
    records = cards_to_records(primary)
    issues = card_issues(cards, rules)
    blocked = blocked_subjects(issues)
    dup_years = {i["year"] for i in issues if i["kind"] == "duplicate_card"}
    if dup_years:  # a duplicated year blocks every subject until the approver picks a card
        blocked = sorted(set(blocked) | set(rules.subjects))
    return {
        "template_version": rules.version,
        "issues": issues,
        "blocked_subjects": blocked,
        "subjects": {s: subject_result(records, s, rules, blocked) for s in rules.subjects},
        "areas": {a: skill_result(records, a, rules, blocked) for a in rules.areas},
    }


def cards_to_records(cards):
    out = []
    for c in cards:
        for s in c["subjects"]:
            for t, score in enumerate(s["terms"], start=1):
                out.append(
                    {
                        "learner_id": c["learner_id"],
                        "card_id": c["card_id"],
                        "year": c["year"],
                        "stage": c["stage"],
                        "term": t,
                        "subject": s["name"],
                        "score": score,
                        "denominator": c.get("denominator", Fraction(20)),
                    }
                )
    return out
