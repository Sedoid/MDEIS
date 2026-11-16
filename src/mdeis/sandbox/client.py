"""The client of the measure runner, for `mdeis-mcp` to use later. Nothing here is a model tool.

    result = client.run(source, code_sha256, records, params, output_kind="number")

returns `{"output_kind": ..., "output": {...}}` with every number as exact text, or raises
`RunnerRefusal` with a kind from `protocol.KINDS`. There is no answer to read from a failed run,
and the client never asks a second time and never works a number out itself: a missing, closed,
silent or refusing runner is `runner_unavailable`, `timeout`, `memory`, `crash`, `bad_output`,
`bad_request`, `static` or `runtime`, and the caller says so.

The rules and limits come from `templates/sandbox-v1.yaml` (read here, in `mdeis-mcp`, which has the
file mounted). They travel inside the request, and the runner cuts every limit to its own ceiling.
The socket path is `MEASURE_RUNNER_SOCKET` unless it is given.
"""

from __future__ import annotations

import os
import socket
import time

from .config import load_rules
from .protocol import (
    BYTES_PER_KB,
    KINDS,
    SOCKET_ENV,
    UNAVAILABLE,
    ProtocolError,
    encode,
    parse,
    request,
)
from .rules import LIMIT_KEYS, Rules


class RunnerRefusal(Exception):
    """The runner did not give a result. `kind` is one of `protocol.KINDS`; `detail` is short and
    holds no record data."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def run(
    source: str,
    code_sha256: str,
    records: list,
    params: dict,
    *,
    output_kind: str,
    rules: Rules | None = None,
    socket_path: str | None = None,
    limits: dict | None = None,
) -> dict:
    """Run one function in the runner. `output_kind` is the kind the registry holds for the version:
    the runner checks that the source declares the same one."""
    rules = load_rules() if rules is None else rules
    path = socket_path or os.environ.get(SOCKET_ENV)
    if not path:
        raise RunnerRefusal(UNAVAILABLE, f"{SOCKET_ENV} is not set, so the runner is not known")
    asked = dict(rules.limits if limits is None else limits)
    for key in LIMIT_KEYS:
        if not isinstance(asked.get(key), int) or isinstance(asked.get(key), bool):
            raise RunnerRefusal("bad_request", f"the limit {key} must be a whole number")
    try:
        line = encode(
            request(
                source=source,
                code_sha256=code_sha256,
                output_kind=output_kind,
                records=records,
                params=params,
                rules=rules.to_dict(),
                limits=asked,
            )
        )
    except ProtocolError as refusal:
        raise RunnerRefusal("bad_request", str(refusal)) from None
    if len(line) > rules.limits["input_kb"] * BYTES_PER_KB:
        raise RunnerRefusal("bad_request", "the input is too large")
    cap = rules.limits["output_kb"] * BYTES_PER_KB
    wait = asked["wall_s"] + rules.runner["reply_slack_s"]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(rules.runner["connect_s"])
            connection.connect(path)
            connection.sendall(line)
            reply = _read_line(connection, cap, wait)
    except OSError as failure:  # missing, refused, closed, silent: none of them is an answer
        detail = f"the runner could not be reached ({type(failure).__name__})"
        raise RunnerRefusal(UNAVAILABLE, detail) from None
    return _result(reply, cap)


def _read_line(connection: socket.socket, cap: int, wait: float) -> bytes:
    """One line, or an OSError if the runner closes the connection or is too slow."""
    deadline = time.monotonic() + wait
    data = b""
    while b"\n" not in data and len(data) <= cap:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("no answer in time")
        connection.settimeout(left)
        chunk = connection.recv(BYTES_PER_KB)
        if not chunk:
            raise ConnectionError("the runner closed the connection without an answer")
        data += chunk
    return data


def _result(reply: bytes, cap: int) -> dict:
    if len(reply) > cap:
        raise RunnerRefusal("bad_output", "the runner's answer is too large")
    try:
        answer = parse(reply)
    except ProtocolError:
        raise RunnerRefusal("bad_output", "the runner's answer is not valid") from None
    if isinstance(answer, dict) and answer.get("status") == "ok":
        output_kind, output = answer.get("output_kind"), answer.get("output")
        if isinstance(output_kind, str) and isinstance(output, dict):
            return {"output_kind": output_kind, "output": output}
    elif isinstance(answer, dict) and answer.get("status") == "error":
        kind, detail = answer.get("kind"), answer.get("detail")
        if kind in KINDS and isinstance(detail, str):
            raise RunnerRefusal(kind, detail)
    raise RunnerRefusal("bad_output", "the runner's answer has the wrong shape")
