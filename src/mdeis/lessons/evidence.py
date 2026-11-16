"""The evidence sentence: composed by code from a class finding and the template. Never by a model.

For each shown bin with a count above zero, in the order of the template's levels, one line:

  Evidence ({area}, {stage}, term {term}): {count} of {group_size} learners. {statement}
  [{level clause}, {range clause}, {group clause}, template {version}, {finding id}]

The numbers are the finding's. The statement is the template's own level statement. The clause ids
come from the template and the finding. The few fixed words in the line (Evidence, of, learners, and
the two plain statements below) are interface words: they carry no rule and no number.

When there is no usable evidence no count is stated at all. A suppressed group shows neither its
counts nor its size.
"""

from .findings import Finding
from .rules import LessonRules

NOT_ENOUGH = "There is not enough class evidence to adapt this lesson."
NO_GAP = (
    "No class gap against the expected range is shown for this selection, so no "
    "evidence-based edit is made."
)


class EvidenceError(ValueError):
    """A finding does not agree with the rulebook it is being read against."""


def _range_clause(finding: Finding, rules: LessonRules) -> str | None:
    """Check the finding against the rulebook; return the range clause for its stage and area."""
    if finding.template_version != rules.version:
        raise EvidenceError(
            f"the finding was made under rulebook version {finding.template_version}, "
            f"not {rules.version}"
        )
    if finding.stage is None or finding.area is None:
        return None
    clause = rules.range_clause.get((finding.stage, finding.area))
    if clause is None:
        raise EvidenceError(
            f"the rulebook {rules.version} has no range for {finding.stage} {finding.area}"
        )
    if clause not in finding.clause_ids:
        raise EvidenceError(f"the finding does not cite {clause}, the range rule for its stage")
    return clause


check_finding = _range_clause  # the checks reuse it: a finding must agree with the rulebook


def _cite(clauses, rules: LessonRules, finding: Finding) -> str:
    shown = ", ".join(c for c in clauses if c)
    return f"[{shown}, template {rules.version}, {finding.finding_id}]"


def not_enough_evidence_sentence(finding: Finding | None, rules: LessonRules) -> str:
    if finding is None:
        return NOT_ENOUGH
    range_clause = _range_clause(finding, rules)
    if finding.status == "suppressed":
        return f"{NOT_ENOUGH} {rules.group_text} {_cite([rules.group_clause], rules, finding)}"
    return f"{NOT_ENOUGH} {_cite([range_clause or rules.group_clause], rules, finding)}"


def no_gap_sentence(finding: Finding, rules: LessonRules) -> str:
    range_clause = _range_clause(finding, rules)
    return f"{NO_GAP} {_cite([range_clause or rules.group_clause], rules, finding)}"


def evidence_sentence(finding: Finding, rules: LessonRules) -> str:
    range_clause = _range_clause(finding, rules)
    if finding.status != "ok" or not finding.bins:
        return not_enough_evidence_sentence(finding, rules)
    lines = []
    for level in rules.levels:
        for bin_ in finding.bins:
            if bin_.level_clause:
                matches = bin_.level_clause == level.clause_id
            else:
                matches = bin_.name == level.name
            if not (matches and bin_.shown and bin_.count):
                continue
            head = (
                f"Evidence ({finding.area}, {finding.stage}, term {finding.term}): "
                f"{bin_.count} of {finding.group_size} learners. {level.statement}"
            )
            clauses = [level.clause_id, range_clause, rules.group_clause]
            lines.append(f"{head} {_cite(clauses, rules, finding)}")
    if not lines:
        return not_enough_evidence_sentence(finding, rules)
    return "\n".join(lines)
