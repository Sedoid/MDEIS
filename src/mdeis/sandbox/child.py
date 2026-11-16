"""The child of the measure runner: reads one request, runs one function, writes one answer.

The supervisor starts a fresh child for every call and kills it afterwards, so nothing one call does
can reach the next. The child has three guards, each stopping the one before it from being the only
one:

  1. It checks the hash of the source and runs the static check again, with the rules that came in
     the request. A source that fails is never run.
  2. It runs the function in a namespace whose builtins are only the listed ones, and whose imports
     are a guard that allows only the listed modules and members. Even a source that got past the
     static check cannot reach `open`, `__import__("os")`, `getattr` or the real `math`.
  3. It sets its own limits before it runs anything: processor time, address space, no core file, no
     file written, and a wall-clock alarm. The container adds the rest (no network, a read-only file
     system), which is the real boundary.

Standard library, the rules object and the static check only: the runner image carries no other
package, so there is nothing else here to import.
"""

from __future__ import annotations

import builtins
import hashlib
import os
import resource
import signal
import sys
import types

from .protocol import (
    BYTES_PER_KB,
    BYTES_PER_MB,
    BadOutput,
    ProtocolError,
    check_request,
    encode,
    error,
    ok,
    parse,
    to_wire,
)
from .rules import Rules, SandboxRulesError
from .static_check import check_source

NO_MEMORY = "the function used more memory than the limit"


# --- one request -------------------------------------------------------------------------------
def answer(line: bytes, prepare=None) -> dict:
    """The answer to one request line. Never raises. `prepare(limits)` is called once the request is
    known to be well formed and before anything of the caller's is run (the child uses it to set
    its limits; a test can leave it out)."""
    try:
        request = check_request(parse(line))
    except ProtocolError as refusal:
        return error("bad_request", str(refusal))
    if prepare is not None:
        try:
            prepare(request["limits"])
        except (OSError, ValueError):
            return error("crash", "the limits could not be applied, so nothing was run")
    try:
        return handle(request)
    except MemoryError:
        return error("memory", NO_MEMORY)
    except Exception as failure:  # a fault in the child itself, not in the function
        return error("crash", f"the child failed unexpectedly: {type(failure).__name__}")


def handle(request: dict) -> dict:
    """Check, run and convert. No limits are applied here (see `apply_limits`)."""
    source = request["source"]
    try:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return error("bad_request", "the source is not valid text")
    if digest != request["code_sha256"]:
        return error("bad_request", "the source does not match its hash")
    try:
        rules = Rules.from_dict(request["rules"])
    except SandboxRulesError as refusal:
        return error("bad_request", str(refusal))
    report = check_source(source, output_kind=request["output_kind"], rules=rules)
    if not report.passed:
        codes = ", ".join(sorted({finding.code for finding in report.findings}))
        return error("static", f"the static check refused the source: {codes}")
    try:
        result = _run(source, rules, request["records"], request["params"])
        output = to_wire(result)
    except MemoryError:
        return error("memory", NO_MEMORY)
    except BadOutput as refusal:
        return error("bad_output", str(refusal))
    except SandboxRulesError as refusal:
        return error("bad_request", str(refusal))
    except Exception as failure:  # the function's own error: only its type is reported
        return error("runtime", type(failure).__name__)
    if not isinstance(output, dict):
        return error("bad_output", "the function must return an object")
    reply = ok(request["output_kind"], output)
    if len(encode(reply)) > request["limits"]["output_kb"] * BYTES_PER_KB:
        return error("bad_output", "the output is too large")
    return reply


def _run(source: str, rules: Rules, records: list, params: dict):
    space = restricted_namespace(rules)
    code = compile(source, "<measure>", "exec", dont_inherit=True)
    exec(code, space)  # the namespace holds only the guarded builtins; see restricted_namespace
    entry = space.get(rules.entry_function)
    if not callable(entry):
        raise TypeError("the entry function is not defined")
    return entry(records, params)


# --- what the function can reach ---------------------------------------------------------------
def restricted_namespace(rules: Rules) -> dict:
    """The globals the function runs in. Its builtins are the listed ones and a guarded import."""
    allowed = {}
    for name in sorted(rules.builtins):
        if not hasattr(builtins, name):
            raise SandboxRulesError(f"the rules list a builtin that does not exist: {name}")
        allowed[name] = getattr(builtins, name)
    real_import = builtins.__import__

    def guarded_import(name, scope=None, local_scope=None, fromlist=(), level=0):
        if level or name not in rules.modules:
            raise ImportError(f"the module {name} cannot be imported")
        members = rules.modules[name]
        for member in fromlist or ():
            if member not in members:
                raise ImportError(f"{name}.{member} cannot be imported")
        module = real_import(name, None, None, (), 0)
        # Not the real module: only the listed members, so `math.sqrt` is not there to be found.
        shown = {member: getattr(module, member) for member in members if hasattr(module, member)}
        return types.SimpleNamespace(**shown)

    allowed["__import__"] = guarded_import
    return {"__builtins__": allowed}


# --- the child's own limits --------------------------------------------------------------------
def apply_limits(limits: dict) -> None:
    """Set the limits of this process. They cannot be raised again by the code that runs in it."""
    memory = limits["memory_mb"] * BYTES_PER_MB
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # no core file
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # no file can be written
    # At the soft limit the kernel sends SIGXCPU, which ends the process; the supervisor reads that
    # as a time limit. No hard limit is set, so the signal is not turned into SIGKILL.
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_s"], resource.RLIM_INFINITY))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    signal.alarm(limits["wall_s"])  # SIGALRM ends the process; also read as a time limit


def main() -> None:
    """Read one line from standard input, write one line to standard output."""
    reply_to = sys.stdout.buffer
    sys.stdout = sys.stderr  # anything printed by mistake cannot become part of the answer
    line = sys.stdin.buffer.readline()
    reply = answer(line, prepare=apply_limits)
    try:
        data = encode(reply)
    except ProtocolError:
        data = encode(error("bad_output", "the answer could not be written"))
    signal.alarm(0)
    reply_to.write(data)
    reply_to.flush()
    os._exit(0)  # no finalisation: the answer is written, and nothing else is wanted from here
