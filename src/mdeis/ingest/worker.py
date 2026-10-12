"""STUB for the document worker (ingestd).

It only proves the wiring: every few seconds it opens the database and, if that works, touches a
heartbeat file that the container healthcheck reads. It claims no documents and parses nothing.
The claim loop and the parsers come in T-035 and T-038.
"""
import os
import pathlib
import sys
import time

import psycopg

HEARTBEAT = pathlib.Path("/tmp/ingestd.heartbeat")
INTERVAL_SECONDS = 10


def main() -> None:
    url = os.environ["DATABASE_URL"]
    print("ingestd stub: no parsers yet, only a heartbeat", flush=True)
    while True:
        try:
            psycopg.connect(url, connect_timeout=3).close()
            HEARTBEAT.touch()
        except Exception as e:  # (log the class only, never the connection string)
            msg = f"ingestd stub: database not reachable ({type(e).__name__})"
            print(msg, file=sys.stderr, flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
