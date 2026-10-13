"""The pieces of the citation check that need no database. Expected values are typed by hand."""

from decimal import Decimal

from mdeis.tools import citations

IDS = {"A-000101", "A-000102", "TR-2"}
NUMBERS = {Decimal("2"), Decimal("11")}
BANNED = ["weak", "lazy"]


def reasons(body):
    return [p["reason"] for p in citations.problems_in(body, IDS, NUMBERS, BANNED)]


def test_a_sentence_is_split_after_its_full_stop_but_not_inside_a_decimal():
    body = "English fell by 2.00 points [A-000101]. Maths is steady [A-000102]."
    assert citations.sentences(body) == [
        "English fell by 2.00 points [A-000101].",
        "Maths is steady [A-000102].",
    ]


def test_ids_are_not_counted_as_numbers():
    assert citations.numbers_in("fell by 2.00 [A-000101] [TR-2] in Form 3") == {
        Decimal("2.00"),
        Decimal("3"),
    }


def test_a_clean_sentence_has_no_problem():
    assert reasons("English fell by 2.00 points [A-000101] [TR-2].") == []


def test_each_kind_of_problem_has_its_own_name():
    assert reasons("English fell by 2.00 points.") == ["uncited_sentence"]
    assert reasons("English fell by 2.00 points [A-000999].") == ["unknown_record"]
    assert reasons("English fell by 2.00 points [TR-9].") == ["unknown_clause"]
    assert reasons("English fell by 2.50 points [A-000101].") == ["unknown_number"]


def test_a_banned_word_is_found_whatever_its_case_and_only_as_a_whole_word():
    assert "banned_word" in reasons("English is WEAK [A-000101].")
    assert "banned_word" not in reasons("English is weaker [A-000101].")


def test_a_number_written_the_other_way_is_still_the_same_number():
    assert reasons("Scores moved by 2 points [A-000101].") == []
