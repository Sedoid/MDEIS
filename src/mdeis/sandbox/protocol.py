"""The wire format of the measure runner. Standard library only.

One JSON object per line goes in and one comes back. Exact numbers travel as text, never as JSON
numbers: a score is "31/2" (a fraction in lowest terms) or "0.60" (a decimal), a whole number is
"5". A JSON float, NaN or Infinity is refused on the way in, a repeated key is refused, and nothing
that is not exact is ever written on the way out.

    request   {"protocol": 1, "source": "...", "code_sha256": "...", "output_kind": "number",
               "records": [...], "params": {"<clause id>": "<exact text>"},
               "rules": {...the sandbox rules...}, "limits": {"wall_s": ..., ...}}
    answer    {"status": "ok", "output_kind": "number", "output": {...}}
              {"status": "error", "kind": "<one of KINDS>", "detail": "<short, no record data>"}

Used by the child, the supervisor and the client, so the three agree on one definition. This file
holds no rule of the school and no limit: the limits come from `templates/sandbox-v1.yaml`, and the
only numbers here are the size of a kilobyte, the version of this format and the 0 and 1 of the
code's own structure.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from fractions import Fraction

from .rules import LIMIT_KEYS

PROTOCOL_VERSION = 1  # the version of this format, not a school value
BYTES_PER_KB = 1024  # a unit
BYTES_PER_MB = BYTES_PER_KB * BYTES_PER_KB  # a unit

# The settings of the container, by environment variable name.
SOCKET_ENV = "MEASURE_RUNNER_SOCKET"
CEILING_PREFIX = "MEASURE_RUNNER_MAX_"  # + WALL_S, CPU_S, MEMORY_MB, INPUT_KB, OUTPUT_KB, PARALLEL
PARALLEL_ENV = CEILING_PREFIX + "PARALLEL"

# What an error can be. The first group is what the supervisor and the child can say. The last is
# what only the client says, when it cannot reach the runner at all.
ERROR_KINDS = (
    "static",  # the static check refused the source
    "timeout",  # the wall-clock or processor limit was reached
    "memory",  # the memory limit was reached
    "crash",  # the child stopped without an answer
    "bad_output",  # the answer was not exact, not an object, or too large
    "runtime",  # the function raised an error
    "bad_request",  # the request was not valid, or the hash did not match
)
UNAVAILABLE = "runner_unavailable"
KINDS = ERROR_KINDS + (UNAVAILABLE,)

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """A line that is not a valid message of this format. The message is safe to show."""


class BadOutput(ProtocolError):
    """A value the function returned that cannot be sent: not exact, or not of a known shape."""


# --- reading a line ----------------------------------------------------------------------------
def _refuse_float(text: str):
    raise ProtocolError("a number with a decimal point or an exponent is not allowed: send exact "
                        "numbers as text")


def _refuse_constant(text: str):
    raise ProtocolError("NaN and Infinity are not allowed: send exact numbers as text")


def _no_repeats(pairs):
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise ProtocolError("a key is repeated in the same object")
        found[key] = value
    return found


def parse(line: bytes | str):
    """One line of JSON, read strictly. Raises ProtocolError for anything else."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("the line is not valid UTF-8") from None
    try:
        return json.loads(
            line,
            parse_float=_refuse_float,
            parse_constant=_refuse_constant,
            object_pairs_hook=_no_repeats,
        )
    except ProtocolError:
        raise
    except (ValueError, RecursionError):
        raise ProtocolError("the line is not valid JSON") from None


# --- writing a line ----------------------------------------------------------------------------
def _check_exact(value) -> None:
    """Refuse a float, and a key that is not text, anywhere inside the value."""
    if isinstance(value, float):
        raise ProtocolError("a float cannot be sent: send exact numbers as text")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("an object key that is not text cannot be sent")
            _check_exact(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_exact(item)


def encode(message) -> bytes:
    """One compact line, keys in order, ending in a newline. The same message, the same bytes."""
    try:
        _check_exact(message)
        text = json.dumps(message, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except RecursionError:
        raise ProtocolError("the message is nested too deeply") from None
    except ProtocolError:
        raise
    except (TypeError, ValueError):
        raise ProtocolError("the message cannot be written as JSON") from None
    return text.encode("ascii") + b"\n"


def ok(output_kind: str, output: dict) -> dict:
    return {"status": "ok", "output_kind": output_kind, "output": output}


def error(kind: str, detail: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of error")
    return {"status": "error", "kind": kind, "detail": detail}


# --- the way out: exact values only ------------------------------------------------------------
def to_wire(value):
    """A function's result as JSON-ready exact values. Whole numbers and fractions become text, a
    decimal keeps its places, and a list, tuple or object is walked. A float, a set, bytes or a
    non-finite decimal raises BadOutput. The message names the type and never the value."""
    try:
        return _wire(value)
    except RecursionError:
        raise BadOutput("the output is nested too deeply") from None


def _wire(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        try:
            return str(value)
        except ValueError:
            raise BadOutput("a whole number is too long to write") from None
    if isinstance(value, Fraction):
        return exact_text(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BadOutput("a decimal that is not a finite number cannot be returned")
        return format(value, "f")
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        wired = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BadOutput("an object key that is not text cannot be returned")
            wired[key] = _wire(item)
        return wired
    if isinstance(value, float):
        raise BadOutput("a float is not an exact number and cannot be returned")
    raise BadOutput(f"a value of type {type(value).__name__} cannot be returned")


def exact_text(value: Fraction) -> str:
    """A fraction in lowest terms as "n/d", or as "n" when it is a whole number."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


# --- the request -------------------------------------------------------------------------------
def request(*, source, code_sha256, output_kind, records, params, rules, limits) -> dict:
    return {
        "protocol": PROTOCOL_VERSION,
        "source": source,
        "code_sha256": code_sha256,
        "output_kind": output_kind,
        "records": records,
        "params": params,
        "rules": rules,
        "limits": limits,
    }


REQUEST_PARTS = (
    "protocol", "source", "code_sha256", "output_kind", "records", "params", "rules", "limits"
)


def _whole_above_zero(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def check_request(message) -> dict:
    """The request as it is, if every part is there and the right shape. Else ProtocolError."""
    if not isinstance(message, dict):
        raise ProtocolError("the request is not an object")
    for part in REQUEST_PARTS:
        if part not in message:
            raise ProtocolError(f"`{part}` is missing from the request")
    version = message["protocol"]
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ProtocolError("`protocol` is not a version this runner speaks")
    for part in ("source", "output_kind"):
        if not isinstance(message[part], str) or not message[part].strip():
            raise ProtocolError(f"`{part}` must be text")
    if not isinstance(message["code_sha256"], str) or not SHA256.match(message["code_sha256"]):
        raise ProtocolError("`code_sha256` must be a sha256 in lower-case hex")
    records = message["records"]
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ProtocolError("`records` must be a list of objects")
    params = message["params"]
    if not isinstance(params, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in params.items()
    ):
        raise ProtocolError("`params` must be an object of text values: send exact numbers as text")
    if not isinstance(message["rules"], dict):
        raise ProtocolError("`rules` must be an object")
    limits = message["limits"]
    if not isinstance(limits, dict):
        raise ProtocolError("`limits` must be an object")
    for key in LIMIT_KEYS:
        if not _whole_above_zero(limits.get(key)):
            raise ProtocolError(f"`limits.{key}` must be a whole number above zero")
    return message
