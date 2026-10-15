"""Set-up for the tool contract tests.

It builds a throwaway schema from the migrations, loads both rulebook versions, and loads the
practice cohort the way the card reader will eventually load it: one `documents` row per printed
card, one `report_cards` and `card_subjects` set with the values the card printed, and one
`assessments` row per term score, each with its card, page and row.

Nothing here is a tool. It is the stage the tools are tested on. A score is stored as printed
(`score`, `raw_score`, `denominator`): converting to the template's scale is the tools' job.
"""

import hashlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

SCHOOL_CODE = "OCC"


def database_url():
    return os.environ.get("DATABASE_URL")


def build_schema(url):
    """Create a throwaway schema and apply every migration in it. Returns (name, its url)."""
    name = "mdeis_tools_" + os.urandom(4).hex()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{name}"')
    scoped = make_conninfo(url, options=f"-c search_path={name},public")
    built = subprocess.run(
        [sys.executable, "-m", "mdeis.db.migrate"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": scoped},
    )
    if built.returncode != 0:
        drop_schema(url, name)
        raise RuntimeError(f"could not build the test schema:\n{built.stderr}")
    return name, scoped


def drop_schema(url, name):
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')


def load_rulebooks(scoped_url, templates_dir):
    """Both versions, the stricter first so that v1 ends up the active one."""
    from mdeis import rulebook

    with psycopg.connect(scoped_url) as conn:
        for name in ("v2-strict", "v1"):
            rulebook.load_file(conn, Path(templates_dir) / f"{name}.yaml", url=scoped_url)


def number(text):
    return None if text is None else Decimal(text)


def load_cohort(scoped_url, learners, cards):
    """learners: cohort/learners.json as a list. cards: cohort/cards.json as a list."""
    with psycopg.connect(scoped_url) as conn:
        for ln in learners:
            conn.execute(
                "INSERT INTO learners (learner_id, school_code, school_ref, display_name) "
                "VALUES (%s, %s, %s, %s)",
                (ln["gen_id"], SCHOOL_CODE, ln["school_ref"], ln["name"]),
            )
        by_ref = {ln["school_ref"]: ln["gen_id"] for ln in learners}
        for card in cards:
            learner_id = by_ref[card["learner_id"]]
            document_id = None
            if card["printable"]:
                document_id = conn.execute(
                    "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
                    "uploader_id, uploader_name, status) "
                    "VALUES ('results', %s, %s, %s, 1, 'practice', 'Practice data', 'done') "
                    "RETURNING document_id",
                    (
                        card["card_id"] + ".pdf",
                        card["card_id"] + ".bin",
                        hashlib.sha256(card["card_id"].encode()).hexdigest(),
                    ),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO report_cards (document_id, learner_id, academic_year, stage, "
                    "school_name, printed_total_coefficients, printed_total_weighted, "
                    "printed_average, printed_grade, absences, class_decision) "
                    "VALUES (%s, %s, %s, %s, 'Practice school', %s, %s, %s, %s, %s, %s)",
                    (
                        document_id,
                        learner_id,
                        card["year"],
                        card["stage"],
                        card["printed_total_coef"],
                        number(card["printed_total_weighted"]),
                        number(card["printed_average"]),
                        card["printed_grade"],
                        card["absences"],
                        card["decision"],
                    ),
                )
            for row, subject in enumerate(card["subjects"], start=1):
                if card["printable"]:
                    conn.execute(
                        "INSERT INTO card_subjects (document_id, subject, coefficient, "
                        "printed_annual, printed_weighted, printed_grade, remarks, page, \"row\") "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)",
                        (
                            document_id,
                            subject["name"],
                            subject["coef"],
                            number(subject["printed_annual"]),
                            number(subject["printed_weighted"]),
                            subject["printed_grade"],
                            subject["remark"],
                            row,
                        ),
                    )
                for term, raw in enumerate(subject["terms"], start=1):
                    conn.execute(
                        "INSERT INTO assessments (learner_id, subject, score, academic_year, "
                        "stage, term, coefficient, denominator, raw_score, source_document_id, "
                        "source_page, source_row) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            learner_id,
                            subject["name"],
                            number(raw),
                            card["year"],
                            card["stage"],
                            term,
                            subject["coef"],
                            number(card["denominator"]),
                            raw,
                            document_id,
                            1 if document_id else None,
                            row if document_id else None,
                        ),
                    )
        conn.commit()
