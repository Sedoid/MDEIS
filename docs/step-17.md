# Step 17: one command, and a README that tells the truth

Branch: `step-17-one-command` (from `step-16-website`). Status: written, **not run**. `make demo` has
never been run end to end and is described as **planned** everywhere until it has.

**Goal.** A stranger can clone the repository, edit `.env`, and get a running system with data in it;
and every public page says only what has been done.

## What changed

| File | What |
| --- | --- |
| `Makefile` | `demo-data` now really loads: the rulebook, the methodology, and every practice card. New `make ingest-dir`. `make demo` step 5 calls it |
| `src/mdeis/admin.py` | `ingest-dir`: reads every PDF in a folder in name order, one line for each, exit 1 if any failed |
| `README.md` | A status table with three honest levels (run on the server, written and awaiting a run, planned); setup steps that exist; the borrowed MCP server named; the full list of reused open-source software; the weakness of the class view stated |
| `ARCHITECTURE.md` | The graph as it is built (no checkpoint; the conversation is the state), the optional proxy, who signs and who only checks the token |
| `docs/STARTUP.md` | Status line no longer points at a local-only file and says which commands have been run |

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D17-1 | `make demo-data` loads the methodology as the **Markdown** copy (`DEMO_METHODOLOGY`), and the 40 practice cards. Set `DEMO_METHODOLOGY=data/synthetic/samples/Methodology_of_mdeis.pdf` to try the PDF reader | The Markdown path is the one I could check; the PDF path reads headings by font and has not run |
| D17-2 | The demo loads the cards in `data/synthetic/cards` only, not the broken ones in `cards_bad` | A demo should start clean; the broken cards are for the tests |
| D17-3 | Running `make demo-data` twice adds nothing: the rulebook is unchanged, and a file already read is marked a duplicate | "Safe to run twice" |
| D17-4 | The README does not call the one command done. It stays **Planned** until the stranger test passes | `docs/PUBLISH.md` and the challenge's "a repo that doesn't run" disqualifier |

## What you do (the stranger test)

On a machine that has never run MDEIS, or a throwaway directory with no volumes, using throwaway keys
you revoke afterwards:

```bash
cd "$(mktemp -d)"
git clone https://github.com/Sedoid/MDEIS mdeis && cd mdeis
make env-init                 # then edit only the lines it marks for you
time make demo
```

**Pass** means: following only the README it ends with the banner and an address, and only `.env` was
edited by hand. Also run it once with a variable missing and check that the message names it, and once
twice in a row. Then change the status table row and the word **Planned** in the README and in
`docs/STARTUP.md`. Repeat after the last change and again before submitting; do not commit after the
deadline.

If `make demo` stops, paste the last 40 lines. The likely first stops, in order: a variable `make
demo` checks that `.env.example` does not set; an image tag that does not exist; `make demo-data`
failing on a card (it prints which one and why); the smoke test.

## Server test process for this step

```bash
git fetch && git checkout step-17-one-command && git pull
make test ARGS="-q"
make lint
make dev-up && make demo-data         # expect: rulebook loaded, methodology done, "40 of 40 read; 0 failed"
make demo-data                        # again: nothing added
make dev-psql                         # SELECT count(*) FROM learners;  -- the practice school's learners
```

## Needs your edit

- `.env.example` lines for the variables added in Steps 9 to 16 (I cannot edit it): `ORCHESTRATOR_MODE`,
  `ORCHESTRATOR_MAX_STEPS`, `ORCHESTRATOR_MAX_REDRAFTS`, `CLASS_VIEW_ENABLED`, `APP_HOST`, `PROXY_BIND`,
  `WEBUI_SESSION_COOKIE_SECURE`. All have safe defaults in the compose files.
- The README's team section still says "Names to be added".
- `docs/STRUCTURE.md` rows for the new folders and files (the docs-keeper's job).
