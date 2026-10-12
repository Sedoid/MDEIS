"""Checks on the migration files themselves. No database needed, so these run everywhere."""

import re

from mdeis.db.migrate import migration_files

FORBIDDEN_WORDS = [
    "threshold",
    "pass_mark",
    "move_threshold",
    "min_scores",
    "min_terms",
    "window_terms",
    "group_minimum",
    "min_group_size",
    "stage_range",
    "gap_direction",
]


def sql_text():
    return {f.name: f.read_text(encoding="utf-8") for f in migration_files()}


def strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def test_migrations_are_numbered_without_gaps():
    files = migration_files()
    assert files, "no migration files found"
    assert all(re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", f.name) for f in files)
    numbers = [int(f.name.split("_")[0]) for f in files]
    assert numbers == list(range(1, len(numbers) + 1))


def test_no_school_rule_is_written_into_the_schema():
    for name, sql in sql_text().items():
        code = strip_comments(sql).lower()
        for word in FORBIDDEN_WORDS:
            assert word not in code, f"{name} mentions {word}"
        # a comparison with a number of two or more digits would be a rule (a limit or a band)
        assert not re.search(r"(<=|>=|<|>|between)\s*[0-9]{2,}", code), (
            f"{name} compares with a number"
        )
        assert not re.search(r"default\s+[0-9]*[2-9]\b", code), (
            f"{name} has a numeric default above 1"
        )


def test_no_migration_removes_data_or_the_diary_protection():
    for name, sql in sql_text().items():
        code = strip_comments(sql).lower()
        assert not re.search(r"\bdrop\s+(table|trigger|schema|column)\b", code), name
        assert not re.search(r"\bdelete\s+from\b", code), name
        assert not re.search(r"^\s*truncate\b", code, re.MULTILINE), (
            name
        )  # a statement, not BEFORE TRUNCATE
        assert "disable trigger" not in code, name


def test_no_date_of_birth_or_gender_column_is_ever_created():
    for name, sql in sql_text().items():
        code = strip_comments(sql).lower()
        assert not re.search(r"\b(date_of_birth|dob|birth_date|birthdate|gender|sex)\b", code), name
