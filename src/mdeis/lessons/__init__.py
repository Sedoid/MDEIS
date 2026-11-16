"""The lesson core: read a tutor's notes, check a model's edit list, apply it, and keep a plan.

Everything here is deterministic. No module imports a model client or opens a network connection
(a test scans the imports). Only `store.py` touches the database, and only the MCP server runs it.

What the model can do is small: it returns an edit list (see `edits.py`). Code finds each quoted
span, checks every rule (`checks.py`), applies the edits, builds the evidence sentences from the
class findings and the template (`evidence.py`), and stores the plan. The objective is a locked
field. The signed-in tutor approves; no function here takes a person as an argument.
"""
