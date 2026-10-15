"""Make the practice school: cohort, answer key and the files that go with them.

    python3 synthetic/generate.py --seed 42 --out data/synthetic

Same seed, same bytes. Nothing here imports anything from src/mdeis: the rules are read straight
from the template YAML files (synthetic/rules.py) and the answers are worked out by
synthetic/expected.py, so the tests that compare the system with this key are independent of it.

What it writes under --out:

    cards/*.pdf            report cards (--cards demo or all), each with a .truth.json beside it
    cards_bad/             six cards that must be refused, and expected_failures.json
    methodology/           changed copies of the methodology and what differs
    cohort/learners.json   who the learners are (no tags, nothing about the planted pattern)
    cohort/cards.json      every card as printed: term scores, annual scores, totals, remarks
    cohort/records.csv     one row per term score, flat
    truth/answer_key.json  the correct result for every learner under each template
    truth/injected_problems.json   what was planted, and the hidden pattern (kept apart on purpose)
    truth/fixtures.json    the named fixture learners and the tests they serve
    MANIFEST.sha256        a hash of every file written, so two runs can be compared
"""

import argparse
import csv
import hashlib
import io
import json
import sys
from fractions import Fraction
from pathlib import Path

if __package__ in (None, ""):  # run as a script: python3 synthetic/generate.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synthetic.cohort import CURRENT_YEAR, build_cohort  # noqa: E402
from synthetic.expected import learner_expected  # noqa: E402
from synthetic.hand_checks import check as hand_check  # noqa: E402
from synthetic.maths import two_places  # noqa: E402
from synthetic.rules import load_rules  # noqa: E402

TEMPLATES = ("v1", "v2-strict")  # the normal rulebook first; the cohort is built from it
ASSUMPTIONS = [
    "Severity is the key's own reading. missing_term, value_out_of_range and duplicate_card are "
    "blocking; printed_value_mismatch, note_conflict and denominator_unexpected are warnings. "
    "docs/SPEC.md 5.1 names the kinds but not the severities.",
    "A duplicated school year blocks every subject until the approver chooses a card.",
    "A score recorded out of another denominator is converted to the template's scale "
    "(score x scale / denominator) and raises a denominator_unexpected warning.",
    "The window is the last window_terms term scores that exist for the subject (or, for a skill, "
    "the last window_terms terms in which every subject of the area has a score). Fewer than "
    "min_scores means not_enough_evidence.",
    "The level is the stage-weighted average of the window (CA-4). The gap is the distance from "
    "the level to the nearest edge of the range for the stage of the last term (CA-5). The gap "
    "direction compares the first term's gap (against its own stage's range) with the last term's "
    "gap, using the TR-4 threshold.",
    "A change of exactly the threshold is not a move (TR-2 says 'more than').",
]


