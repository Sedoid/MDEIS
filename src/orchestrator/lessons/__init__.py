"""The lesson graph: the only place a model touches a lesson.

One model call (and at most `MAX_REPAIR_ROUNDS` repeats) turns a tutor's notes and the class
evidence into an edit list. The model has no tools. Everything before it (the class read, the notes
guard, the evidence check) and everything after it (the checks, the diff, the evidence sentences,
the stored plan) is code that runs inside the MCP server and returns finished results.

This package holds the names the graph may use. It imports nothing from the lesson core: the
orchestrator never holds the learner roster and never touches the plan table.
"""

# How many times a failed edit list may be sent back to the model. A bound on a loop, not a school
# rule. It must equal the core's bound; a test compares the two.
MAX_REPAIR_ROUNDS = 2

# The three tools the graph may call, by name. Nothing else: the graph cannot reach any other tool,
# and it never gives the model a tool list. Reading a saved plan is a separate route, not a node.
CLASS_TOOL = "class_baseline"
GUARD_TOOL = "guard_lesson_notes"
SAVE_TOOL = "save_lesson_plan"
READ_TOOL = "get_lesson_plan"
GRAPH_TOOLS = frozenset({CLASS_TOOL, GUARD_TOOL, SAVE_TOOL})

# The class tool's bin for "below the expected range", as the core names it (a test compares them).
GAP_BIN = "below_expected_range"
# What the save tool is told when no model was called (the plain no-evidence statement).
NO_MODEL = "none: no model was called"
EMPTY_EDITS = '{"edits": []}'
SUGGESTION_LABEL = "Suggestion (the tutor decides)"
