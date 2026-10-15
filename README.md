# Lucent AI

*Working name in the repository, the package and the commands: MDEIS (Multidimensional Educational Intelligence System).*

Entry for the **African Agentic AI Design Challenge** (Nairobi AI Community and Qhala Trust),
Education track, "The Long View".

Lucent AI reads one learner's school records across subjects, terms and years, and reports what the
data shows against a **criteria template the school supplies**. Every finding cites the records
and the template clauses behind it. A **named human approver** must release every report.

**Demo sign-in** (the demo server holds synthetic data only):

- Approver email: `approver@example.com`
- Approver password: `564663220b9508b9ecab3cf060f1177ab818f71b9614efa2`

| Part | Status |
| --- | --- |
| Design, tool contracts, test plan | Written (`docs/`) |
| Criteria templates (a normal and a stricter one) and their loader, database and migrations, the diary, the synthetic practice school and its answer key | Built and run on my server |
| The eight tools and the MCP server, with the contract tests that define them | Built and run: the 71 contract tests pass on my server, and so do the 514 unit tests (one skips itself when the embedder is not running) |
| Report-card and methodology readers, the document worker, version pins | Built and run. 50 made-up report cards from a teacher were read: 22 of 50 on the first try, then 34, then 50 of 50 after three reader fixes (details in `EVALS.md`) |
| Orchestrator (a plain tool loop, and the LangGraph agent), the gateway | Built and run end to end through Open WebUI. The agent evals are in `EVALS.md` |
| Upload page (sign-in, upload by file or folder, files grouped by class and year, methodology in force, files open in a new tab) | Built, and I have used it in a browser |
| Progression charts in the chat, drawn from stored scores only | Built and run |
| Class view (counts only) | Built and **locked**: off, and not listed, unless switched on. I ran it once with the switch on. Its automated tests are slow and not all confirmed |
| Public page and reverse proxy | I run it on a bare IP address over plain http for the demo (no domain, no certificate) |
| `make demo` (the one command) | **Planned** until it has passed from a fresh clone on a clean machine |
| Evaluation results | Five real runs in `EVALS.md`: ten scenarios, five attempts each. Run 3 scored 49 of 50 (one step-limit miss, recorded), run 4 scored 50 of 50, and run 5 re-ran the one failing scenario (5 of 5, a small sample) |

**All data in this repository is synthetic.** There are no real learners, names, photos or
schools.

---

## Problem statement

In many African schools a learner can move up a class without having the basics of the class
below. A learner in Form 3 may still read, write, listen or do arithmetic at a Form 1 level, and
nobody sees it until it is very late. The evidence exists, in marks, assessment types and teacher
notes spread over terms and years, but it is scattered, uses different scales, and is rarely read
as one story.

Existing "AI for schools" tools tend to do one of two harmful things: hand a model a pile of
marks and let it say what it likes (unsourced, unrepeatable), or put a judgement on a child
("weak", "gifted", a rank, a career path). The challenge forbids both, and so do I.

## Solution overview

Lucent AI is a small agent system that:

1. **Loads** one learner's assessment records and converts every scale using the school's
   template. Messy data (missing terms, duplicates, marks above the maximum, notes that
   contradict marks) becomes a **typed issue** with a plain question, never a silent fix.
2. **Computes**, with deterministic code only, a trend per skill across terms and the **gap to
   the stage standard**: where the learner's work sits against what the template expects at that
   stage. The comparison is always with the standard, never with classmates.
3. **Drafts** a plain-language report. The model writes words only. Every number comes from a
   tool, and every sentence carries record and clause citations.
4. **Checks** the draft mechanically (`check_citations`) and **holds** it until a named approver
   reads it and releases it.
5. **Logs** every tool call in an append-only action log.

The rules (scales, thresholds, stage ranges, minimum evidence, banned words) live in the
template, not in code. A different school brings a different template, and the code is
unchanged.

## Target users

- **Teachers and school heads** (the approver): see where a learner's foundation skills sit
  against the stage standard, and decide what the report says before it goes anywhere.
