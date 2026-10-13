# Step 21: trend charts in the chat

Branch `step-21-charts`, from `step-20-portal-ui`. **Part 1 is the spike only.** Nothing here is wired
to real learner data yet, and nothing has been seen in Open WebUI v0.11.4 yet.

## The design (decided)

- A chart shows stored scores only. The model never draws it, never types a number on it and never
  sees the picture. The orchestrator will add it to the reply after the model's text, from a tool
  result, the way it already adds the saved draft.
- One learner, one subject or learning area, over the terms in the window. The shaded band is the
  stage's expected range (the area result has one; a subject has none, so a subject chart is the
  line alone). The top of the axis is the rulebook's scale, not a fixed 20. The legend is drawn inside
  the picture, so it travels with it when saved or printed.
- No chart for a blocked subject or "not enough evidence": the reply says why in words.
- No chart of one learner against another, or of a group.
- Format: SVG as a markdown image with a `data:` address. No script, no outside address, no new
  library. A PNG would need an image library (a new dependency, so it needs your approval).
- The gateway must strip these images from the conversation before it reaches the orchestrator.
  Open WebUI sends the whole history each turn, and a picture is 10 to 30 KB of text that would
  otherwise be paid for again every turn and could be quoted back.
- Anything the model writes that looks like an image or a data address is removed before the reply
  is shown (same idea as the token scrub).

## Part 1: what is built (the spike)

| Path | What |
| --- | --- |
| `src/mdeis/core/charts.py` | `trend_svg(chart)` draws the picture from numbers passed in; `markdown_image(svg, alt)` wraps it. Pure, deterministic, no database |
| `tests/unit/test_charts.py` | same input gives the same bytes; no script, no foreign element, no outside address; every printed number is one passed in; axis top is the passed scale; hostile text escaped; one point and no band still draw; markdown image round trip |
| `src/gateway/app.py` | **temporary**: with `CHART_SPIKE` set, the message `chart test` answers with one made-up sample chart |
| `deploy/compose/gateway.yml`, `.env.example` | the `CHART_SPIKE` switch (empty by default) |

Static checks run here (not a pytest run): the seven chart tests were called directly with Python and
all passed; the sample was drawn to a picture with macOS Quick Look and looked right (line, markers,
labelled values, shaded range with dashed edges, bracket for the gap, legend inside).

## Server run sheet (the spike)

```
git fetch && git checkout step-21-charts && git pull
make lint
make test ARGS="tests/unit/test_charts.py -q"
CHART_SPIKE=1 python3 scripts/set_env.py CHART_SPIKE
make dev-up
```

Then open the chat (`http://13.140.150.214:8088/` or the Chat view in the portal), sign in, start a
new chat and send exactly: `chart test`

Check and paste back (a screenshot is best):
1. Does a picture appear, or does the reply show a long `![Sample trend chart](data:image/...` line?
2. Light theme and dark theme: is the text readable, the legend whole, the picture not cut off?
3. Click the picture: does it enlarge? Can you save it?
4. Send a second message, `hello`, in the same chat. Does the chat still work? (It tests that the
   history with a picture in it is accepted.)
5. In the portal's Chat view (the frame), and on a phone width: does the picture fit?

To switch the spike off, set `CHART_SPIKE=` (empty) in `.env` and run `make dev-up`. The hook is
deleted in part 2.

## Spike result

Seen in Open WebUI v0.11.4 on the server (dark theme): the picture renders inline, the legend stays
whole, the text is readable, and the chat shows it in the normal reply. Layout then tidied: larger
fonts (title 21, values 14, legend 13.5), the gap bracket moved clear of the value label, more room
between the axis labels, the legend rows and the source line.
Not yet checked: the light theme, click-to-enlarge, a second message in the same chat (history with a
picture in it), and the portal's frame at phone width.

## Part 2: charts from real records (built, not yet run on the server)

**Approved by you:** a new tool, `make_chart` (the tool count goes from seven to eight). The contract
docs `SPEC.md` and `TESTS.md` need a section for it and for E-23; I cannot edit them. Three existing
tests were changed because the count changed, and each change is only the new name or number:
`TOOL_NAMES` in `tests/acceptance/test_tools_contract.py`, the `== 7` in
`tests/acceptance/test_class_view.py`, and the names of two unit tests. No test was weakened.

