# Step 23: the teacher's data, and a portal that explains itself

Branch: `step-23-portal-files` (from `step-21-charts`). Status: **built, not yet run on the server**
(sections 4 and 7). Sections 1 to 3 are what the dry run found, and are done: all 50 of the teacher's
cards read after two reader fixes (see section 7). Today is 2026-10-14; the deadline is 2026-10-15.

## 1. What is in the folder (read from the files, not guessed)

- `data/MDEIS SYNTHETIC REPORT CARDS/`: 10 learners, one folder each, 51 files: Andy Koskar, John
  Williams, Randy Tantoh, Carlson Nfor, Mohammed Bachirou, Stepne Chioma, Amara Nfor, Badawe Zakiatou,
  Amina Vakia, Mary Berinyuy. One card per learner per school year, Form 1 to Form 5, from 2018/19 to
  2027/28.
- **One file is HTML, not PDF** (`randy tantoh form4-science 2025-2026.html`). The portal and the reader
  take PDF only. It has to be printed to PDF first.
- The cards use the same layout as the OCC sample card (ID number `OCC/2022/0185`, Class, Academic year,
  coefficients, ABS for an absent term, `*` for an average of the terms recorded, remarks, absences,
  council decision). So the reader should recognise them. Unverified until a dry run.
- Several things on the cards are **not in the rulebook** (`templates/v1.yaml`):
  - **Stages**: "Form 4 Science", "Form 4 Arts", "Form 5 Science", "Form 5 Arts", and the file names
    show "first sitting" and "repeat" years. The rulebook has "Form 1" to "Form 5", "Lower Sixth",
    "Upper Sixth" only.
  - **Subjects**: Additional Mathematics, Food and Nutrition and Economics are not in the coefficient list.
- What that does: the reader will still read each card. An unknown subject becomes a question
  (`unmapped_subject`: "Which rulebook subject is Economics?"). An unknown stage is stored as printed,
  but the trend tool then refuses with "the rulebook has no stage weight for Form 4 Science", and no
  expected range exists for it either. So **numbers and charts for these learners need a new rulebook
  version**, with values only the teacher can confirm.
- `Methodology of MDEIS v3.pdf` and `Methodology of MDEIS v 2.2.pdf` are **the same file** (identical
  checksum, and the title inside says "v 2.2"). Uploading both reports the second as "the same file".
  I need the real v3.
- `data/MDEIS SYNTHETIC REPORT CARDS-…zip` is a copy of the folder. All of these are now ignored by
  git (`.gitignore`), so none of the teacher's files can be committed by accident.

## 2. Phase 0: load it now, as a developer (about 30 minutes, you)

Copy the folder to the server (not through git), for example with `scp -r`, into `data/incoming/`.
Then, one learner first, to see what the reader makes of it:

```bash
git pull
make ingest-dir KIND=results DIR="data/incoming/MDEIS SYNTHETIC REPORT CARDS/Andy Koskar " AS="Demo loader"
```

`RECURSIVE=1` reads the sub-folders in one go, and leaves the HTML card alone with a line saying so:

```bash
make ingest-dir KIND=results DIR="data/incoming/MDEIS SYNTHETIC REPORT CARDS" AS="Demo loader" RECURSIVE=1
```

Read the output line by line. A card that fails prints its failure kind (and page and row where there is
one): keep those lines, they go in `EVALS.md` under what the reader could not do. Then:

```bash
make dev-psql
select kind, count(*) from documents group by 1;            -- results / methodology
select status, count(*) from documents group by 1;          -- done, failed, needs_review
select learner_id, school_ref from learners order by 1;     -- the new learner numbers
select kind, count(*) from issues group by 1 order by 2 desc;   -- what the records raise as questions
\q
```

Methodology (use the portal as the administrator, which is the path a user takes): upload
`Methodology of MDEIS v 2.2.pdf`, read the status ("Read; a person must check it"), then
`make ack DOC=D-… BY="Demo Approver" NOTE="checked"`. It is a Google Docs PDF with numbered headings
like `2.1 Use evidence`; the reader was tested on other layouts, so a section count that looks wrong is
a finding.

**Paste back the outputs.** Everything below depends on them.

## 3. The rulebook gap (decision for the teacher, before any chart on these learners)

A new rulebook version, `templates/v3.yaml` (a new file; v1 stays as it is), with:
stage names as the cards print them and their weights; the extra subjects and their coefficients; which
area each belongs to (SK-1 literacy, SK-2 numeracy are the only two now); and an expected range per
stage and area. Every value is hers to confirm; each carries a clause ID and the PLACEHOLDER mark until she does.
Then `make load-criteria`. I can draft the file from the cards and the methodology, marked
PLACEHOLDER, for her to correct. If there is no time: the system still reads the cards, asks the
questions, and charts the stages the rulebook knows; for the others it says plainly that the rulebook
has no rule yet. That is an honest result for the video.

## 4. The portal changes you asked for

