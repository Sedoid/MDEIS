"""The supervisor of the measure runner: a small server on a Unix socket that starts a fresh child
process for every call.

    python -m mdeis.sandbox.supervisor

Settings come from the environment of the container (see `protocol.py` for the names): the socket
path, the ceiling for each limit, and how many children may run at once. If one is missing the
runner does not start. The limits a request asks for are cut to these ceilings, so the smaller
number always applies and a bad request or a bad rules file cannot raise a limit.

For each call the supervisor:
  * reads one line, no more than the input ceiling, and answers a malformed one as `bad_request`;
  * starts a new interpreter (`python -S -P`, a clean environment with a fixed hash seed so a set of
    text always comes out in the same order, no inherited file, its own session and process group);
  * feeds it the request and reads at most one answer line, no more than the output ceiling;
  * kills the whole process group at the wall-clock limit, and again when the call is over;
  * returns the child's own answer if it is well formed, or a typed error: `timeout`, `memory`
    (said by the child), `crash` or `bad_output`.
A child that crashes, hangs, closes its output or writes rubbish never takes the supervisor down,
and no state is shared between calls because no process is reused.

Standard library only. The network is shut by the container (`network_mode: none`), not by this
code; this code only listens on a socket file in a folder shared with `mdeis-mcp` and no one else.
"""

from __future__ import annotations

import os
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .protocol import (
    BYTES_PER_KB,
    CEILING_PREFIX,
    ERROR_KINDS,
    PARALLEL_ENV,
    SOCKET_ENV,
    ProtocolError,
    check_request,
    encode,
    error,
    parse,
)
from .rules import LIMIT_KEYS

# Who may use the socket: its owner and its group, so `mdeis-mcp` can be given the group. A mode,
# not a school rule.
SOCKET_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP

# The signals that mean "ran out of time": the processor limit and the child's own alarm.
TIME_SIGNALS = (signal.SIGXCPU, signal.SIGALRM)

# What the child's interpreter runs: put the folder that holds the `mdeis` package on the path (the
# interpreter is started without site and without the script folder, so nothing else is found), then
# call the child.
BOOT = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from mdeis.sandbox import child; child.main()"
)


class RunnerConfigError(Exception):
    """A setting of the runner is missing or wrong. The message names the variable."""


@dataclass(frozen=True)
class Ceilings:
    """The container's own limits. A request is cut to these."""

    wall_s: int
    cpu_s: int
    memory_mb: int
    input_kb: int
    output_kb: int
    parallel: int

    def limits(self) -> dict:
        return {key: getattr(self, key) for key in LIMIT_KEYS}

    @classmethod
    def from_environment(cls, environ) -> Ceilings:
        values = {key: _whole(environ, CEILING_PREFIX + key.upper()) for key in LIMIT_KEYS}
        return cls(parallel=_whole(environ, PARALLEL_ENV), **values)


def _whole(environ, name: str) -> int:
    text = environ.get(name)
    if text is None:
        raise RunnerConfigError(f"{name} is not set")
    try:
        number = int(text)
    except ValueError:
        raise RunnerConfigError(f"{name} must be a whole number above zero") from None
    if number <= 0:
        raise RunnerConfigError(f"{name} must be a whole number above zero")
    return number


def clamp(asked: dict, ceilings: Ceilings) -> dict:
    """Each requested limit, cut to the ceiling when it is above it."""
    ceiling = ceilings.limits()
    return {key: min(asked[key], ceiling[key]) for key in LIMIT_KEYS}


# --- one child ---------------------------------------------------------------------------------
def child_command() -> list[str]:
    root = Path(__file__).resolve().parent.parent.parent  # the folder that holds `mdeis`
    return [sys.executable, "-S", "-P", "-c", BOOT, str(root)]


def child_environment() -> dict[str, str]:
    """All the child gets. Nothing is inherited, so no key, password or address reaches it."""
    return {
        "PYTHONHASHSEED": "0",  # a set of text comes out in the same order in every process
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONCOERCECLOCALE": "0",
    }


def _kill(process: subprocess.Popen) -> None:
    """Kill the whole group if the child is not yet collected (so its id cannot be reused)."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _feed(process: subprocess.Popen, line: bytes) -> None:
    try:
        process.stdin.write(line)
        process.stdin.close()
    except (OSError, ValueError):
        pass  # the child stopped before it read: what it did is judged from what comes back


def _well_formed(reply) -> bool:
    if not isinstance(reply, dict):
        return False
    if reply.get("status") == "ok":
        return (
            set(reply) == {"status", "output_kind", "output"}
            and isinstance(reply["output_kind"], str)
            and isinstance(reply["output"], dict)
        )
    if reply.get("status") == "error":
        return (
            set(reply) == {"status", "kind", "detail"}
            and reply["kind"] in ERROR_KINDS
            and isinstance(reply["detail"], str)
            and bool(reply["detail"])
        )
    return False


def _judge(data: bytes, code, expired: bool, cap: int) -> dict:
    """What the child's bytes and way of stopping amount to."""
    if len(data) > cap:
        return error("bad_output", "the child's answer is too large")
    if data.endswith(b"\n"):
        try:
            reply = parse(data)
        except ProtocolError:
            return error("bad_output", "the child's answer is not valid JSON")
        if _well_formed(reply):
            return reply
        return error("bad_output", "the child's answer has the wrong shape")
    if expired:
        return error("timeout", "the call used more than the wall-clock limit")
    if code is not None and code < 0 and -code in TIME_SIGNALS:
        return error("timeout", "the call used more than the time limit")
    return error("crash", "the child stopped without an answer")


