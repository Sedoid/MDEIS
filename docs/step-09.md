# Step 9: the readers

Branch: `step-09-readers` (from `step-08-tools`). Status: written, **not run on the server**. The card
reader was checked offline against all 40 practice cards, the real sample card and the six broken
cards (details under "What was checked here"), but no test in this step has been run by `pytest`.

**Goal.** Documents go in, records come out, and every record knows the page it came from. Two
readers (report cards, the methodology), the worker that runs them, the two admin commands that run
them by hand, and the version pins in the tools. No model reads any file.

## The plan

1. The report card reader: words by position with pdfplumber, rows found by the numbers on a line,
   one typed failure for a card it cannot read, nothing stored when it fails.
2. Storage: the learner (if new), the card, its subject rows and one record per term score, in one
   transaction, each record with its document, page and row.
3. The methodology reader: numbered sections with pages, compared with the version in force and with
   the sections the school's rules rest on. A changed document waits for a named person.
4. The worker (`ingestd`): claims documents safely, runs the reader with a time limit, logs each
   document, recovers a stuck one.
5. `make ingest` and `make ack`, which run the same code from the command line.
6. The pins in the tools: a result names the methodology in force; a draft remembers its rulebook
   and methodology; a citation leads to its document and page; a release is refused when either
   moved (the refusal itself was built in Step 8).

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D9-1 | `pdfplumber` moves from the dev tools to the runtime dependencies (`pyproject.toml`). It is MIT licensed; please confirm and pin the version when you build the image | `docs/INGEST.md` names it as the reader. The test tools already used it. D-014 in `DECISIONS.md` is still `open`: it needs your edit to say yes |
| D9-2 | The reader runs in a child process with a 30 second limit; on timeout the document fails with `parse_timeout` | A file that hangs the reader cannot stop the worker |
| D9-3 | A card is read by position: the column of a word is the header word nearest to its left edge; a row is a line with a whole-number coefficient and values under the terms | `docs/INGEST.md` section 3: table detection splits this layout badly |
| D9-4 | The failures, in order: too large, not a PDF, too many pages, no text layer, no title (`layout_unrecognised`), no ID number, title year differs from the Academic year field, no subject rows, a row that is not a coefficient plus five values (`row_unreadable`, with page and row). Limits: a card is at most 8 MB and 12 pages; a methodology 20 MB and 80 pages | The kinds are the ones in `docs/INGEST.md`. The limits are mine |
| D9-5 | A score is stored exactly as printed (`raw_score`); a blank term (`-` or `ABS`) is a record with no score, never zero. Nothing printed is recomputed or rounded | The tools compare the printed totals with their own sums (`printed_value_mismatch`) |
| D9-6 | The school code is the part of the ID number before the first `/` (`OCC` from `OCC/2024/0001`). The learner is created on first sight with the card's name. Date of birth and gender are checked to be on the card and are not kept | `learners.school_code` is required; the spec says the two fields are not stored |
| D9-7 | A methodology is a PDF with a text layer or Markdown. In a PDF the headings come from the font (bold 17 pt starting with a number, bold 13 pt, bold `A.` to `F.`), exactly as the tool that made the Markdown copies reads them. A section is "changed" when its own words differ after spaces and line breaks are ignored | One way of reading headings in both places. Reflowing a paragraph is not a change |
| D9-8 | A rule "rests on" a section when its `method_ref` is that section or sits inside it. Any finding (added, removed or changed section, a rule whose section is missing, a rule resting on a section that moved, no numbering, a repeated number) makes the document `needs_review`; a clean one is `done` and in force | `docs/INGEST.md` section 4 |
| D9-9 | `acknowledge` needs a name, only works on a document waiting for review, and is logged when refused too. The person's name is the approver on that diary line | `docs/INGEST.md` section 2 and 6 |
| D9-10 | The worker takes one document at a time (`FOR UPDATE SKIP LOCKED`). A document stuck in `processing` for 5 minutes goes back to `pending` once; the second time it fails with `parse_timeout`. The same file twice (same SHA-256 and kind) is marked `done` with `duplicate_of` and adds nothing. A missing file, a changed file or a bug fails the document as `internal_error` with a detail that names the kind of error, never its message | Safe by default; nothing partial is kept |
| D9-11 | One diary line per document (`ingestd`): document, kind, hash, uploader, status, counts. Never file contents or learner names | Non-negotiable 9, and the privacy rule |
| D9-12 | Vectors for a methodology in force are made after the commit, only if `EMBEDDER_URL` is set. A failure sets `embedding_status = failed` and never fails the document | The only network call in the worker, to the embedder only |
| D9-13 | `compute_trend` and `get_criterion` return `methodology_document_id`; `result_hash` does not cover it. `get_criterion` also returns the sections of the methodology in force, by ID (`M-6.4`), by meaning, or by words. `draft_summary` pins the methodology in force | `docs/INGEST.md` section 5 |
| D9-14 | `check_citations` accepts `[M-6.4]` only if a tool returned it in the run **and** it is in the draft's pinned methodology, otherwise `unknown_section`. A rule must also be in the draft's pinned rulebook version. `sources[]` resolves a record to its document, page and row, a rule to its version, a section to its document, title and page | E-17 |

## What was built