Principle: the page explains itself. No new screens and no new design language: same tokens, same
sidebar, same table and pill styles. Native `<details>` for the accordion (no script needed, keyboard
and screen-reader friendly, allowed by the page's content policy).

### A. Methodology: what is in force, and replace it

In the Upload view, the Methodology box becomes a small panel (administrators only):

| State | What the panel shows |
| --- | --- |
| None yet | "No methodology yet. Reports are written without one and say so." and the upload box |
| One in force | "In force: *file name*, checked by *name* on *date*" (and its section count), a "Replace it" upload box, a note: "Reports already written stay pinned to this one and show as out of date after a replacement." |
| One waiting | "Waiting for a person to check it", the list of changes against the one in force, and the exact command to copy |
| Same file again | "This is the same file as D-…; nothing was added." (this is what the identical v3/v2.2 files will show) |

Behind it: one new read call, `GET /api/methodology` (in force, waiting, earlier ones), from the
`documents` rows the portal role can already read. No schema change.

**Acknowledging from the page** is not in this plan. Acknowledging names a person and changes which
methodology is in force, and the diary line for it is written by the admin code, not by the portal
role (which can only insert and read documents). A button would need either a wider portal role or a new
request path: a design decision, not a UI detail. For the video, run `make ack` in a second terminal
(about 15 seconds). If you want the button, say so and it becomes its own step.

### B. Files grouped by class and year

The Files view becomes accordions, one per **stage and school year**, for example
"Form 4 Science · 2025/26 — 6 cards, all read". Inside, one row per card as now (file, status pill,
detail). Two groups sit on top and open by themselves when they are not empty: **Being read** (waiting or
processing, so the page shows progress) and **Could not be read** (each with its plain-words reason).
Then the groups, newest year first.

This needs the stage and year, which are only known after a card is read, so the list query joins
`report_cards`. The portal role cannot read that table today, so it needs one small additive grant in
a new migration: `GRANT SELECT (document_id, academic_year, stage) ON report_cards TO portal_app`. That
is a schema change, so **it needs your yes.** No learner, score or name column is granted.

Cut line if there is no time for the grant: group by status only (Being read, Read, Could not be read,
Needs checking). It needs no change to the database and still explains itself.

### C. Upload many, or a whole folder

- The card box already takes many files. Add "or choose a folder" (`webkitdirectory`): the PDFs inside
  are queued, anything else is skipped with a line saying which ("1 file skipped: not a PDF").
- Uploads go three at a time, with a count: "12 of 51 uploaded", and a "stop" button; a failed file does
  not stop the others. The existing size and type limits stay (10 MB, PDF only).
- Dropping a folder on the box does the same where the browser allows it.

### D. Small things that make the demo self-explanatory

- The Files page empty state says what to do next ("Upload a folder of report cards").
- Each learner's chat link: none. Learner numbers are shown in a group row only after a card has been
  read? **Not planned**: that would need the learner column too. The chat asks by number; after the
  upload the group header says "10 learners". Skip unless asked.

## 5. Tests first (written before each change)

| Piece | Tests |
| --- | --- |
| `GET /api/methodology` | none yet, one in force, one waiting, same-file duplicate, history; non-admin gets 403; no file name leak beyond what the list shows |
| Files list with stage and year | rows with a card carry stage and year; rows without one do not; the fake store and a real-database test with the portal role's grant (it cannot read `learner_id` or scores: a test that tries must fail) |
| Page rules | still no inline script or style, no `innerHTML`, one `fetch(`; `<details>` used; text set with `textContent` |
| Folder queue | pure function: from a list of files, the PDFs in name order, the skipped ones named, at most N at a time |

## 6. Order, time, and what I need

| # | What | Who | Time |
| --- | --- | --- | --- |
| 0 | Dry run of the cards and the methodology (section 2) | you | 30 min |
| 1 | Methodology panel (A) | me | about 1.5 h |
| 2 | Files grouped (B), after the grant is approved | me | about 2 h |
| 3 | Folder upload with progress (C) | me | about 1 h |
| 4 | `templates/v3.yaml` draft for the teacher (section 3) | me, with the dry-run output | about 1 h |
| 5 | Run sheet, merge to `main`, video | you | rest of the day |

**I need from you:**
1. The outputs of section 2 (this decides how much of section 3 is needed).
2. A yes or no on the `report_cards` grant (B).
3. The real v3 methodology (the file you added is v2.2).
4. The HTML card printed to PDF, and the teacher's written confirmation that all cards are made up.
5. Whether you want the acknowledge button as a later step.

**If the day runs short, cut in this order:** the folder drop (C), the grouping by year (fall back to
grouping by status), the methodology history list. The methodology panel (A) and the dry run (section 2)
stay, because they are what the video shows.


## 7. What was built (you approved the grant)

### The reader, after the dry run (done, pushed before this branch)
First run: 22 of 50 read, 28 failed. Two causes, both found from the cards' own text:
- an annual score ending in `*` (the card's mark for an average over the terms that have a result);
- a teacher's remark that says "Term 2" moved the term columns, so ordinary words were read as scores;
- a long title wrapped onto two lines ("... (FIRST SITTING) · ACADEMIC / YEAR 2025/2026").
After the fixes: **50 of 50 read, 0 failed**. The first run's failures belong in `EVALS.md` as a
real finding, with the failure kinds above. Unknown subjects (Additional Mathematics, Food and
Nutrition, Economics) and stages ("Form 4 Science", "(First Sitting)", "(Repeat)") are not in `v1`;
they surface as questions and as "the rulebook has no rule" until a `v3` rulebook exists (section 3).

### The portal (this branch)
| What | Where |
| --- | --- |
| Migration 004: the portal role may read `academic_year` and `stage` of a card, nothing else of it | `src/mdeis/db/migrations/004_portal_files.sql`, checks in `deploy/postgres/checks/roles.sql` |
| The list joins the card's stage and year; a failure that was later read from the same file is no longer listed; up to 500 rows | `src/portal/store.py` |
| `GET /api/methodology` (administrators): in force, waiting, earlier | `src/portal/app.py` |
| Files grouped as accordions by stage and school year (newest first), with *Being read*, *Could not be read*, *Needs checking* on top and *Same file uploaded again* at the bottom; a summary line; open or closed as you leave them | `src/portal/static/app.js`, `index.html`, `style.css` |
| Methodology panel: what is in force (file, who checked it, when), "Replace it", waiting for a check with the changes and the exact command to copy (with your name in `BY=`), earlier uploads | same |
| Upload: many files, or "choose a whole folder"; PDFs only, others skipped by name; three at a time; "12 of 51 uploaded"; Stop; a dropped connection keeps the files | same |

Not built: acknowledging from the page (stays `make ack`; the page prints the command with your
name in it).

### Checks run here (not pytest, not a browser)
`node --check` on `app.js`; a fake page was driven with sample documents: the groups came out in the
right order (2025/26 before 2023/24), the counts and summary were right, the empty state showed, and
the methodology panel drew its three states (in force with earlier uploads, none, being read); the page
rules from `test_portal.py` (no inline script or style, no `innerHTML`, one `fetch(`, no `/api/` path in
the script) were replayed by hand. The database tests could not be run here.

### Server run sheet
```
git fetch && git checkout step-23-portal-files && git pull
make lint
make test ARGS="tests/unit/test_portal.py tests/unit/test_portal_store.py tests/unit/test_migrations_static.py -q"
make dev-up
```
`make dev-up` applies migration 004 (the `migrate` container). Expect lint clean and the tests passing.
Then check the grant: `make dev-psql` and paste

```sql
set role portal_app;   -- if this says permission denied, skip it and run the next line only
select document_id, academic_year, stage from report_cards limit 3;
select learner_id from report_cards limit 1;   -- must be refused
```

Then in the browser at `http://13.140.150.214:8088/portal/` as the administrator:
1. **Files**: the cards in groups such as "Form 2 · 2023/24", the newest year first; the summary line;
   open and close a group and wait: it stays as you left it.
2. **Upload, Methodology**: the panel says what is in force (or "No methodology yet"). Upload
   `Methodology of MDEIS v 2.2.pdf`: it appears as "Waiting for a person to check it" with the changes
   and a command to copy. Run it. The panel then says "In force", with your name and the date.
3. Upload the same file again: it is listed under Earlier uploads as "Same file as D-…".
4. **Upload, Report cards**: "choose a whole folder" with the teacher's folder (copy a few cards to
   your Mac's Desktop first if you like). The HTML card is skipped by name. Watch "n of m uploaded";
   try Stop. Everything already read shows as "Same file uploaded again".
5. Sign in as the teacher's "User" account: Files works, no methodology panel, no methodology box.
Paste screenshots.


## 8. Open a file in a new tab (added later)

Every file name in the Files list, and every methodology in the panel (in force, waiting, earlier), is
now a link that opens the uploaded file itself in a new tab: `GET /api/documents/D-…/file`.

- The owner of a file, and administrators, can open it; anyone else gets "no such document". Signing in
  is needed (the new tab carries the same sign-in cookie).
- A PDF is shown by the browser's own viewer. A Markdown methodology opens as plain text.
- The path on disk is built only from a name the portal or the admin tool made (32 hex digits and
  `.pdf` or `.md`). Any other stored name is refused, so no path can be steered.
- The page's content policy is not sent with a PDF (the browser's PDF viewer would be blocked by it);
  the type is fixed and `nosniff` is set so a file is never treated as a web page.
- A file that is no longer in the inbox says so ("no longer available").

Server run sheet: `git pull`, the same `make test ARGS=...` as section 7, `make dev-up`, hard-refresh,
click a file name in Files and the methodology name in the panel. Paste what opens (or the message).
