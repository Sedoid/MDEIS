"""apply_edits: the revised text, per-edit before and after, and the diff (T-060, slice 1).

E-52: the text outside the edits is identical after apply. E-61: the same lesson and edits give the
same revised text and diff. Expected texts are typed by hand.
"""

import hashlib
import random

import pytest
from lessonkit import A_ASK, A_PLENARY, A_STARTER, LESSON, OBJECTIVE, add_after, edit, lesson

from mdeis.lessons.apply import ApplyError, apply_edits
from mdeis.lessons.parse import parse_lesson

REWORDED = "Ask what the passage tells us and why."


def test_a_reword_replaces_only_the_anchor_and_every_other_character_stays():
    revised = apply_edits(lesson(), [edit()])
    assert revised.text == LESSON.replace(A_ASK, REWORDED)
    assert revised.text.count(REWORDED) == 1


def test_an_added_example_is_inserted_straight_after_its_anchor():
    added = " Use a story the class knows."
    revised = apply_edits(lesson(), [add_after(A_STARTER, added)])
    assert revised.text == LESSON.replace(A_STARTER, A_STARTER + added)


def test_the_hand_worked_example_with_two_edits():
    two = [
        edit(id="e2", anchor=A_PLENARY, replacement=A_PLENARY + " Ask for one reason each.",
             type="add_check"),
        edit(id="e1"),
    ]
    expected = (
        "Objective: Learners explain the main idea of a short text.\n\n"
        "## Starter\n"
        "Read the short passage aloud to the class. Ask what the passage tells us and why.\n\n"
        "## Main activity\n"
        "Learners read the passage in pairs and underline the key sentence.\n"
        "Then they write one sentence that sums up the passage.\n\n"
        "## Plenary\n"
        "Share the sentences and check them against the passage. Ask for one reason each.\n"
    )
    assert apply_edits(lesson(), two).text == expected


def test_the_same_edits_give_the_same_text_and_diff_every_time_and_in_any_order():
    edits = [edit(), add_after(A_PLENARY, " Close with a question.", id="e2", type="add_check")]
    first = apply_edits(lesson(), edits)
    assert apply_edits(lesson(), edits) == first
    assert apply_edits(lesson(), list(reversed(edits))) == first


def test_a_subset_of_the_edits_applies_the_same_way():
    both = [edit(), add_after(A_PLENARY, " Close with a question.", id="e2", type="add_check")]
    only_second = apply_edits(lesson(), both[1:])
    assert only_second.text == LESSON.replace(A_PLENARY, A_PLENARY + " Close with a question.")
    assert apply_edits(lesson(), []).text == LESSON


def test_the_objective_is_carried_unchanged_with_its_hash():
    revised = apply_edits(lesson(), [edit()])
    assert revised.objective == OBJECTIVE
    assert revised.objective_sha256 == hashlib.sha256(OBJECTIVE.encode("utf-8")).hexdigest()
    assert revised.text_sha256 == hashlib.sha256(revised.text.encode("utf-8")).hexdigest()


def test_each_change_records_before_after_and_where_it_sits_in_both_texts():
    edits = [edit(id="e2", anchor=A_PLENARY, type="add_check", replacement=A_PLENARY + " More."),
             edit(id="e1")]
    revised = apply_edits(lesson(), edits)
    assert [c.edit_id for c in revised.changes] == ["e1", "e2"]  # in document order
    for change in revised.changes:
        assert LESSON[change.start : change.end] == change.before
        assert revised.text[change.new_start : change.new_end] == change.after
    first = revised.changes[0]
    assert (first.before, first.after) == (A_ASK, REWORDED)
    assert first.new_start == first.start  # nothing before it moved


