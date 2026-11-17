"""A runner for tests that works in this process: it builds the same request the client would send
and hands it to `child.answer` (the real static check, the real restricted namespace, the real
exact-text conversion), with no socket and no limits. It has the signature of `client.run`, so the
code under test cannot tell the difference. Used by the params-channel and sensitivity tests."""

from mdeis.sandbox import child, protocol
from mdeis.sandbox.client import RunnerRefusal
from mdeis.sandbox.config import load_rules

RULES = load_rules()


class Calls(list):
    """The runner, and a record of every call it was given (for `len(runner)` and `runner[0]`)."""

    def __call__(self, source, code_sha256, records, params, *, output_kind, **_ignored):
        self.append({"source": source, "records": records, "params": dict(params)})
        reply = child.answer(
            protocol.encode(
                protocol.request(
                    source=source,
                    code_sha256=code_sha256,
                    output_kind=output_kind,
                    records=records,
                    params=params,
                    rules=RULES.to_dict(),
                    limits=dict(RULES.limits),
                )
            )
        )
        if reply["status"] != "ok":
            raise RunnerRefusal(reply["kind"], reply["detail"])
        return {"output_kind": reply["output_kind"], "output": reply["output"]}
