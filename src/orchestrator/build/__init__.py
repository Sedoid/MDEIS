"""The build graph: the only place a model touches a measure build (T-071).

For a methodology that passed the clarity gate and was acknowledged, the graph takes each measure
section the plan names and makes at most one proposed function from it. A model does three jobs, and
only three: it extracts the values a section states (JSON), it proposes a function and its tests
(JSON), and it reads the finished, tested function and can only HOLD it. It has no tools. Everything
else is code that runs inside the MCP server and returns finished results: which sections are built,
whether each quote is in the text, where each value lives, the static check, the tests in the
sandbox, the sensitivity check, and the writes to the registry.

This package holds the names the graph may use. It imports nothing from the registry, the sandbox or
the database: the orchestrator never reads a record and never writes the registry. The build is by
the system. It names no approver, takes none, and the model is never shown one.
"""

# How the build tools are named on the server, as the graph calls them. A test compares these with
# the server side's own table. They are internal tools: they are never in the chat allow-list.
PLAN_TOOL = "build_plan"
SETTLE_TOOL = "build_settle_rules"
CHECK_TOOL = "build_check_function"
STORE_TOOL = "build_store_version"
REVIEW_TOOL = "build_record_review"
RETIRE_TOOL = "build_retire_removed"
GRAPH_TOOLS = frozenset({PLAN_TOOL, SETTLE_TOOL, CHECK_TOOL, STORE_TOOL, REVIEW_TOOL, RETIRE_TOOL})

# The three jobs a model does, and the first word of the system prompt of each.
EXTRACT, FUNCTION, REVIEW = "extract", "function", "review"
TASKS = (EXTRACT, FUNCTION, REVIEW)
