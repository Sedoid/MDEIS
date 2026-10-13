"""Read one learner's stored cards and term scores from the database.

A card is one uploaded report card (a `documents` row with its printed values), or, for scores that
came without a card, one group of records for a school year. Scores are kept as printed: the tools
convert them to the rulebook's scale, this module does not.
"""

from fractions import Fraction

from .numbers import exact


def _fraction(value):
    return None if value is None else exact(value)


def year_start(year: str) -> int:
    """2025 from the school year '2025/2026'."""
    return int(year.split("/")[0])


def load_cards(conn, learner_id: str) -> list:
    """Every card of the learner, oldest first. Each card is a plain dict (see `_new_card`)."""
    rows = conn.execute(
        "SELECT assessment_id, subject, score, academic_year, stage, term, coefficient, "
        "denominator, raw_score, source_document_id, source_page, source_row FROM assessments "
        "WHERE learner_id = %s ORDER BY academic_year, term, subject, assessment_id",
        (learner_id,),
    ).fetchall()
    printed = {
        document_id: {"total_weighted": total, "average": average, "grade": grade}
        for document_id, total, average, grade in conn.execute(
            "SELECT document_id, printed_total_weighted, printed_average, printed_grade "
            "FROM report_cards WHERE learner_id = %s",
            (learner_id,),
        )
    }
    subject_rows = {}
    for document_id, subject, annual, weighted, grade, remarks in conn.execute(
        "SELECT document_id, subject, printed_annual, printed_weighted, printed_grade, remarks "
        "FROM card_subjects WHERE document_id = ANY(%s)",
        (list(printed),),
    ):
        subject_rows[(document_id, subject)] = {
            "annual": annual,
            "weighted": weighted,
            "grade": grade,
            "remark": remarks,
        }
    cards = {}
    for rid, subject, score, year, stage, term, coef, denominator, raw, doc, page, row in rows:
        key = doc if doc else (None, year, str(denominator))
        if key not in cards:
            cards[key] = _new_card(doc, year, stage, denominator, printed.get(doc))
        card = cards[key]
        entry = card["subjects"].setdefault(
            subject, _new_subject(coef, subject_rows.get((doc, subject)))
        )
        entry["terms"][term] = {
            "record_id": rid,
            "score": _fraction(score),
            "raw": raw,
            "page": page,
            "row": row,
        }
    return sorted(cards.values(), key=lambda c: (c["year_start"], c["document_id"] or ""))


def _new_card(document_id, year, stage, denominator, printed):
    return {
        "card_id": document_id or f"records-{year}",
        "document_id": document_id,
        "year": year,
        "year_start": year_start(year),
        "stage": stage,
        "denominator": _fraction(denominator),
        "printable": document_id is not None,
        "printed_total_weighted": _fraction(printed["total_weighted"]) if printed else None,
        "printed_average": _fraction(printed["average"]) if printed else None,
        "printed_grade": printed["grade"] if printed else None,
        "subjects": {},
    }


def _new_subject(coefficient, printed):
    return {
        "coef": _fraction(coefficient),
        "terms": {},
        "printed_annual": _fraction(printed["annual"]) if printed else None,
        "printed_weighted": _fraction(printed["weighted"]) if printed else None,
        "printed_grade": printed["grade"] if printed else None,
        "remark": (printed["remark"] or "") if printed else "",
    }


def term_scores(subject_entry):
    """A subject's scores on one card in term order: [(term, record id, score or None)]."""
    return [(t, e["record_id"], e["score"]) for t, e in sorted(subject_entry["terms"].items())]


ZERO = Fraction(0)
