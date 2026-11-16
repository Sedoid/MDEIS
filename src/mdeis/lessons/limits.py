"""Loop bounds for the lesson graph. These are bounds on a loop, not school rules.

The size limits (notes length, number of edits, edit length) are rules and live in the template's
`lessons` section. The number of times the graph may send a failed edit list back to the model is a
bound on a loop, so it is a named constant here, as the code rules allow.
"""

# How many repair rounds the lesson graph may run after the first model answer. Two: one to fix a
# shape or wording problem, one more if the fix itself failed. After that: "no valid edits".
MAX_REPAIR_ROUNDS = 2