- **Parents and guardians** (readers of released reports): get a sourced, plain-words account of
  the data, with the evidence behind each statement.
- **School leaders** (later, locked): counts of learners below, within and above the stage range,
  with no names. This view is built only after the safety tests pass. See Limitations.

## Architecture

Every part is a container, and they talk to each other over private Docker networks. Only the
Open WebUI port and the upload page port are published, on the local address by default. The
optional proxy puts both behind one port when you set it up on purpose.

![Lucent AI architecture: the foundation built first, and the full design with the optional parts marked](docs/img/architecture-full.svg)

The full picture, the smaller foundation built first, one request step by step and what each part
is trusted with are in [`ARCHITECTURE.md`](ARCHITECTURE.md).

- **The gateway** knows who is signed in, signs the approver token and calls the orchestrator. No calculation and no model code.
- **The orchestrator** (LangGraph) runs the sequence and the ask-and-resolve loop. It does no calculation.
- **n8n** is running beside the agent but has no workflow yet. It is kept for seasonal automations outside the agent (see Future improvements) and has no access to learner data.
- **The model** plans, calls tools and writes the summary text. It never produces a number that
  a tool did not return.
- **The Lucent AI server** does every calculation, check and release, and makes no model calls.
- **Postgres** holds records, parsed template rules, a pgvector index of clause text, and the
  action log.

Details: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/SPEC.md`](docs/SPEC.md) and [`docs/CONTAINERS.md`](docs/CONTAINERS.md) (containers, networks, ports). Decisions and their reasons: [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Setup/installation instructions

You need a server or computer with Docker (with the Compose plugin), `make`, `python3` and `git`, and
your own OpenRouter key. **No key or secret is ever committed.** Everything runs in containers.

```bash
git clone https://github.com/Sedoid/MDEIS && cd MDEIS
make env-init            # makes .env with random secrets; then edit the lines marked for you
make dev-config          # checks that every variable is set
make dev-up              # downloads the embedding model, builds and starts everything
make demo-data           # loads the rulebook, the methodology and the practice report cards
```

Then open the chat at `http://127.0.0.1:3000` and sign in with the administrator account named in `.env`.
The upload page is at `http://127.0.0.1:8080` (or under `/portal/` on the public proxy). It uses the same
sign-in as the chat, so sign in to the chat's address and open the page on the same host name.
`make demo` is meant to do all of this in one command and to check each step; **it is Planned**
and I will not call it done until it has passed from a fresh clone with only `.env` edited. `make clean` removes the containers and the data volumes. `make help` lists every command.

The values you provide are the ones `make env-init` marks for you: your OpenRouter key and the
open-weights model name (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and optionally
`OPENROUTER_PROVIDER`), and the administrator's email and password for Open WebUI.
`.env.example` is the source of truth and has a comment for every variable.

For development:

```bash
make test        # unit and acceptance tests, in a container
make test-contract   # only the tool contract tests
make lint        # ruff
make check       # structure check and secret scan
make dev-health  # proves the wiring and the safety properties of the running stack
```

## Usage instructions

This is the walkthrough, and it is also the demo script. I have run these steps on my server
with the practice school and a teacher's made-up cards; the evaluation results are in
[`EVALS.md`](EVALS.md).

**1. Put the records in (the upload page).** Sign in, upload the school's methodology (it waits for a
named person to check it with `make ack`, then shows as in force), then upload report cards: files, or a
whole folder. They are read by ordinary code, not by the model. The Files list groups them by class and
school year, shows any that could not be read and why, and every file name opens the file in a new tab.

**2. Ask in the chat.** Ask by the learner's name, by the learner's number (for example `L-0111`), or by the
ID number printed on the report card. If two learners share a name, the agent asks which one you mean.

- *"What is learner OCC/2024/0211's English trend like?"* The agent reads the records, works out the trend
  with code, and answers in plain words. Under the answer it names the report-card files it used and the
  rules it applied, in the rulebook's own words.