# --------------------------------------------------------------------------- writing
def plain(value):
    """Make a value ready for JSON: fractions become text with two decimals."""
    if isinstance(value, Fraction):
        return two_places(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def dump_json(value):
    return json.dumps(plain(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def card_json(card):
    out = dict(card)
    out["subjects"] = [dict(s, coef=int(s["coef"])) for s in card["subjects"]]
    if "printed_total_coef" in out:
        out["printed_total_coef"] = int(out["printed_total_coef"])
    return out


def records_csv(learners):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "learner_ref", "card_id", "card_version", "year", "stage", "term", "subject",
            "coefficient", "raw_score", "denominator",
        ]
    )  # fmt: skip
    for ln in learners:
        for c in ln["cards"]:
            for s in c["subjects"]:
                for term, score in enumerate(s["terms"], start=1):
                    w.writerow(
                        [
                            ln["ref"], c["card_id"], c["card_version"], c["year"], c["stage"],
                            term, s["name"], int(s["coef"]),
                            "" if score is None else two_places(score), int(c["denominator"]),
                        ]
                    )  # fmt: skip
    return buf.getvalue()


def answer_key(seed, learners, rules_by_version):
    key = {
        "seed": seed,
        "current_school_year": f"{CURRENT_YEAR}/{CURRENT_YEAR + 1}",
        "assumptions": ASSUMPTIONS,
        "templates": {
            v: {
                "window_terms": r.window_terms,
                "min_scores": r.min_scores,
                "move_threshold": r.move_threshold,
                "gap_threshold": r.gap_threshold,
                "ranges": {f"{st}/{ar}": list(rg) for (st, ar), rg in r.ranges.items()},
                "stage_weights": r.stage_weights,
            }
            for v, r in rules_by_version.items()
        },
        "learners": {},
    }
    for ln in learners:
        key["learners"][ln["ref"]] = {
            "gen_id": ln["gen_id"],
            "name": ln["name"],
            "stage": ln["stage"],
            "fixture": ln["fixture"],
            "tests": ln["tests"],
            "by_template": {
                v: learner_expected(ln["cards"], r) for v, r in rules_by_version.items()
            },
        }
    return key


DEMO_PER_STAGE = {
    "Form 3": 3,
    "Form 2": 3,
    "Form 1": 2,
}  # the eight demo learners, besides fixtures


def demo_learners(learners):
    """The fixtures plus eight others (all their years), picked by order of ID."""
    chosen = [ln for ln in learners if ln["fixture"]]
    for stage, n in DEMO_PER_STAGE.items():
        pool = [ln for ln in learners if ln["stage"] == stage and not ln["fixture"]]
        chosen += pool[:n]
    return chosen


def card_stem(card):
    """File name without the extension: first_last_formN_2025-2026 (and _card2 for a duplicate)."""
    first, _, last = card["name"].lower().partition(" ")
    start = card["year"][:4]
    form = card["stage"].lower().replace(" ", "")
    stem = f"{first}_{last.replace(' ', '_')}_{form}_{start}-{int(start) + 1}"
    return stem + ("_card2" if card["card_version"] > 1 else "")


def card_files(learners, rules, which):
    """PDFs with a truth sheet beside each, for 'demo' or 'all'. Also the bad cards."""
    from synthetic.bad_cards import bad_cards
    from synthetic.cards import render_card

    files = {}
    chosen = learners if which == "all" else demo_learners(learners)
    for ln in chosen:
        for card in ln["cards"]:
            if not card["printable"]:
                continue
            data, truth = render_card(card, rules)
            files[f"cards/{card_stem(card)}.pdf"] = data
            files[f"cards/{card_stem(card)}.truth.json"] = dump_json(truth).encode("utf-8")
    source = next(ln for ln in learners if ln["fixture"] == "FX-01")["cards"][-1]
    expected = {}
    for name, (data, what) in bad_cards(source, rules).items():
        files[f"cards_bad/{name}"] = data
        expected[name] = what
    files["cards_bad/expected_failures.json"] = dump_json(expected).encode("utf-8")
    return files


def methodology_files(source, template_path):
    """Changed copies of the methodology document, what differs, and each file's section list."""
    from synthetic.methodology import build

    base = Path(source).read_text(encoding="utf-8")
    texts, changes, section_lists = build(base, template_path)
    files = {f"methodology/{name}": text.encode("utf-8") for name, text in texts.items()}
    files["methodology/methodology-changes.json"] = dump_json(changes).encode("utf-8")
    for name, found in section_lists.items():
        stem = name[: -len(".md")]
        files[f"methodology/{stem}.sections.json"] = dump_json(found).encode("utf-8")
    return files


def build_outputs(seed, templates_dir, cards="none", methodology=None):
    """Return {relative path: bytes} for every file. Pure: nothing touches the disk.

    `cards` is "none", "demo" (fixtures and eight more learners) or "all" (every card).
    `methodology` is the path of methodology-v2.0.md; when given, the changed copies are made too.
    """
    rules = {v: load_rules(Path(templates_dir) / f"{v}.yaml") for v in TEMPLATES}
    learners, problems, hidden = build_cohort(seed, rules[TEMPLATES[0]])
    key = answer_key(seed, learners, rules)
    wrong = hand_check(key)
    if wrong:  # the key's code and the pencil answers disagree: one of them is wrong
        raise SystemExit("hand checks failed:\n  " + "\n  ".join(wrong))
    files = {
        "cohort/learners.json": dump_json(
            [
                {
                    "gen_id": ln["gen_id"],
                    "school_ref": ln["ref"],
                    "name": ln["name"],
                    "gender": ln["gender"],
                    "stage": ln["stage"],
                    "entry_year": ln["entry_year"],
                }
                for ln in learners
            ]
        ),  # fmt: skip
        "cohort/cards.json": dump_json([card_json(c) for ln in learners for c in ln["cards"]]),
        "cohort/records.csv": records_csv(learners),
        "truth/answer_key.json": dump_json(key),
        "truth/injected_problems.json": dump_json(
            {
                "seed": seed,
                "injected": problems,
                "hidden_pattern": {
                    "what": "literacy subjects (English, French, Literature in English) sit in the "
                    "Form 1 expected range, flat, across every term of the learner's cards",
                    "learners": hidden,
                    "note": "This is not tagged anywhere in cohort/. It is only here.",
                },
            }
        ),
        "truth/fixtures.json": dump_json(
            [
                {
                    "handle": ln["fixture"],
                    "school_ref": ln["ref"],
                    "gen_id": ln["gen_id"],
                    "name": ln["name"],
                    "stage": ln["stage"],
                    "tests": ln["tests"],
                    "records_only": ln["records_only"],
                }
                for ln in learners
                if ln["fixture"]
            ]
        ),  # fmt: skip
    }
    out = {k: v.encode("utf-8") for k, v in files.items()}
    if cards != "none":
        out.update(card_files(learners, rules[TEMPLATES[0]], cards))
    if methodology:
        out.update(methodology_files(methodology, Path(templates_dir) / f"{TEMPLATES[0]}.yaml"))
    return out, learners, rules


def manifest(files):
    lines = [f"{sha256(data)}  {path}" for path, data in sorted(files.items())]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_all(out, files):
    out = Path(out)
    for path, data in sorted(files.items()):
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument(
        "--templates", default="templates", help="folder with v1.yaml and v2-strict.yaml"
    )
    ap.add_argument(
        "--cards",
        choices=("none", "demo", "all"),
        default="demo",
        help="print report cards as PDFs: the fixtures and 8 more learners (the default), "
        "everyone, or none",
    )
    ap.add_argument(
        "--methodology",
        default="data/synthetic/methodology/methodology-v2.0.md",
        help="the base methodology document (Markdown); changed copies are made from it",
    )
    args = ap.parse_args(argv)
    source = args.methodology if Path(args.methodology).exists() else None
    files, learners, _ = build_outputs(args.seed, args.templates, args.cards, source)
    files["MANIFEST.sha256"] = manifest(files)
    write_all(args.out, files)
    print(f"seed {args.seed}: {len(learners)} learners, {len(files)} files written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
