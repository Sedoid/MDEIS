"""Class-level results for the lesson-adapter fixtures: an independent answer key.

    python3 synthetic/lesson_fixtures.py          # writes lessons/class-results.json
    python3 synthetic/lesson_fixtures.py --check  # compares the file on disk, writes nothing

(lessons/ is data/synthetic/lessons/)

It reads data/synthetic/cohort/ and templates/v1.yaml directly. It imports nothing from
src/mdeis and nothing from synthetic/expected.py, so it is a second, separate worked answer.
Every threshold, weight, range and subject list comes from the template; none is typed here.

The rules it applies (written out so a reader can check them by hand):

1. The class is the learners who have a record at the stage in the school year (2025/2026).
2. Only primary cards count (card_version 1). Scores are put on the template's scale (SC-1)
   as score * scale / denominator.
3. A learner's area value in one term is the average of the area's subjects (SK-n), each
   weighted by its coefficient (WT-1). A term with a blank subject has no area value.
4. The window is the last TR-1 window_terms area values up to the end of the given term of the
   school year. The level is their average, each weighted by its stage weight (SW-1).
5. Fewer than TR-1 min_scores area values: not_enough_evidence.
6. Otherwise the level is placed against the range of the class stage (ST-n): below the bottom
   is below_expected_range, above the top is above_expected_range, bounds included is within.
7. blocked: any subject of the area has a blank score or a score above its denominator or below
   zero on any of the learner's cards (any year or term), or the learner has two cards for one
   year. A duplicated year blocks every subject. This check comes before rule 5.
8. A group smaller than GM-1 min_group_size is marked not shown (the counts are still written
   for the key; a test must not expect them to be shown).

Where this may differ from the product: see data/synthetic/lessons/README.md.
"""

import argparse
import csv
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "data" / "synthetic" / "cohort"
OUT = ROOT / "data" / "synthetic" / "lessons" / "class-results.json"
YEAR = "2025/2026"
STAGES = ("Form 1", "Form 2", "Form 3")
AREAS = ("literacy", "numeracy")
TERMS = (2, 3)
BINS = ("below_expected_range", "within_expected_range", "above_expected_range",
        "not_enough_evidence", "blocked")  # fmt: skip


def number(x):
    return Fraction(str(x))


def read_rules(path):
    t = yaml.safe_load(path.read_text())
    one = lambda group, key: t[group][0][key]  # noqa: E731
    return {
        "scale": number(one("scales", "max")),
        "coef": {k: number(v) for k, v in one("coefficients", "subjects").items()},
        "weight": {k: number(v) for k, v in one("stage_weights", "stages").items()},
        "areas": {a["area"]: a["subjects"] for a in t["areas"]},
        "area_clause": {a["area"]: a["id"] for a in t["areas"]},
        "range": {
            (s["stage"], s["area"]): (number(s["range"][0]), number(s["range"][1]))
            for s in t["stage_expectations"]
        },
        "range_clause": {(s["stage"], s["area"]): s["id"] for s in t["stage_expectations"]},
        "min_scores": one("trend", "min_scores"),
        "window": one("trend", "window_terms"),
        "min_group": one("group_minimum", "min_group_size"),
        "ids": {"window": t["trend"][0]["id"], "weights": t["stage_weights"][0]["id"],
                "coef": t["coefficients"][0]["id"], "group": t["group_minimum"][0]["id"],
                "gap": next(c["id"] for c in t["calculations"] if "gap" in c)},
        "version": f"v{t['version']}",
    }  # fmt: skip


def read_records():
    """learner_ref -> list of primary-card rows, plus the set of learners with a duplicate year."""
    rows, cards = defaultdict(list), defaultdict(set)
    for r in csv.DictReader(open(COHORT / "records.csv")):
        cards[(r["learner_ref"], r["year"])].add(r["card_id"])
        if r["card_version"] == "1":
            rows[r["learner_ref"]].append(r)
    duplicated = {ref for (ref, _), ids in cards.items() if len(ids) > 1}
    return rows, duplicated


def level_for(rows, area, stage, term, rules, duplicated, ref):
    """One learner's bin for one area, stage and term (rules 3 to 7)."""
    subjects = rules["areas"][area]
    bad = ref in duplicated
    for r in rows:
        if r["subject"] in subjects:
            score = r["raw_score"]
            if score == "" or number(score) > number(r["denominator"]) or number(score) < 0:
                bad = True
    if bad:
        return "blocked"
    by_time = defaultdict(dict)  # (year start, term) -> {subject: (score on scale, stage)}
    for r in rows:
        if r["subject"] in subjects:
            when = (int(r["year"][:4]), int(r["term"]))
            on_scale = number(r["raw_score"]) * rules["scale"] / number(r["denominator"])
            by_time[when][r["subject"]] = (on_scale, r["stage"])
    cutoff = (int(YEAR[:4]), term)
    values = []  # (when, area value, stage), oldest first
    for when in sorted(w for w in by_time if w <= cutoff):
        got = by_time[when]
        if set(got) == set(subjects):
            total = sum(rules["coef"][s] * got[s][0] for s in subjects)
            weights = sum(rules["coef"][s] for s in subjects)
            values.append((when, total / weights, got[subjects[0]][1]))
    if len(values) < rules["min_scores"]:
        return "not_enough_evidence"
    last = values[-rules["window"]:]
    stage_weights = [rules["weight"][st] for _, _, st in last]
    weighted = sum(w * v for w, (_, v, _) in zip(stage_weights, last, strict=True))
    level = weighted / sum(stage_weights)
    low, high = rules["range"][(stage, area)]
    if level < low:
        return "below_expected_range"
    return "above_expected_range" if level > high else "within_expected_range"


def build():
    rules = read_rules(ROOT / "templates" / "v1.yaml")
    rows, duplicated = read_records()
    classes = []
    for stage in STAGES:
        members = [
            ref
            for ref, rs in sorted(rows.items())
            if any(r["year"] == YEAR and r["stage"] == stage for r in rs)
        ]
        for area in AREAS:
            for term in TERMS:
                counts = dict.fromkeys(BINS, 0)
                for ref in members:
                    counts[level_for(rows[ref], area, stage, term, rules, duplicated, ref)] += 1
                classes.append({
                    "stage": stage, "area": area, "term": term, "group_size": len(members),
                    "shown": len(members) >= rules["min_group"], "counts": counts,
                    "clause_ids": [rules["area_clause"][area], rules["range_clause"][(stage, area)],
                                   rules["ids"]["window"], rules["ids"]["weights"],
                                   rules["ids"]["gap"], rules["ids"]["group"]],
                    "expected_range": [str(n) for n in rules["range"][(stage, area)]],
                })  # fmt: skip
    return {
        "about": "Class-level answer key for the lesson fixtures. Synthetic. "
                 "No names, no learner numbers.",
        "school_year": YEAR, "template_version": rules["version"],
        "minimum_group_size": rules["min_group"], "primary_term": 2, "classes": classes,
    }  # fmt: skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare with the file, write nothing")
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if ap.parse_args().check:
        same = OUT.read_text() == text
        raise SystemExit(0 if same else "class-results.json differs from the script")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
