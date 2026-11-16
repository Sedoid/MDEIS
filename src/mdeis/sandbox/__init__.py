"""The sandbox: what a generated function may contain, and (slice 2) where it is run.

Slice 1 holds the static check only. `rules.py` and `static_check.py` use the standard library and
nothing else of ours, so the runner image can carry them alone. `config.py` reads the YAML and is
used only by `mdeis-mcp`.

A generated function can only compute. It never reaches a fixed tool's powers: no database, no
network, no file, no clock, no tool of ours. The static check is the first line. The container
(slice 2) is the boundary.
"""
