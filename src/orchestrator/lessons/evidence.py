"""The class evidence the model is shown, and the cheap check that decides whether to ask it at all.

The class tool returns one result for a stage and term, with a finding per learning area. The model
sees only the finding of the area the tutor chose: its id, stage, term, area, group size and the
five counts (a hidden count is the word `hidden`, never a number). It sees no name and no learner
number because the class tool returns none.

`has_visible_gap` is a cheap guard, not the rule. It says no only when the gap count is hidden or is
zero. The real test of a gap (the rulebook's minimum cell size) is applied by the checks in the MCP
server, so no threshold is held here.
"""

from . import GAP_BIN

HIDDEN = "hidden"


def finding_view(result, area: str) -> dict | None:
    """The finding of one area of a class result, as the model sees it, or None when the result is
    not ok (suppressed, refused) or the area has no usable counts."""
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    for item in result.get("areas") or []:
        if not isinstance(item, dict) or item.get("area") != area:
            continue
        bins = item.get("bins")
        if item.get("status") != "ok" or not isinstance(bins, list):
            return None
        return {
            "finding_id": item.get("finding_id"),
            "stage": result.get("stage"),
            "term": result.get("term"),
            "area": area,
            "group_size": result.get("group_size"),
            "bins": [
                {"bin": b.get("bin"), "count": b.get("count") if b.get("shown") else HIDDEN}
                for b in bins
                if isinstance(b, dict)
            ],
        }
    return None


def has_visible_gap(view: dict | None) -> bool:
    """True when the below-the-range count is shown and is more than zero."""
    if view is None:
        return False
    for b in view["bins"]:
        if b["bin"] == GAP_BIN:
            count = b["count"]
            return isinstance(count, int) and not isinstance(count, bool) and count > 0
    return False
