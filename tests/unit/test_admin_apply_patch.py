"""The admin command `apply-template-patch` (T-071): its wiring and its refusals that need no
database. The step itself is tested against a real database in tests/unit/test_build_db.py."""

import pytest

from mdeis import admin
from mdeis.measure_build import apply


def test_the_command_is_listed_and_routed_by_the_admin_entry_point(capsys):
    assert "apply-template-patch" in admin.USAGE
    assert admin.main([]) == 2
    assert "apply-template-patch" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--patch", "TP-000001"],
        ["--patch", "TP-000001", "--version-name", "baobab-v2"],  # no --by: a typed name is needed
        ["--version-name", "baobab-v2", "--by", "Ada"],  # no patch
    ],
)
def test_the_command_needs_a_patch_a_version_name_and_the_name_of_the_person(argv):
    with pytest.raises(SystemExit):
        admin.apply_template_patch(argv)


def test_the_command_has_no_approver_argument_and_no_way_to_name_the_teacher():
    parser_source = open(admin.__file__, encoding="utf-8").read()
    start = parser_source.index("def apply_template_patch")
    body = parser_source[start : parser_source.index("def main", start)]
    for forbidden in ("--approver", "--teacher", "--confirmed", "--name"):
        assert forbidden not in body, forbidden


def test_without_a_template_folder_it_refuses_before_touching_the_database(monkeypatch, capsys):
    monkeypatch.delenv(apply.TEMPLATE_DIR_ENV, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code = admin.apply_template_patch(
        ["--patch", "TP-000001", "--version-name", "baobab-v2", "--by", "Ada"]
    )
    assert code == 1 and apply.TEMPLATE_DIR_ENV in capsys.readouterr().err
