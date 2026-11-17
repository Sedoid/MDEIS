"""The measure build (T-071): how an acknowledged methodology becomes proposed measure functions.

A methodology that passed the clarity gate and was acknowledged is read section by section. For each
measure section the build settles the values it states (one home per setting), proposes a pure
function and tests, runs the static check, the tests in the sandbox and the sensitivity check, and
stores the version for an automated review that can only hold. A teacher confirms it at the demo
(the registry holds that step). A confirmed measure's new values become part of the school's
template through a patch and the admin step `apply-template-patch`.

    config      the build rules (`templates/measure-build-v1.yaml`)
    quotes      the quote checks: is a quote literally in the text, is a number in it
    engine      plan, settle_rules and check_function: pure, deterministic, no model
    patch       a template patch as data, and the merge into a new template version
    store       the database reads and writes
    api         the six build tools the server runs, each one action-log row
    apply       the admin step that loads confirmed patches as a new template version

Nothing here calls a model: the graph that does is in `src/orchestrator/build/`, and it reaches
these tools only through the MCP server. Nothing here names an approver or approves anything.
"""