def execute(line: bytes, limits: dict, *, command: list[str] | None = None) -> dict:
    """Run one call in a fresh child and return its answer, or a typed error. Never raises.

    `limits` are the effective ones (already cut to the ceilings). `command` is for tests that need
    a child that misbehaves; the supervisor itself always uses `child_command()`.
    """
    cap = limits["output_kb"] * BYTES_PER_KB
    try:
        process = subprocess.Popen(
            command or child_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            cwd=os.sep,
            close_fds=True,
            start_new_session=True,  # its own session and process group, so the group can be killed
        )
    except OSError:
        return error("crash", "the child could not be started")
    expired = threading.Event()

    def out_of_time() -> None:
        expired.set()
        _kill(process)

    timer = threading.Timer(limits["wall_s"], out_of_time)
    timer.daemon = True
    data = b""
    try:
        timer.start()
        _feed(process, line)
        data = process.stdout.readline(cap + 1)  # at most one line, a little over the cap at most
        if not data.endswith(b"\n") and len(data) <= cap:
            process.wait()  # the child has closed its output: the timer still bounds the wait
    finally:
        timer.cancel()
        _kill(process)
        process.wait()
        process.stdout.close()
    return _judge(data, process.returncode, expired.is_set(), cap)


# --- the server --------------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        self.timeout = self.server.ceilings.wall_s  # how long a client may take to send its line
        super().setup()

    def handle(self) -> None:
        cap = self.server.ceilings.input_kb * BYTES_PER_KB
        try:
            line = self.rfile.readline(cap + 1)
            if not line:
                return  # the client sent nothing and left
            too_big = len(line) > cap
            reply = self.server.answer(line, too_big)
        except TimeoutError:
            too_big, reply = False, error("bad_request", "no complete request arrived in time")
        except Exception:  # whatever went wrong, the supervisor goes on with the next call
            too_big, reply = False, error("crash", "the runner could not complete the call")
        try:
            self.wfile.write(encode(reply))
            self.wfile.flush()
            if too_big:
                self._drain()
        except OSError:
            pass  # the client has gone

    def _drain(self) -> None:
        """Read and drop what is left of an over-long request, so that closing the connection does
        not reset it before the client has read the refusal. Bounded by time."""
        self.connection.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + self.server.ceilings.wall_s
        while time.monotonic() < deadline and self.connection.recv(BYTES_PER_KB):
            pass


class Supervisor(socketserver.ThreadingUnixStreamServer):
    """Listens on a Unix socket. Each connection is one call, handled in its own thread, with at
    most `ceilings.parallel` children alive at once."""

    daemon_threads = True

    def __init__(self, path, ceilings: Ceilings, command: list[str] | None = None):
        self.path = str(path)
        self.ceilings = ceilings
        self.command = command  # None: the real child. A test can set another.
        self.slots = threading.BoundedSemaphore(ceilings.parallel)
        _clear_old_socket(self.path)
        super().__init__(self.path, _Handler)
        os.chmod(self.path, SOCKET_MODE)  # only the folder's two users can reach it either way

    def answer(self, line: bytes, too_big: bool) -> dict:
        if too_big:
            return error("bad_request", "the request is too large")
        if not line.endswith(b"\n"):
            return error("bad_request", "the request is not a complete line")
        try:
            request = check_request(parse(line))
        except ProtocolError as refusal:
            return error("bad_request", str(refusal))
        cut = clamp(request["limits"], self.ceilings)
        request["limits"] = cut
        with self.slots:
            return execute(encode(request), cut, command=self.command)

    def server_close(self) -> None:
        super().server_close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def _clear_old_socket(path: str) -> None:
    """A socket file left by a runner that did not stop cleanly would stop the new one binding."""
    try:
        if stat.S_ISSOCK(os.stat(path).st_mode):
            os.unlink(path)
    except FileNotFoundError:
        pass


# --- the entry command -------------------------------------------------------------------------
def _stop_now(signum, frame) -> None:
    raise SystemExit(0)


def main(environ=None) -> int:
    environ = os.environ if environ is None else environ
    try:
        path = environ.get(SOCKET_ENV)
        if not path:
            raise RunnerConfigError(f"{SOCKET_ENV} is not set")
        ceilings = Ceilings.from_environment(environ)
    except RunnerConfigError as refusal:
        print(f"measure-runner: {refusal}", file=sys.stderr)
        return 1
    server = Supervisor(path, ceilings)
    signal.signal(signal.SIGTERM, _stop_now)
    print(f"measure-runner: listening on {path}", flush=True)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
