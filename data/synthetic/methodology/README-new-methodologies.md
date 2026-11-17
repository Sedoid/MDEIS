# Three new methodology fixtures (T-068)

All three are fictional schools with invented numbers. No real school, person or learner is in them.
Each has sections 4, 6.2, 6.3 and 6.4, so the template-link check against `templates/v1.1.yaml` passes.

**`methodology-baobab.md`** is a clean, complete document. Scores out of 100, bands A to E, pass mark 50,
a window of the last 3 term scores, a steady rule (spread at most 8), a moved threshold of 10 and a
minimum of 2 scores. The settings are in tables. It tests that a full document passes. Expected gate
result: no faults. Its 13 hand-worked cases are in `methodology-baobab.cases.json` (B-01 to B-13).

**`methodology-savanna.md`** is clean but laid out differently. A level scale 1 to 5 and a four-level
checklist, no averaging, a group share at level 3 or above, a step change, a sustained level and an
evidence coverage rule. The settings are `**Name:** value` lines, not a table, and four use the gate's
alias names. It tests the gate on structure. Expected gate result: no faults. Its 15 hand-worked
cases are in `methodology-savanna.cases.json` (SV-01 to SV-15).

**`methodology-mangrove.md`** is deliberately loose. It reads like a well-meaning policy and should be
turned back. Faults built in (by rule and section, 21 in all):
- GT-1 (section 3): "Classification rules", "Comparison rules", "Evidence requirements" not defined.
- GT-2 (6.3): Progress has no inputs. GT-5 (6.3): no worked example anywhere in the document.
- GT-3 (6.2): a threshold and a score range, with no source.
- GT-4: "suitable" (4), "appropriate" (5), "as needed" (5), "reasonable" (7).
- GT-6: pass mark 40 in section 2 and 45 in 6.1; trend window 3 terms in 5 and 4 terms in 6.4.
- GT-9: "is applied when" and "are checked for" with no steps (4, twice); "are checked for" (7).
- GT-10: "important" (2, 7), "where possible" (5), "where necessary" (5, 8), "where feasible" (8).
GT-7 and GT-8 are not expected. These come from my own reading and a small stand-alone check of my
own, not from the gate.

## Commands

- Gate, no database: `make gate FILE=data/synthetic/methodology/methodology-baobab.md TEMPLATE=templates/v1.1.yaml`
  (same for `-savanna.md` and `-mangrove.md`).
- Upload through the portal as an admin. A clean document goes live or waits for review. Mangrove is
  turned back with its fault list.
- If a document waits for review: `make ack DOC=D-... BY="Name"` (add `NOTE="..."` if wanted).
