# Step 22: a whole class, before the teacher tests

Branch `step-21-charts`. This is a run sheet, not new code, apart from one make target
(`make class-data`) and one `.gitignore` line.

## Order matters

1. **First finish the hand tests and the stress evals** (E-07c release, E-22 planted instruction,
   E-19 upload then ask, `make evals`). They rely on particular learners staying as they are: for
   example the eval for "a problem put to the approver" uses Collins Eyong (FX-04b), whose Chemistry
   score above the maximum must still be unanswered. A teacher who answers that question changes the
   data under the evals. Run them before she starts.
2. **Then load the class and the methodology.** The cohort is 60 learners. The committed cards cover
   40 of them (the 13 named fixtures and some more). `make class-data` prints a card for every learner.
3. **Then give her an account and a short script.**

## Make the whole class

```
git pull
make class-data
ls data/full-class/cards | wc -l
```

The files go to `data/full-class/` (ignored by git, never committed). The same seed gives the same
learners, so the 40 cards already loaded are the same files again: they are reported as
"the same file" and add nothing. Only the new cards add records.

## Methodology (administrator only)

1. Portal, signed in as the administrator: Methodology box, upload
   `data/synthetic/methodology/methodology-v2.0.md`.
2. It shows "Read; a person must check it". Run the command the page gives you:
   `make ack DOC=D-xxxxxx BY="Demo Approver" NOTE="checked"`.
3. Optional, to see what a change looks like: upload `methodology-v2.1-small.md` and read the list of
   changes on the page before you acknowledge it (`make ack` again). A report written under the
   earlier methodology then shows as out of date; it is never rewritten.

The teacher's account cannot upload or acknowledge a methodology. That is deliberate.

## Report cards for the class

Many files: use the command line, which is faster than selecting them in a browser.

```
make ingest-dir KIND=results DIR=data/full-class/cards AS="Demo loader"
```

It prints one line per card and a count. A few of the cards printed for other fixtures are meant to
fail (see `cards_bad` and `injected_problems.json`); the committed set had one expected failure.
Paste the count back.

The portal is for the teacher's own test: she signs in, uploads one or two cards, watches them move
from waiting to read, then asks about that learner.

## The teacher's account

1. Open WebUI, Admin Panel, Users, add a user with the role **User** (not Admin). Sign-up is off, so
   only accounts you make can sign in.
2. Give her the address, the email and the password. She uses `http://13.140.150.214:8088/portal/`.
3. As a "User" she has no attach button, no controls, no workspace pages, no voice and no
   methodology upload.
4. Tell her:
   - everything here is made up, do not enter a real learner;
   - the connection is plain http, so do not reuse a real password;
   - ask by learner number (like `L-0111`), because names cannot be looked up;
   - the system describes records and never ranks or labels a learner;
   - nothing is released until she (or the approver) asks for it by name.

## Things to watch while she tests

- Every call is in the diary: `make dev-psql`, then
  `select at, tool, outcome, approver_name from action_log order by event_id desc limit 20;`
- Answers she gives to a question about the records are kept for good (they are not undone).
- If she releases a report, `reports.status` becomes `released` under her name. That is the real gate.
- To start over: `make clean` deletes the data volumes. Only do it after the evals and the hand tests
  are recorded.
