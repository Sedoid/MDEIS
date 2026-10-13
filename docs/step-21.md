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

## Part 2 (SVG renders; not started)

1. Tests first: the chart's numbers equal the `compute_trend` window for that run; blocked or thin
   data gives no chart and a stated reason; another learner's data is refused; the gateway strips
   images from the history; a model-written image is removed.
2. A read-only tool `make_chart(learner_id, skill, template_version)` in `mdeis-mcp`. It recomputes
   with the same functions as `compute_trend` (so its `result_hash` matches that call), reads the scale
   and ranges from the rulebook, and returns a chart id, the caption (record ids, clause ids) and the
   picture. Logged like every call. **This adds a tool: it needs your approval**, and the contract doc
   `SPEC.md` needs a section for it, which I cannot edit.
3. The orchestrator keeps the picture out of the model's context and appends it, with the caption, to
   the final reply; it asks for a chart only when the person asked for one (chart, graph, plot).
4. Fallback if SVG does not render: a markdown table with a text sparkline; or a mermaid chart.

## Cut order if time runs short
The two-version comparison chart (not planned for the first cut), then the shaded range, then the
gap bracket. The table with the numbers is the floor.
