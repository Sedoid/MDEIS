"""Store a parsed report card.

`store_report_card` runs on the connection it is given and does not commit: the caller writes the
card, the document's new status and the diary line in one transaction, so a failure leaves nothing
behind. Every score is kept as printed (`raw_score`); converting to the rulebook's scale is the
tools' job. A blank term (`-` or `ABS`) is a row with no score, never a zero. Date of birth and
gender were checked by the parser and are not here to be stored.
"""

from __future__ import annotations

from decimal import Decimal

from .report_card import Card


def school_code_of(learner_ref: str) -> str:
    """OCC from OCC/2022/0147: the part of the reference before the first slash."""
    return learner_ref.split("/")[0]


def _number(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def store_report_card(conn, document_id: str, card: Card) -> dict:
    """Write the learner (if new), the card, its subject rows and its term scores.

    Returns the learner's number and how many term-score records were written.
    """
    code = school_code_of(card.learner_ref)
    found = conn.execute(
        "SELECT learner_id FROM learners WHERE school_code = %s AND school_ref = %s",
        (code, card.learner_ref),
    ).fetchone()
    if found is None:
        found = conn.execute(
            "INSERT INTO learners (school_code, school_ref, display_name) VALUES (%s, %s, %s) "
            "RETURNING learner_id",
            (code, card.learner_ref, card.learner_name),
        ).fetchone()
    learner_id = found[0]
    conn.execute(
        "INSERT INTO report_cards (document_id, learner_id, academic_year, stage, school_name, "
        "printed_total_coefficients, printed_total_weighted, printed_average, printed_grade, "
        "absences, class_decision) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            document_id,
            learner_id,
            card.academic_year,
            card.stage,
            card.school_name,
            card.total_coefficients,
            _number(card.total_weighted),
            _number(card.annual_average),
            card.grade,
            card.absences,
            card.council_decision,
        ),
    )
    written = 0
    for row in card.rows:
        conn.execute(
            "INSERT INTO card_subjects (document_id, subject, coefficient, printed_annual, "
            "printed_weighted, printed_grade, remarks, page, \"row\") "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                document_id,
                row.subject,
                row.coefficient,
                _number(row.annual_score),
                _number(row.weighted_score),
                row.grade,
                row.remark,
                row.page,
                row.row,
            ),
        )
        cells = zip(row.terms, row.printed_terms, strict=True)
        for term, (value, printed) in enumerate(cells, start=1):
            conn.execute(
                "INSERT INTO assessments (learner_id, subject, score, academic_year, stage, term, "
                "coefficient, denominator, raw_score, source_document_id, source_page, source_row) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    learner_id,
                    row.subject,
                    _number(value),
                    card.academic_year,
                    card.stage,
                    term,
                    row.coefficient,
                    _number(card.denominator),
                    printed,
                    document_id,
                    row.page,
                    row.row,
                ),
            )
            written += 1
    return {"learner_id": learner_id, "records": written, "subjects": len(card.rows)}
