"""The supervisor and the client of the measure runner, over a REAL Unix socket (T-052, slice 2).

The supervisor listens on a socket and starts a fresh child process for each call. The client is
what `mdeis-mcp` will use later. No Docker is needed: the socket lives in a temporary folder.
Nothing here is a model tool.

What these tests hold the code to:
  * the client gets the result, or a typed refusal, and never a number from a failed run;
  * a socket that is missing, refused, closed or silent is `runner_unavailable`, and the client
    never asks twice;
  * a child that crashes, hangs, closes its output or answers with rubbish does not take the
    supervisor down, and the next call works;
  * the child starts clean: no inherited environment, no inherited file, its own process group;
  * the container's ceilings cut a request, and the runner will not start without them;
  * the entry command the container will use starts, answers, and stops cleanly on SIGTERM.
"""

import ast
import hashlib
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from mdeis.sandbox import client, config, protocol, supervisor
from mdeis.sandbox.rules import Rules

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "sandbox-v1.yaml"
RULES = config.load_rules(FILE)
CEILINGS = supervisor.Ceilings(**RULES.limits, parallel=4)

GOOD = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    total = Fraction(0)
    for record in records:
        total += Fraction(record["score"])
    return {"value": Fraction(total, len(records))}
'''
KEEPS_STATE = '''\
OUTPUT_KIND = "number"
SEEN = []


def measure(records, params):
    SEEN.append(1)
    return {"calls_so_far": len(SEEN)}
'''
LOOPS_FOR_EVER = '''\
OUTPUT_KIND = "number"


def measure(records, params):
    for step in range(int(params["n"])):
        pass
    return {"done": "yes"}