def test_the_diff_lists_the_changed_lines_and_the_unchanged_runs_between_them():
    revised = apply_edits(lesson(), [edit()])
    ops = [(d.op, d.old, d.new) for d in revised.diff]
    changed = [o for o in ops if o[0] != "equal"]
    assert changed == [
        (
            "replace",
            ("Read the short passage aloud to the class. Ask what the passage is about.\n",),
            (f"Read the short passage aloud to the class. {REWORDED}\n",),
        )
    ]
    assert ops[0][0] == "equal"
    # every line of the original and of the revision appears exactly once in the diff
    assert "".join("".join(o[1]) for o in ops if o[0] in ("equal", "replace", "delete")) == LESSON
    kept_new = "".join("".join(o[2]) for o in ops if o[0] in ("equal", "replace", "insert"))
    assert kept_new == revised.text


def test_an_edit_whose_anchor_is_not_in_the_notes_is_an_error_not_a_guess():
    with pytest.raises(ApplyError, match="not found"):
        apply_edits(lesson(), [edit(anchor="This sentence is not there.", replacement="x")])


def test_an_anchor_that_occurs_twice_is_an_error_not_a_guess():
    twice = parse_lesson("Repeat me.\n\n## B\nRepeat me.\n", "the aim")
    with pytest.raises(ApplyError, match="more than once"):
        apply_edits(twice, [edit(anchor="Repeat me.", replacement="Changed.")])


def test_two_edits_that_overlap_are_an_error():
    a = edit(id="e1", anchor="Read the short passage aloud", replacement="Read the passage aloud")
    b = edit(id="e2", anchor="passage aloud to the class.", replacement="text aloud to the class.")
    with pytest.raises(ApplyError, match="overlap"):
        apply_edits(lesson(), [a, b])


def test_edits_that_only_touch_are_not_overlapping():
    a = edit(id="e1", anchor="Read the short passage aloud", replacement="Read the passage aloud")
    b = edit(id="e2", anchor=" to the class.", replacement=" to everyone.")
    revised = apply_edits(lesson(), [a, b])
    assert "Read the passage aloud to everyone." in revised.text


def test_apply_does_not_change_the_lesson_it_is_given():
    parsed = lesson()
    apply_edits(parsed, [edit()])
    assert parsed.text == LESSON


# --------------------------------------------------------------------------- E-52, many cases
def random_case(rng):
    """A text of unique fixed-width words, and edits at positions the generator knows, so the
    expected text is built without reading apply_edits."""
    words = [f"w{n:04d}" for n in rng.sample(range(10_000), rng.randint(8, 40))]
    separators = [rng.choice([" ", " ", "\n", "\n\n"]) for _ in words]
    pieces, spans, cursor = [], {}, 0
    for word, sep in zip(words, separators, strict=True):
        pieces.append(word + sep)
        spans[word] = (cursor, cursor + len(word))
        cursor += len(word) + len(sep)
    text = "".join(pieces)
    chosen = sorted(rng.sample(words, rng.randint(1, min(6, len(words)))), key=lambda w: spans[w])
    return text, spans, chosen


@pytest.mark.parametrize("seed", range(200))
def test_the_text_outside_the_edits_is_byte_identical_for_many_random_edit_sets(seed):
    rng = random.Random(seed)
    text, spans, chosen = random_case(rng)
    edits, expected, cursor = [], [], 0
    for n, word in enumerate(chosen, start=1):
        kind = rng.choice(["reword", "add_example", "add_check"])
        tail = "".join(rng.choice("abc xyz\n") for _ in range(rng.randint(1, 8)))
        replacement = f"R{n}{tail}" if kind == "reword" else word + tail
        edits.append(edit(id=f"e{n}", type=kind, anchor=word, replacement=replacement))
        start, end = spans[word]
        expected += [text[cursor:start], replacement]
        cursor = end
    expected.append(text[cursor:])
    rng.shuffle(edits)  # the order the model gave them must not matter
    parsed = parse_lesson(text, "the aim")
    revised = apply_edits(parsed, edits)
    assert revised.text == "".join(expected)
    assert parsed.text == text
    # the length changes by exactly the sum of the replacements minus the anchors
    delta = sum(len(e.replacement) - len(e.anchor_quote) for e in edits)
    assert len(revised.text) == len(text) + delta