- *"Show a chart of English for that learner"*, or *"Show Mathematics, Chemistry and Physics together"*.
  The chart is drawn from stored scores only, with its legend inside the picture.
- If the records have a problem that blocks a subject (a missing term, a duplicate, a mark above the
  maximum), the agent asks you in chat and waits. Answer in your own words; it records your answer with
  `resolve_issue`. It does not guess.
- *"Write a report on that learner's Chemistry."* The agent drafts it with record and rule citations,
  and `check_citations` accepts or rejects it. A failed draft is fixed and checked again, a bounded number
  of times.
- You read the draft. Only when you ask for the release, in a separate message, does `release_report` run,
  with your name taken from the sign-in.
- *"Who is the best in the class?"* or *"Which subject should she take?"* are refused with a reason.

**3. Check what happened.** Every tool call is in the action log, with the approver's name. In `make
dev-psql`: `select at, tool, outcome, approver_name from action_log order by event_id desc limit 20;`
and `select * from verify_action_log();` (no rows means no line was changed).

Swapping `templates/v1.yaml` for `templates/v2-strict.yaml` changes the results for the same data, with no
code change. The tool tests show it; that is the proof that rules live in the template.

## Technology stack

| Layer | Choice |
| --- | --- |
| Interface | Open WebUI, behind a small FastAPI gateway |
| Orchestration | LangGraph (Python, open source) runs the agent in its own `orchestrator` container, with a plain tool loop as the fallback. n8n (open source) runs beside it, with no workflow built yet |
| Second check (planned, not built) | A third-party model (Jev, through OpenRouter) that could only place a hold on a draft. The settings exist and are off; no code calls it. Everything the demo shows is checked by deterministic code alone |
| Model | A pinned open-weights model through OpenRouter (provider order fixed, fallbacks off, data collection denied). The model slug is pinned in the environment file |
| My MCP server | Python 3.12 and the official MCP Python SDK, Streamable HTTP |
| Borrowed MCP server | A read-only Postgres MCP server |
| Data | Postgres 16 with pgvector |
| Embeddings | llama.cpp server with an open embedding model |
| Packaging | Docker Compose, one container per part |
| Tracing (optional) | Langfuse |
| Quality | pytest, ruff, GitHub Actions |
| Deployment (bonus) | Docker Compose on my own server |

## Agent architecture

One agent loop, orchestrated by LangGraph, using tools. It is not a chatbot wrapper: the model cannot
produce a finding except through a tool call, and the gate sits on the only irreversible action.

- **Screen:** before any model call, the question is checked against the refusal rules.
- **Plan and call:** the model chooses which tools to call and in what order for the request, up
  to 12 model steps in one run. Independent lookups are asked for together. If it runs out of
  steps it stops and says so.
- **Ask and resolve:** a blocking data issue stops the affected subject. The agent asks the
  approver, then records the answer. It does not guess.
- **Draft and check:** the model writes the summary text; a deterministic checker accepts or
  rejects it. A draft that fails is written again up to twice, then the agent stops and says why.
- **Answer:** the final reply is put together by code from the tool results, not by the model:
  the chart, a caption, the report-card files the figures came from, the rules applied in the
  rulebook's own words, and any assumption made, such as a missing stage weight.
- **Second check:** the graph has a step reserved for a hold-only second-check model. It is a
  pass-through today.
- **Gate:** release needs a human approver taken from the signed-in session. The model cannot
  name or change the approver.
- **Refuse:** requests to rank, compare learners, put a judgement on a child or suggest a pathway are refused
  with a reason.

I built this project with the help of AI coding agents (Claude Code). The agent rules and task
plans are kept outside this repository. I review every change, and the tests and CI are the check
on the code.

## MCP implementation

The server is written in Python with the official MCP SDK and served over Streamable HTTP, which
Open WebUI supports natively. All tools return structured JSON. A refusal is a normal structured
result with reasons, not an exception. Every call is written to the action log with the run id
and the approver, if any. The approver is read from a verified, short-lived signed token
forwarded as a header, never from a tool argument.

Contracts for every tool: [`docs/SPEC.md`](docs/SPEC.md) section 5.

## MCP tools/servers

**Mine (the Lucent AI server). Built and run: 71 contract tests pass.**

| Tool | Kind | What it does |
| --- | --- | --- |
| `ingest_assessments` | read | Loads one learner's records, converts scales, returns typed issues |
| `resolve_issue` | **acting** | Records the approver's answer to one issue |
| `compute_trend` | read | Deterministic trend and gap to the stage standard, with citations and a `result_hash` |
| `get_criterion` | read | Finds the template clause to cite (pgvector search) |
| `draft_summary` | write | Stores the model's text as a pending report |
| `check_citations` | read | Rejects uncited, unsourced or banned-word text |
| `release_report` | **acting, the gate** | Releases a checked report. Needs a valid approver |
| `make_chart` | read | Prepares a progression chart of stored scores for one or several subjects, with the files and rules behind it. The system draws the picture, never the model |
| `stage_gap_counts` | read, **locked** | Counts only, no names, one school, groups below the template's minimum hidden. Written but off: not listed unless switched on by a named person |

**Borrowed.** [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) (MIT licence), in
its restricted read-only mode and connected with a database role that can only SELECT, so a write is
refused twice. The model may use four of its lookups (`list_schemas`, `list_objects`,
`get_object_details`, `execute_sql`). Numbers the model gets from it are not findings: the citation
check only accepts numbers that the school's own tools returned.

**Reused open-source software.** Nothing here is hidden: Open WebUI (the interface), Postgres and
pgvector, llama.cpp with the nomic-embed-text-v1.5 embedding model, n8n (automations beside the
agent), LangGraph (the agent graph), the official MCP Python SDK, FastAPI and uvicorn, psycopg,
pydantic, PyYAML, httpx, pdfplumber (reads the report cards), Caddy (the optional public proxy),
and for development pytest, ruff and fpdf2 (draws the practice report cards). The orchestrator
calls the model with plain HTTP and the MCP SDK; it does not use LangChain's model or MCP adapters.
The second-check model (Jev) named in the design is not part of this build, so no third-party
model is reused.

## Human-in-the-loop workflow

Three points need a person, each with the named person taken from the sign-in, never from the model:

1. **Checking the methodology.** A methodology that is uploaded waits until a named person has checked
   it and acknowledged it. Until then it is not in force.
2. **Resolving data problems.** The system does not repair records. A person answers, and the answer is
   recorded under their name.
3. **Releasing a report.** `release_report` refuses without a valid approver, or if `check_citations` has
   not passed. The approver reads the draft in chat and asks for the release in a separate message.
   Both allowed and refused attempts are logged.

Nothing reaches a parent or teacher outside this gate.

## Limitations

- **What was run, and how far.** See the status table and [`EVALS.md`](EVALS.md). The agent evals are
  one model, one provider and a few days; five runs of each scenario show the spread on that model only.
  One E-01 run reached its step limit once; it is recorded there, along with the change I made afterwards
  and the fact that five later runs are not proof.
- **The report-card reader is built for one layout.** Another school's card design needs a new reader.
  A teacher's cards in the same layout needed three fixes before all 50 read.
- **A stage or subject the rulebook does not know** ("Form 4 Science", Economics) is read, but a level
  against that stage's range is refused until a rulebook version names it with values the school confirms.
  A stage with no weight, where a window spans stages, is counted once and the answer says so.
- **Chat answers are not machine-checked the way drafts are.** A draft cannot pass without a real record or
  rule behind every sentence. A chat answer rests on the instructions, on numbers coming only from tools,
  and on the sources line under it.
- **Names are searchable, and that has a cost.** The assistant can find a learner by name. The name it finds
  is sent to the model's provider (the model is reached through OpenRouter), and so is any name a user
  types. That is acceptable only if the school accepts it, or runs its own model. File names of report cards
  appear in answers as sources, and in this school's files they carry the learner's name. A spelling that
  differs from the card's can miss; the agent then asks for the ID number.
- **Synthetic data only.** Real-world messiness, real handwriting and real school records are
  untested. The template values in `v1` are placeholders until a teacher confirms them.
- **Evidence is limited to records.** Marks and notes show attainment, not why. Lucent AI does not
  explain causes and does not decide anything about a learner.
- **Small-model risk.** An open-weights model can write a poor sentence. The citation checker
  catches missing sources and banned words, but not every poor phrasing, which is why a person
  approves.
- **Class view is locked.** The code exists but is off. It is switched on only after the safety
  tests pass and an approver is named. Counts only, no names, a minimum group size from the
  template, one school. A shown group can still point at one child ("below: 1"); that is a known
  weakness.
- **The diary is protected from edits, but not from its own owner.** The action log refuses
  UPDATE, DELETE and TRUNCATE three ways: the tools' database role has no such rights, a trigger
  refuses them, and each line is chained to the one before it, so `verify_action_log()` shows a
  changed line. That role also owns the tables, so it could switch the trigger off, and a full
  rewrite by it would not be detected. A call that is killed in the middle (a crash, a stopped
  container) leaves no line.
- **One school, one language** in the first version.

## Future improvements

- A separate owner role for the database tables, so the tools' role cannot change the diary's
  protections.
- Teacher-confirmed template values from real stage standards, and templates for other
  curricula.
- The locked class view, then school-level gap reporting for leaders.
- Local-language report text, checked by a human.
- Evidence from cases judged by teachers to measure how often the gap findings agree with
  teachers' own judgement.
- Parent-facing report layouts, and charts and PDF downloads served by the Lucent AI server after
  release.
- Import from common school record formats.
- **Seasonal automations through n8n, inside the approval gate.** None of this is built yet; n8n runs in
  the stack but has no workflow and no email. The idea is to remove routine work from the approver
  without removing the approver:
  - *Notices to the approver:* at the end of a term, an email with counts and a link to the upload
    page ("12 learners have enough evidence, 3 drafts wait for you, 2 problems in the records need
    your answer, a methodology waits for a check"). No finding about a child is in the email.
  - *Preparing term drafts:* a draft and a citation check for each learner with enough evidence,
    started by a named approver with one button, never under a made-up service identity. Drafts stay
    drafts.
  - *Delivery after release:* once the approver has released a report, n8n may send it to the
    approver or the teacher.
  The rule that holds: a report is released only by the signed-in approver, in their own message,
  and nothing about a learner leaves the system before that. Delivery to guardians is out of scope
  until consent and a human step before it are designed. Building this needs a mail service, a few
  read-only database views, and a decision on how a batch of drafts is started by a named person.

## Other

### Evaluation

Eleven tests in two layers, described in [`docs/TESTS.md`](docs/TESTS.md): a deterministic core
layer (the 71 contract tests) and an agent layer that runs each scenario five times and reports the
spread. The real runs are quoted in [`EVALS.md`](EVALS.md) and saved under `evals/results/`. Failures
are recorded as failures, and the failure I found is described there.

### Safety rules

- Numbers come only from deterministic code and SQL.
- Rules come from the template, with a clause ID on each.
- No judgement on a child: findings describe the data ("reading in the Form 1 range for three
  terms"), never "weak" or "gifted". No ranking or comparison between learners.
- No pathway, subject-choice or career suggestions.
- Synthetic data only.
- No secrets in the repo.

### Repository map

Full detail in [`docs/STRUCTURE.md`](docs/STRUCTURE.md).

```
src/mdeis/      MCP server (tools/) and deterministic core (core/)
templates/      criteria templates
src/orchestrator/   the LangGraph agent service
workflows/      n8n workflow exports (automations)
synthetic/      synthetic data generator    data/synthetic/  its output
tests/ evals/   deterministic tests, agent-layer evals and results
deploy/         service config and scripts
docs/           spec, containers, tests, template, decisions
```

### Licence

Apache License 2.0, an OSI-approved licence. See [`LICENSE`](LICENSE).

### Team

Up to three members, all 18 or over and resident in Africa, as the challenge requires. Names to
be added.