'''
RECORDS = [{"score": "31/2"}, {"score": "15"}]


def sha(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@contextmanager
def serving(ceilings=CEILINGS, command=None):
    # A short path: a Unix socket path cannot be much longer than 100 characters.
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "runner.sock")
        server = supervisor.Supervisor(path, ceilings, command=command)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, path
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


@pytest.fixture
def runner():
    with serving() as (server, path):
        yield server, path


def call(path, source=GOOD, records=RECORDS, params=None, **over):
    args = {"output_kind": "number", "rules": RULES, "socket_path": path}
    args.update(over)
    return client.run(source, args.pop("code_sha256", sha(source)), records, params or {}, **args)


def refusal_kind(path, **kw):
    with pytest.raises(client.RunnerRefusal) as info:
        call(path, **kw)
    return info.value.kind


def raw(path, data, close_write=True):
    """Send bytes as they are and read the whole answer."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(15)
        sock.connect(path)
        sock.sendall(data)
        if close_write:
            sock.shutdown(socket.SHUT_WR)
        got = b""
        while chunk := sock.recv(65536):
            got += chunk
        return got


# -------------------------------------------------------------------------- the client's view
def test_the_client_gets_the_result_as_exact_text(runner):
    _, path = runner
    assert call(path) == {"output_kind": "number", "output": {"value": "61/4"}}


def test_the_client_signature_matches_the_brief_and_takes_no_person():
    import inspect

    names = list(inspect.signature(client.run).parameters)
    assert names[:4] == ["source", "code_sha256", "records", "params"]
    assert not {"approver", "user", "user_id", "name"} & set(names)


def test_a_refusal_from_the_child_comes_back_as_a_typed_refusal_with_the_kind(runner):
    _, path = runner
    with pytest.raises(client.RunnerRefusal) as info:
        call(path, code_sha256=sha("something else"))
    assert info.value.kind == "bad_request"
    assert isinstance(info.value.detail, str) and info.value.detail
    bad = 'import os\n\nOUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n    return {}\n'
    assert refusal_kind(path, source=bad) == "static"


def test_the_refusal_is_an_exception_and_there_is_no_result_to_read(runner):
    _, path = runner
    # A total over no records is a division by zero inside the function: a runtime error.
    assert refusal_kind(path, source=GOOD, records=[]) == "runtime"


def test_a_function_that_never_ends_is_a_timeout_through_the_socket(runner):
    _, path = runner
    started = time.monotonic()
    kind = refusal_kind(
        path, source=LOOPS_FOR_EVER, params={"n": "1000000000000"},
        limits={**RULES.limits, "wall_s": 1, "cpu_s": 30},
    )
    assert kind == "timeout"
    assert time.monotonic() - started < 10


def test_a_missing_socket_is_runner_unavailable():
    with tempfile.TemporaryDirectory() as folder:
        assert refusal_kind(os.path.join(folder, "nobody.sock")) == "runner_unavailable"


def test_a_path_that_is_a_plain_file_is_runner_unavailable():
    with tempfile.NamedTemporaryFile() as plain:
        assert refusal_kind(plain.name) == "runner_unavailable"


def test_a_socket_file_with_nobody_listening_is_runner_unavailable():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "dead.sock")
        dead = socket.socket(socket.AF_UNIX)
        dead.bind(path)
        dead.close()  # the file stays, nothing listens
        assert refusal_kind(path) == "runner_unavailable"


def test_without_a_socket_path_the_environment_names_it(monkeypatch, runner):
    _, path = runner
    monkeypatch.delenv(protocol.SOCKET_ENV, raising=False)
    with pytest.raises(client.RunnerRefusal) as info:
        client.run(GOOD, sha(GOOD), RECORDS, {}, output_kind="number", rules=RULES)
    assert info.value.kind == "runner_unavailable"
    assert protocol.SOCKET_ENV in info.value.detail
    monkeypatch.setenv(protocol.SOCKET_ENV, path)
    got = client.run(GOOD, sha(GOOD), RECORDS, {}, output_kind="number", rules=RULES)
    assert got["output"] == {"value": "61/4"}


@contextmanager
def fake_runner(behaviour):
    """A listener that accepts a connection and then closes it, or says nothing."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "fake.sock")
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(path)
        listener.listen(5)
        accepted, held = [], []

        def loop():
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                accepted.append(1)
                if behaviour == "close":
                    conn.close()
                else:
                    held.append(conn)

        threading.Thread(target=loop, daemon=True).start()
        try:
            yield path, accepted
        finally:
            listener.close()
            for conn in held:
                conn.close()


def test_a_runner_that_closes_the_connection_is_unavailable_and_is_asked_only_once():
    with fake_runner("close") as (path, accepted):
        assert refusal_kind(path) == "runner_unavailable"
        time.sleep(0.2)
        assert len(accepted) == 1


def test_a_runner_that_never_answers_is_unavailable_after_the_wall_limit_and_the_slack():
    quick = RULES.to_dict()
    quick["limits"]["wall_s"] = 1
    quick["runner"]["reply_slack_s"] = 1
    started = time.monotonic()
    with fake_runner("silent") as (path, accepted):
        assert refusal_kind(path, rules=Rules.from_dict(quick)) == "runner_unavailable"
        assert len(accepted) == 1
    assert time.monotonic() - started < 15


def test_an_input_over_the_limit_is_refused_before_anything_is_sent():
    small = RULES.to_dict()
    small["limits"]["input_kb"] = 1
    with tempfile.TemporaryDirectory() as folder:
        gone = os.path.join(folder, "never.sock")  # no runner at all: the size check comes first
        big = [{"score": "1/2", "pad": "x" * 5000}]
        assert refusal_kind(gone, records=big, rules=Rules.from_dict(small)) == "bad_request"


def test_the_client_does_not_compute_or_guess_a_number_when_the_run_fails():
    source = (ROOT / "src" / "mdeis" / "sandbox" / "client.py").read_text(encoding="utf-8")
    for word in ("Fraction", "Decimal", "float(", "retry", "while True"):
        assert word not in source, word


# ------------------------------------------------------------------- the supervisor's view
def test_two_calls_do_not_share_state(runner):
    _, path = runner
    one = call(path, source=KEEPS_STATE)
    two = call(path, source=KEEPS_STATE)
    assert one["output"] == {"calls_so_far": "1"} and two["output"] == {"calls_so_far": "1"}


def test_two_calls_at_the_same_time_both_finish(runner):
    _, path = runner
    got, errors = [], []

    def one():
        try:
            got.append(call(path)["output"])
        except Exception as error:  # the test reports it below
            errors.append(error)

    threads = [threading.Thread(target=one) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors and got == [{"value": "61/4"}] * 3


CRASHES = [sys.executable, "-c", "import os; os._exit(3)"]


def test_a_child_that_crashes_does_not_take_the_supervisor_down(runner):
    server, path = runner
    server.command = CRASHES
    assert refusal_kind(path) == "crash"
    server.command = [sys.executable, "-c",
                      "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"]
    assert refusal_kind(path) == "crash"
    server.command = None  # the real child again
    assert call(path)["output"] == {"value": "61/4"}


def test_a_connection_that_sends_rubbish_gets_a_bad_request_and_the_next_call_works(runner):
    _, path = runner
    for rubbish in (b"garbage\n", b'{"protocol": 1}\n', b"[]\n", b"\n", b'{"a": 1.5}\n'):
        got = protocol.parse(raw(path, rubbish).strip())
        assert got["status"] == "error" and got["kind"] == "bad_request", rubbish
    unfinished = protocol.parse(raw(path, b'{"incomplete": ').strip())  # a line with no end
    assert unfinished["kind"] == "bad_request"
    assert raw(path, b"") == b""  # nothing sent at all: the connection is just closed
    assert call(path)["output"] == {"value": "61/4"}


def test_a_client_that_connects_and_hangs_up_does_not_stop_the_supervisor(runner):
    _, path = runner
    for _ in range(3):
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(path)
        sock.close()
    assert call(path)["output"] == {"value": "61/4"}


def test_a_request_over_the_input_ceiling_is_refused_and_the_runner_goes_on():
    tight = supervisor.Ceilings(**{**RULES.limits, "input_kb": 1}, parallel=2)
    with serving(tight) as (_, path):
        got = protocol.parse(raw(path, b'{"x": "' + b"a" * 5000 + b'"}\n').strip())
        assert got["status"] == "error" and got["kind"] == "bad_request"
        assert "large" in got["detail"]


def test_the_ceiling_cuts_what_the_request_asks_for():
    short = supervisor.Ceilings(**{**RULES.limits, "wall_s": 1, "cpu_s": 30}, parallel=2)
    started = time.monotonic()
    with serving(short) as (_, path):
        kind = refusal_kind(
            path, source=LOOPS_FOR_EVER, params={"n": "1000000000000"},
            limits={**RULES.limits, "wall_s": 3600, "cpu_s": 3600},
        )
    assert kind == "timeout"
    assert time.monotonic() - started < 15


def test_the_socket_can_be_used_by_its_owner_and_group_and_by_no_one_else(runner):
    _, path = runner
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP


def test_a_left_over_socket_file_does_not_stop_the_runner_starting():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "runner.sock")
        old = socket.socket(socket.AF_UNIX)
        old.bind(path)
        old.close()
        server = supervisor.Supervisor(path, CEILINGS)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert call(path)["output"] == {"value": "61/4"}
        finally:
            server.shutdown()
            server.server_close()


def test_closing_the_server_removes_the_socket_file():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "runner.sock")
        server = supervisor.Supervisor(path, CEILINGS)
        assert os.path.exists(path)
        server.server_close()
        assert not os.path.exists(path)


# ----------------------------------------------------------- one child, started by `execute`
def line():
    request = protocol.request(
        source=GOOD, code_sha256=sha(GOOD), output_kind="number", records=RECORDS, params={},
        rules=RULES.to_dict(), limits=dict(RULES.limits),
    )
    return protocol.encode(request)


def run_command(code, **over):
    return supervisor.execute(line(), {**RULES.limits, **over},
                              command=[sys.executable, "-c", code])


def says(body):
    """A child that answers with exactly this, whatever it is sent."""
    return f"import sys; sys.stdout.write({body!r} + '\\n'); sys.stdout.flush()"


@pytest.mark.parametrize(
    "code, kind",
    [
        ("pass", "crash"),
        ("import os; os._exit(3)", "crash"),
        ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "crash"),
        (says("not json"), "bad_output"),
        (says('{"status": "ok", "output_kind": "number", "output": {"v": 1.5}}'), "bad_output"),
        (says('{"status": "fine"}'), "bad_output"),
        (says('{"status": "ok", "output_kind": "number", "output": []}'), "bad_output"),
        (says('{"status": "ok", "output": {}}'), "bad_output"),
        (says('{"status": "error", "kind": "made_up", "detail": "x"}'), "bad_output"),
        (says('{"status": "error", "kind": "static"}'), "bad_output"),
    ],
)
def test_a_child_that_dies_or_answers_wrongly_gives_the_typed_error(code, kind):
    reply = run_command(code)
    assert reply["status"] == "error" and reply["kind"] == kind, reply
    assert isinstance(reply["detail"], str) and reply["detail"]


def test_an_answer_over_the_output_limit_is_bad_output_and_the_rest_is_not_read():
    reply = run_command("print('x' * 100000)", output_kb=1)
    assert reply["kind"] == "bad_output" and "large" in reply["detail"]


def test_a_child_that_hangs_is_killed_at_the_wall_limit():
    started = time.monotonic()
    reply = run_command("import time; time.sleep(60)", wall_s=1)
    assert reply["kind"] == "timeout"
    assert time.monotonic() - started < 10


def test_a_child_that_closes_its_output_and_goes_on_is_still_killed_at_the_wall_limit():
    started = time.monotonic()
    reply = run_command("import os, time; os.close(1); time.sleep(60)", wall_s=1)
    assert reply["kind"] == "timeout"
    assert time.monotonic() - started < 10


def test_a_child_that_answers_correctly_but_not_at_once_is_still_read():
    ok = json.dumps({"status": "ok", "output_kind": "number", "output": {"v": "1"}})
    reply = run_command(f"import time, sys; time.sleep(0.3); sys.stdout.write({ok!r} + '\\n')")
    assert reply == {"status": "ok", "output_kind": "number", "output": {"v": "1"}}


def report(expression):
    return (
        "import json, os; "
        "print(json.dumps({'status': 'ok', 'output_kind': 'number', "
        f"'output': {{'v': str({expression})}}}}))"
    )


def test_the_child_starts_with_a_clean_environment(monkeypatch):
    monkeypatch.setenv("SECRET_TEST_VALUE", "do-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")
    reply = run_command(report("sorted(os.environ)"))
    assert reply["status"] == "ok"
    seen = ast.literal_eval(reply["output"]["v"])
    assert "SECRET_TEST_VALUE" not in seen and "DATABASE_URL" not in seen
    # Python itself may add one locale variable of its own; nothing else may be there.
    assert set(supervisor.child_environment()) <= set(seen)
    assert set(seen) <= set(supervisor.child_environment()) | {"LC_CTYPE"}


def test_the_pinned_environment_fixes_the_hash_seed_and_nothing_secret():
    env = supervisor.child_environment()
    assert env["PYTHONHASHSEED"] == "0"
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
    assert not any(word in key for key in env for word in ("KEY", "SECRET", "PASSWORD", "URL"))


def test_the_child_does_not_inherit_an_open_file():
    read_end, write_end = os.pipe()
    os.set_inheritable(read_end, True)
    os.set_inheritable(write_end, True)
    try:
        reply = run_command(report(f"os.path.exists('/proc/self/fd/{read_end}')"))
    finally:
        os.close(read_end)
        os.close(write_end)
    assert reply["output"]["v"] == "False"


def test_the_child_is_in_its_own_process_group():
    reply = run_command(report("os.getpgid(0) == os.getpid()"))
    assert reply["output"]["v"] == "True"


def test_the_command_for_a_real_child_isolates_the_interpreter_and_names_the_package_root():
    command = supervisor.child_command()
    assert command[0] == sys.executable
    assert "-S" in command and "-P" in command
    assert command[-1] == str(Path(supervisor.__file__).resolve().parents[2])


# ------------------------------------------------------ the entry command of the container
def entry_environment(folder, **extra):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "src"),
        protocol.SOCKET_ENV: os.path.join(folder, "runner.sock"),
        protocol.PARALLEL_ENV: "2",
    }
    env.update({protocol.CEILING_PREFIX + k.upper(): str(v) for k, v in RULES.limits.items()})
    env.update(extra)
    return env


def test_the_entry_command_starts_answers_and_stops_cleanly_on_sigterm():
    with tempfile.TemporaryDirectory() as folder:
        env = entry_environment(folder)
        path = env[protocol.SOCKET_ENV]
        proc = subprocess.Popen(
            [sys.executable, "-m", "mdeis.sandbox.supervisor"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 20
            while not os.path.exists(path) and time.monotonic() < deadline:
                assert proc.poll() is None, proc.stderr.read()
                time.sleep(0.05)
            assert call(path)["output"] == {"value": "61/4"}
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=20) == 0
            assert not os.path.exists(path)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()


@pytest.mark.parametrize("missing", [protocol.SOCKET_ENV, protocol.PARALLEL_ENV,
                                     protocol.CEILING_PREFIX + "WALL_S"])
def test_the_entry_command_refuses_to_start_without_its_settings_and_says_which(missing):
    with tempfile.TemporaryDirectory() as folder:
        env = entry_environment(folder)
        del env[missing]
        done = subprocess.run(
            [sys.executable, "-m", "mdeis.sandbox.supervisor"], env=env, capture_output=True,
            text=True, timeout=30,
        )
        assert done.returncode != 0
        assert missing in done.stderr
        assert not os.path.exists(os.path.join(folder, "runner.sock"))


# --------------------------------------------------------- nothing here is a model tool
def test_nothing_the_model_can_reach_imports_the_runner():
    watched = [ROOT / "src" / "mdeis" / "server.py", ROOT / "src" / "mdeis" / "tools"]
    watched += [ROOT / "src" / "orchestrator", ROOT / "src" / "gateway", ROOT / "src" / "portal"]
    files = []
    for place in watched:
        files += [place] if place.is_file() else sorted(place.rglob("*.py"))
    assert files
    for file in files:
        text = file.read_text(encoding="utf-8")
        for word in ("sandbox.client", "sandbox import client", "sandbox.supervisor",
                     "sandbox import supervisor", "sandbox.child", "sandbox import child",
                     "measure-runner", "MEASURE_RUNNER"):
            assert word not in text, (file, word)