| File | What |
| --- | --- |
| `src/mdeis/ingest/report_card.py` | the card reader; `python -m mdeis.ingest.report_card FILE` prints the result as JSON |
| `src/mdeis/ingest/store.py` | stores a read card in one transaction |
| `src/mdeis/ingest/methodology.py` | the methodology reader, the comparison, storing, `acknowledge`; `python -m mdeis.ingest.methodology FILE` |
| `src/mdeis/ingest/worker.py` | `ingestd`: replaces the heartbeat-only stub |
| `src/mdeis/admin.py` | adds `ingest` and `ack-methodology` |
| `src/mdeis/tools/*` | the pins (D9-13, D9-14) |
| `deploy/compose/mdeis-mcp.yml`, `Makefile` | an `admin` one-shot service; `make ingest`, `make ack` |
| `pyproject.toml` | `pdfplumber` as a dependency |
| `tests/unit/test_report_card.py` | the reader's logic on small hand-made pages (blank terms, a split remark, every failure) |
| `tests/unit/test_tool_citations.py` | the new citation reasons |
| `tests/acceptance/test_report_cards.py` | E-12 and E-13: every practice card, the real sample and the six broken cards, against their truth files |
| `tests/acceptance/test_methodology_docs.py` | E-15, reading half: sections, diffs and the rules resting on a change, against the truth files |
| `tests/acceptance/test_ingest_db.py` | E-13 (no row kept), E-14 and E-15, storing half: the real worker on a throwaway database |
| `tests/acceptance/test_pins.py` | E-16 and E-17 |

## What was checked here (static checks, not test runs)

- Everything compiles; no line is over 100 characters; `scripts/check_structure.py` and
  `scripts/scan_secrets.py` pass.
- The card reader's logic (not pdfplumber) was run on the words of every practice card, rebuilt from
  the text and positions inside the PDF files, and on the real sample card rebuilt from the position
  of each line as macOS reads it. All 40 practice cards and the real sample came out equal to their
  truth sheets, and the six broken cards gave their expected failure at the expected page and row.
  This proves the logic, not pdfplumber's exact word boundaries: that is what the server run is for.
- The methodology reader's logic was run on the four Markdown copies: the sections equal their truth
  files, the differences equal `methodology-changes.json`, and TR-1 is named as resting on 6.4. The
  PDF path (headings by font) has not been run at all.
- The hand-made-page tests of the card reader, the number tests and the token tests were run with a
  small stand-in for pytest. The database tests have not been run.

## Server test process

```bash
git fetch && git checkout step-09-readers && git pull
make test ARGS="tests/unit -q"                                   # the pieces that need no database
make test ARGS="tests/acceptance/test_report_cards.py tests/acceptance/test_methodology_docs.py -q"
make test ARGS="tests/acceptance/test_ingest_db.py tests/acceptance/test_pins.py -q"
make test ARGS="-q"                                              # everything, including the tool contract tests
make lint
```

Then, with the stack up, the same code by hand:

```bash
make dev-up && make load-criteria
make ingest KIND=results FILE=data/synthetic/cards/amina_fonyuy_form1_2023-2024.pdf AS="Your Name"
make ingest KIND=results FILE=data/synthetic/cards_bad/unreadable_row.pdf AS="Your Name"
make ingest KIND=methodology FILE=data/synthetic/methodology/methodology-v2.0.md AS="Your Name"
make ingest KIND=methodology FILE=data/synthetic/methodology/methodology-v2.1-small.md AS="Your Name"
make dev-psql        # then:
  SELECT document_id, kind, status, active, failure_kind, failure_page, failure_row FROM documents;
  SELECT kind, ref_id FROM document_issues;
  SELECT count(*) FROM assessments WHERE source_page IS NOT NULL;
make ack DOC=<the id of the 2.1 document> BY="Your Name" NOTE="read 6.4"
```

**What "right" looks like.** The unit and acceptance commands are green. The first card ends `done`
with `records: 42` and `subjects: 14`. The unreadable card ends `failed row_unreadable (page 1, row 7)`
and adds no learner or record. The first methodology ends `done` and in force; the 2.1 copy ends
`needs_review`, with `section_changed 6.4` and `referenced_section_changed TR-1`, and the first is
still in force until `make ack`. After `make ack` the 2.1 copy is in force and the diary has the
refusal and the acknowledgement with your name.

**What to paste back if something fails:** the failing test names and the last 40 lines of the
command. The most likely first problems, in order: pdfplumber cutting a word differently from my
model of it (a card that reads as `row_unreadable` or with a wrong remark); the real methodology PDF
giving a different list of sections (heading fonts); a column name in the SQL; an import order that
`make lint` wants changed. A test is not weakened to pass: if one looks wrong, say so.

## Limits

- Text-layer PDFs in the one card layout (OCC-1) only. A scanned card fails with `no_text_layer`.
  No OCR.
- The reader's geometry (distances such as the 2.5-point line tolerance) was chosen against the
  practice cards and the real sample. A different layout is a different reader.
- Remarks are matched to rows by position. A row whose remark is taller than its neighbours are
  apart could take its neighbour's lines; the practice cards and the real sample do not do this.
- The upload page and its status list (the portal) are not built in this step.
- A "Sources" block added by code to the stored report text is not built: `check_citations`
  returns `sources[]` instead, and the text the writer produced is kept as written. Code-made text
  would need its own markers so that the check would not reject it.

## Needs your edit (contract docs are not edited by me)

- `docs/DECISIONS.md`: D-014 says `open`; it needs the yes on `pdfplumber` (D9-1).
- `docs/SPEC.md` 5.5 to 5.7: `methodology_document_id` in `compute_trend` and `get_criterion`; the
  new `sections` in `get_criterion`; the code `unknown_section`.
- `docs/INGEST.md` section 2: the failure kind `internal_error` is used for a file that is missing
  or changed, and for a bug.
- `docs/TESTS.md`: E-12 to E-17 now have test files (above).
- `docs/STRUCTURE.md`: the new files (the docs-keeper's job).
