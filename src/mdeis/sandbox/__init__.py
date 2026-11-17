"""The sandbox: what a generated function may contain, and where it is run (T-052).

Slice 1 is the static check: `rules.py` and `static_check.py`. Slice 2 is the runner:

  * `protocol.py`    the wire format (one JSON line each way, exact numbers as text);
  * `child.py`       one request in, one function run under limits, one answer out;
  * `supervisor.py`  a server on a Unix socket that starts a fresh child for every call
                     (`python -m mdeis.sandbox.supervisor`, the entry command of `measure-runner`);
  * `client.py`      what `mdeis-mcp` will use to ask the runner (not a model tool).

All of them except `config.py` and `client.py` use the standard library and nothing else of ours, so
the runner image can carry this package alone. `config.py` reads the YAML and is used only by
`mdeis-mcp`.

T-070 adds the school's values to a call. A function reads them only through `params`, keyed by
clause ID, and the static check holds it to the keys its version declares (`static_check.py`). The
build side has `with_template.py` (the call path: values built from the active template by
`mdeis.core.measure_params`, then `client.run`) and `sensitivity.py` (each value must change at
least one test answer). The runner uses neither, and `client.py` is unchanged.

A generated function can only compute. It never reaches a fixed tool's powers: no database, no
network, no file, no clock, no tool of ours. The static check is the first line. The container is
the boundary.
"""
