"""Shared pieces of the measure-build tests (T-071): the Baobab methodology and template read from
the fixtures, the build inputs made from them, and the functions and cases a scripted model hands
back. Nothing here calls a model or a network.

The expected numbers in the cases are typed by hand from data/synthetic/methodology/
methodology-baobab.md (section 6.4 worked example and the section 9 cases B-03, B-06, B-07, B-08),
never taken from the code under test. The quotes are copied from that file."""

import copy
import json
from pathlib import Path

from mdeis.core.template import load_template
from mdeis.ingest import methodology
from mdeis.measure_build import engine
from mdeis.measure_build.config import load_build_rules
from mdeis.sandbox.config import load_rules

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "data" / "synthetic" / "methodology" / "methodology-baobab.md"
CASES = ROOT / "data" / "synthetic" / "methodology" / "methodology-baobab.cases.json"
BAOBAB_TEMPLATE = ROOT / "templates" / "baobab-v1.yaml"
OCC_TEMPLATE = ROOT / "templates" / "v1.1.yaml"

RULES = load_build_rules()
SANDBOX = load_rules()


def parsed():
    return methodology.parse_markdown(DOC.read_text(encoding="utf-8"))


def content_of(path):
    return load_template(path).model_dump(mode="json")


def baobab_content():
    return content_of(BAOBAB_TEMPLATE)


def occ_content():
    return content_of(OCC_TEMPLATE)


def inputs(content=None, issues=(), first=True, version_name="baobab-v1", sections=None):
    return engine.make_inputs(
        document_id="D-000100",
        sections=sections if sections is not None else parsed().sections,
        issues=issues,
        first_methodology=first,
        template_version=version_name,
        content=copy.deepcopy(content if content is not None else baobab_content()),
        rules=RULES,
    )


def section_text(section_id):
    return next(s.text for s in parsed().sections if s.id == section_id)


class Counter:
    """MP numbers for a test, in the order asked."""

    def __init__(self, start=1):
        self.n = start - 1

    def __call__(self):
        self.n += 1
        return self.n


# --- what the model returns for rules (JSON-ready dicts) -------------------------------------
MOVED_QUOTE = "The pattern is moved up when the change is 10 points or more."
WINDOW_QUOTE = "Window | The last 3 term scores in one subject"
EVIDENCE_QUOTE = "Minimum evidence | 2 scores"


def rule_moved(**over):
    return {
        "setting": "other", "name": "moved_at_or_above", "value_type": "integer", "value": 10,
        "unit": "points", "quote": MOVED_QUOTE, **over,
    }  # fmt: skip


def rule_window(**over):
    return {
        "setting": "window", "name": "window", "value_type": "integer", "value": 3,
        "unit": "terms", "quote": WINDOW_QUOTE, **over,
    }  # fmt: skip


def rule_evidence(**over):
    return {
        "setting": "minimum_evidence", "name": "minimum_evidence", "value_type": "integer",
        "value": 2, "unit": "scores", "quote": EVIDENCE_QUOTE, **over,
    }  # fmt: skip


def extraction(*rules):
    return json.dumps({"rules": list(rules)})


# --- the function the model writes for "moved", and cases from the document -------------------
MOVED_SOURCE = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    window = int(params["TR-1.window_terms"])
    minimum = int(params["TR-1.min_scores"])
    threshold = Fraction(params["MP-1"])
    scores = [Fraction(r["score"]) for r in records]
    used = scores[len(scores) - window:] if len(scores) > window else scores
    if len(used) < minimum:
        return {"enough_evidence": 0, "scores_used": len(used), "moved_up": 0, "moved_down": 0}
    change = used[len(used) - 1] - used[0]
    return {
        "enough_evidence": 1,
        "scores_used": len(used),
        "change": change,
        "moved_up": 1 if change >= threshold else 0,
        "moved_down": 1 if change <= -threshold else 0,
    }
'''

# The same function with the threshold written into the code (a literal the static check refuses).
HARDCODED_SOURCE = MOVED_SOURCE.replace("change >= threshold", "change >= 10")

# The function with the window and the minimum not read at all (the template values are ignored).
IGNORES_WINDOW_SOURCE = MOVED_SOURCE.replace(
    'window = int(params["TR-1.window_terms"])', "window = 1"
).replace("len(scores) > window", "len(scores) > 1")


def scores(*values):
    return [{"score": str(v)} for v in values]


def case(name, quote, values, expected, source="document_example"):
    test = {"name": name, "source": source, "records": scores(*values), "expected": expected}
    if quote is not None:
        test["quote"] = quote
    return test


def moved_tests():
    """Worked by hand. Window 3, minimum evidence 2, moved when the change is 10 or more."""
    return [
        case(  # section 6.4 worked example: 52, 58, 63, change 11, moved up
            "worked_example_6_4",
            "The scores, oldest first, were 52, 58 and 63. The change was 63 minus 52, which "
            "is 11, so the pattern is moved up.",
            (52, 58, 63), {"change": "11", "moved_up": "1"},
        ),
        case(  # B-06: two scores, change 10, the boundary counts
            "case_b06_boundary",
            "English, 2 term scores, oldest first: 60, 70 | Minimum evidence met, so both scores "
            "are used. Moved up (change 10, boundary included).",
            (60, 70), {"scores_used": "2", "change": "10", "moved_up": "1"},
        ),
        case(  # B-03: change -8, not moved
            "case_b03_not_moved",
            "Science, 3 term scores, oldest first: 70, 66, 62 | Steady (spread 8, boundary "
            "included). Change -8, so not moved.",
            (70, 66, 62), {"change": "-8", "moved_up": "0", "moved_down": "0"},
        ),
        case(  # B-08: four scores, only the last three are used, change 2
            "case_b08_window",
            "Mathematics, 4 term scores, oldest first: 40, 62, 66, 64 | Only the last 3 are used: "
            "62, 66, 64. Steady (spread 4). Change 2, so not moved.",
            (40, 62, 66, 64), {"scores_used": "3", "change": "2", "moved_up": "0"},
        ),
        case(  # B-07: one score is not enough evidence. No number of it is anchored in a quote
            "case_b07_one_score", None, (64,), {"enough_evidence": "0", "scores_used": "1"},
            source="build",
        ),
        case(  # the model's own case: a fall of twelve
            "own_fall_of_twelve", None, (80, 72, 68), {"change": "-12", "moved_down": "1"},
            source="build",
        ),
    ]


def moved_proposal(source=None, tests=None, reads=None, **over):
    return {
        "measure_name": "moved_at_or_above",
        "description": "Whether the change between the first and last score in the window is at "
                       "or above the moved threshold, up or down.",
        "source": MOVED_SOURCE if source is None else source,
        "reads": ["MP-1", "TR-1"] if reads is None else reads,
        "output_kind": "number",
        "tests": moved_tests() if tests is None else tests,
        **over,
    }  # fmt: skip


def settled_for_moved(content=None, section_id="6.4"):
    """The settlement the build makes for the three Baobab rules, from the real code."""
    got = engine.settle_rules(
        inputs(content), RULES, section_id, [rule_moved(), rule_window(), rule_evidence()],
        Counter(),
    )
    assert got["status"] == "ok", got
    return got