### What a teacher can ask for
- "Show Amina's Mathematics over the years": one line, every stored score, term by term.
- "Show Mathematics, Chemistry and Physics together": up to six lines on one axis, told apart by
  colour, marker shape and dash, each named at its end and in the legend.
- "Show literacy" (a learning area): one line with the expected range of each stage shaded, labelled
  with its clause, and the distance of the last score from the nearest edge of the range.
- One learner only. No chart of one learner against another, and none of a group.

### How it works
1. The model calls `make_chart(learner_id, skills[])`. The tool (`src/mdeis/tools/chart.py`,
   `src/mdeis/core/chartdata.py`) reads the same records and rulebook as `compute_trend` and returns
   the scores (two places, as the other tools give them), the records, the rules, the rulebook
   version and a `result_hash`. A subject with an unanswered problem, or with fewer scores than the
   rulebook asks for, is left off and listed in `excluded` with the reason. A term without a score is
   a gap in the line, never a zero. The result is small and goes to the diary like every call.
2. The model never receives a picture. After it has written its answer, the orchestrator
   (`src/orchestrator/chart_reply.py`) draws the picture from the tool result with plain code
   (`src/mdeis/chart_image.py`: no model, no script, no outside address, same input same bytes) and
   adds it under the model's words, with a caption: the records `[A-…]`, the rules `[TR-1]`, and any
   subject that was not drawn and why.
3. Anything image-like in the model's own words is removed, so the model cannot draw or link a picture.
4. Pictures in earlier turns are taken out of the conversation before it reaches the model (in the
   gateway and again in the orchestrator), so a picture is never sent again with later questions.
5. The scale and the ranges come from the rulebook, never from the drawing code. The spike hook
   (`chart test`) and the `CHART_SPIKE` switch are deleted. (Your `.env` still has `CHART_SPIKE=1`; it
   is no longer read. Remove the line if you like.)

### Files
| Path | What |
| --- | --- |
| `src/mdeis/core/chartdata.py` | what to draw, from records and rules (pure) |
| `src/mdeis/chart_image.py` | the drawing (pure). Outside `core/` because it holds layout numbers, not school numbers |
| `src/mdeis/tools/chart.py`, `server.py`, `tools/__init__.py` | the tool |
| `src/orchestrator/chart_reply.py`, `loop.py`, `graph.py`, `tools.py`, `prompts/system.md` | draw, attach, strip, allow-list, instructions |
| `src/gateway/app.py` | picture taken out of the history |
| `tests/unit/test_chartdata.py`, `test_charts.py`, `test_orchestrator_chart.py`, `test_gateway.py` | unit tests (written first for the data and the drawing) |
| `tests/acceptance/test_chart_tool.py` | the tool on the practice cohort: chart scores equal `compute_trend`'s, range equals its range, blocked subject left off with its question, same input same hash, refusals, diary line |

### Static checks run here (not a pytest run)
The chart data and chart drawing functions were called directly with Python against a stand-in
rulebook and all drawing tests passed; the orchestrator's `compose` and `strip_images` were called
directly and gave the expected text; all files compile. A twelve-term, three-subject chart and a
one-area chart with its range were drawn to a picture and looked right. The database and loop tests
were not run (they need the server).

### Server run sheet
```
git fetch && git checkout step-21-charts && git pull
make lint
make test ARGS="tests/unit -q"
make test-contract
make dev-up
```
Expect lint clean, the unit tests passing, and `make test-contract` passing with 8 tools. Paste the output.

Then in the chat (a learner with at least three terms, for example the one from your earlier runs):
1. "Show a chart of <learner>'s English over the terms." One line, scores listed under the picture.
2. "Show Mathematics, English and Chemistry together for <learner>." Three lines, a legend, names at the
   ends of the lines.
3. "Show a chart of literacy for <learner>." The shaded range and the distance bracket.
4. Ask for a subject with an open problem in the records: it should be left off, with the reason, and
   the others drawn.
5. Send a follow-up question in the same chat: it should answer normally and the picture should not
   be repeated or quoted.
Paste screenshots or the answers. Check the numbers on the picture against `make ask` or the
learner's card.

## Cut order if time runs short
The two-version comparison chart (not planned for the first cut), then the shaded range, then the
gap bracket. The table with the numbers is the floor.

## Not done
- A keyword check that forces a chart when the person writes "chart" (the model decides from its
  instructions; a miss is possible).
- Light-theme and phone-width checks of the picture; click-to-enlarge.
- The comparison of the same learner under two rulebook versions.
