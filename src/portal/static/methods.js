"use strict";
// The Methods page. Everything shown is put in with textContent, never as markup. It makes no request
// of its own: app.js hands it the page's one call() when the page is opened, and every post goes
// through postJson() below, which sends the CSRF token. Every number, quote, rule and sentence about
// a measure comes from the server. This file holds no stage, subject, scale, rule or number about a
// school, and it names no person in anything it sends: the server reads the signed-in teacher.
//
// A hold of the automated review is cleared here by a named signed-in person with a note: the block
// "Held by the automated review" (made by this file) asks the server, and approves nothing.
//
// Two groups of code: the plain functions (`window.methodsLogic`, which a test runs on their own),
// and the view (`window.methodsView`, which app.js opens, resets and asks for the waiting count).
(function () {
  const MEASURES = "api/measures";
  const HELD = "api/measures/held";
  const STATE_WORDS = {
    placeholder: "PLACEHOLDER: proposed from the methodology, not yet confirmed by a teacher",
    in_rulebook: "A rule of the school's rulebook",
    missing: "Missing: not found in the rulebook or in the proposed patch",
  };

  // ---- plain functions -------------------------------------------------------------------------
  function waitingText(count) {
    const n = Number(count) || 0;
    if (n === 0) return "Nothing is waiting for your review.";
    if (n === 1) return "1 measure is waiting for your review.";
    return n + " measures are waiting for your review.";
  }

  // The rows the teacher filled in, as the entries sent. A row with no score is left out. The term is
  // sent as a number. Nothing but the subject, the term and the score is taken from a row.
  function tryBody(stage, rows) {
    const entries = [];
    for (const row of rows || []) {
      const score = String(row.score == null ? "" : row.score).trim();
      if (score === "") continue;
      entries.push({ subject: row.subject, term: Number(row.term), score });
    }
    return { stage, entries };
  }

  function caseBody(stage, rows, expected, agreed) {
    const body = tryBody(stage, rows);
    const kept = {};
    for (const field of Object.keys(expected || {})) {
      const value = String(expected[field] == null ? "" : expected[field]).trim();
      if (value !== "") kept[field] = value;
    }
    body.expected = kept;
    body.agreed = Boolean(agreed);
    return body;
  }

  function noteBody(note) {
    const text = String(note == null ? "" : note).trim();
    return text ? { note: text } : {};
  }

  // Confirm is on only when the server says the registry would allow it and the version is still
  // waiting. The server checks all of this again; this only shows it.
  function confirmState(view) {
    if (!view || view.version_status !== "awaiting_demo") {
      return { enabled: false, why: "This version is not waiting for a decision." };
    }
    const approval = view.approval || {};
    if (!approval.can_confirm) {
      const gaps = (approval.gaps || []).map((gap) => gap.text).join(" ");
      return { enabled: false, why: gaps || "A confirmation is not allowed yet." };
    }
    return { enabled: true, why: "" };
  }

  function rejectState(view, note) {
    if (!view || view.version_status !== "awaiting_demo") {
      return { enabled: false, why: "This version is not waiting for a decision." };
    }
    if (!noteBody(note).note) {
      return { enabled: false, why: "Write why, then reject." };
    }
    return { enabled: true, why: "" };
  }

  // The versions the automated review holds. The review can only hold; a person clears a hold.
  function heldText(count) {
    const n = Number(count) || 0;
    if (n === 0) return "Nothing is held by the automated review.";
    if (n === 1) return "1 version is held by the automated review.";
    return n + " versions are held by the automated review.";
  }

  // A hold is cleared only with a note saying what was checked. The server checks this again.
  function clearHoldState(note) {
    if (!noteBody(note).note) {
      return { enabled: false, why: "Write what you checked, then clear the hold." };
    }
    return { enabled: true, why: "" };
  }

  function exampleWord(example) {
    if (example && example.match === true) return "Matches what the document says.";
    if (example && example.match === false) return "Does not match what the document says.";
    return "The example was not run here" + (example && example.problem ? " (" + example.problem + ")." : ".");
  }

  function stateWord(clause) {
    if (clause && clause.state === "confirmed") return "Confirmed by " + clause.confirmed_by;
    return STATE_WORDS[clause && clause.state] || "";
  }

  function answerLines(answer) {
    return Object.keys(answer || {}).sort().map((field) => {
      const shown = answer[field];
      return field + ": " + shown.value + " (exact " + shown.exact + ")";
    });
  }

  const logic = { waitingText, tryBody, caseBody, noteBody, confirmState, rejectState, exampleWord,
    stateWord, answerLines, heldText, clearHoldState };

  // ---- the view --------------------------------------------------------------------------------
  let api = null;       // { call, text, csrf, me }, given by app.js
  let current = null;   // the demo on screen
  let lastTry = null;   // { stage, rows, answer } of the last successful try
  let working = false;

  const el = (id) => document.getElementById(id);

  function node(tag, value, className) {
    const made = document.createElement(tag);
    if (value !== undefined && value !== null) made.textContent = value;
    if (className) made.className = className;
    return made;
  }

  function button(label, className, onClick) {
    const made = node("button", label, "btn " + className);
    made.type = "button";
    made.addEventListener("click", onClick);
    return made;
  }

  function getJson(path) {
    return api.call(path);
  }

  function postJson(path, body) {
    return api.call(path, {
      method: "POST",
      headers: { "X-CSRF-Token": api.csrf(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function words(reply) {
    const body = reply.body || {};
    return body.detail || body.error || "That did not work. Nothing was changed.";
  }

  function say(id, message, bad) {
    const line = el(id);
    if (!line) return;
    line.textContent = message;
    line.className = bad ? "message bad" : "message";
  }

  function block(title) {
    const box = node("div", null, "methods-block");
    box.appendChild(node("h2", title));
    return box;
  }

  function path(view, tail) {
    return MEASURES + "/" + encodeURIComponent(view.measure_id) + "/" + encodeURIComponent(view.version) +
      (tail ? "/" + tail : "");
  }

  // ---- the home page: what is waiting ----------------------------------------------------------
  function showCount(count) {
    const badge = el("methods-count");
    if (!badge) return;
    badge.textContent = String(count);
    badge.hidden = !(count > 0);
  }

  async function badge(given) {
    api = given;
    const reply = await getJson(MEASURES);
    if (!api) return;
    if (reply.ok && typeof reply.body.waiting === "number") showCount(reply.body.waiting);
    else showCount(0);  // not a registered teacher or a signed-in approver, or the page is off: nothing is shown
  }

  function listRow(item) {
    const row = node("button", null, "lesson-row-button");
    row.type = "button";
    row.appendChild(node("strong", item.measure_id + " version " + item.version));
    row.appendChild(node("span", " " + item.name + " · section " + item.method_ref, "hint"));
    row.addEventListener("click", () => openDemo(item.measure_id, item.version));
    return row;
  }

  // ---- the held block: versions the automated review holds, for a person to clear --------------
  function heldArea() {
    let area = el("methods-held");
    if (!area) {
      const list = el("methods-list");
      if (!list) return null;
      area = node("div", null, "methods-held");
      area.id = "methods-held";
      list.after(area);
    }
    return area;
  }

  async function loadHeld() {
    const area = heldArea();
    if (!area) return;
    const reply = await getJson(HELD);
    if (!api) return;
    area.replaceChildren();
    // not a signed-in approver, or the page is off: the block is not shown
    if (!reply.ok || !Array.isArray(reply.body.measures)) return;
    area.appendChild(heldBlock(reply.body.measures));
  }

  function heldBlock(items) {
    const box = block("Held by the automated review");
    box.appendChild(node("p", heldText(items.length), "summary"));
    box.appendChild(node("p", "The automated review can only hold; it never approves. Clearing a hold " +
      "approves nothing: the version then waits at the demo, where a teacher still tries it and " +
      "decides. Your name and your note are recorded.", "hint"));
    for (const item of items) box.appendChild(heldItem(item));
    return box;
  }

  function heldItem(item) {
    const card = node("div", null, "methods-example");
    card.appendChild(node("strong", item.measure_id + " version " + item.version));
    card.appendChild(node("span", " " + item.name + " · section " + item.method_ref, "hint"));
    card.appendChild(node("p", item.description));
    for (const reason of item.reasons || []) {
      card.appendChild(node("p", "Held because: " + reason, "lesson-notice"));
    }
    const note = node("textarea");
    note.rows = 2;
    note.setAttribute("aria-label", "Note");
    note.placeholder = "What you checked (needed)";
    const line = node("p", "", "message");
    line.setAttribute("role", "status");
    const clear = button("Clear the hold", "primary", () => {
      if (clearHoldState(note.value).enabled) clearHold(item, note.value, line);
    });
    clear.disabled = true;
    note.addEventListener("input", () => { clear.disabled = !clearHoldState(note.value).enabled; });
    const actions = node("div", null, "methods-actions");
    actions.appendChild(clear);
    for (const part of [note, actions, line]) card.appendChild(part);
    return card;
  }

  async function clearHold(item, note, line) {
    if (working) return;
    working = true;
    line.textContent = "Clearing the hold.";
    line.className = "message";
    const reply = await postJson(path(item, "clear-hold"), noteBody(note));
    working = false;
    if (!api) return;
    if (reply.ok && reply.body.status === "cleared") {
      say("methods-message", "Hold cleared on " + item.measure_id + " version " + item.version + " by " +
        reply.body.cleared_by_name + ". " + reply.body.next_step, false);
      loadList();
      return;
    }
    line.textContent = words(reply);
    line.className = "message bad";
  }

  async function loadList() {
    const box = el("methods-list");
    loadHeld();
    const reply = await getJson(MEASURES);
    if (!api) return;
    box.replaceChildren();
    if (!reply.ok) {
      el("methods-waiting").textContent = "";
      say("methods-message", words(reply), true);
      return;
    }
    say("methods-message", "", false);
    const found = Array.isArray(reply.body.measures) ? reply.body.measures : [];
    el("methods-waiting").textContent = waitingText(reply.body.waiting);
    showCount(found.length);
    for (const item of found) box.appendChild(listRow(item));
  }

  function showHome() {
    current = null;
    lastTry = null;
    el("methods-demo").hidden = true;
    el("methods-demo").replaceChildren();
    el("methods-home").hidden = false;
  }

  async function openDemo(measureId, version) {
    const reply = await getJson(MEASURES + "/" + encodeURIComponent(measureId) + "/" +
      encodeURIComponent(version));
    if (!api) return;
    if (!reply.ok || reply.body.status !== "ok") {
      say("methods-message", words(reply), true);
      return;
    }
    current = reply.body;
    lastTry = null;
    renderDemo();
  }

  // ---- the demo screen -------------------------------------------------------------------------
  function originBlock(view) {
    const box = block("Where it comes from");
    box.appendChild(node("p", "Methodology document " + view.origin.methodology_document_id +
      ", section " + view.method_ref + ". Section hash " + view.origin.section_sha256 + ".", "hint"));
    const quotes = view.origin.quotes || [];
    if (!quotes.length) {
      box.appendChild(node("p", "No rule was quoted from the methodology for this measure.", "hint"));
    }
    for (const quote of quotes) {
      box.appendChild(node("blockquote", quote.quote, "methods-quote"));
      const where = ["Document " + view.origin.methodology_document_id,
        quote.page ? "page " + quote.page : null,
        "section " + quote.section_id + (quote.title ? " " + quote.title : ""),
        quote.section_sha256 ? "hash " + quote.section_sha256 : null].filter(Boolean);
      box.appendChild(node("p", where.join(", "), "hint"));
    }
    return box;
  }

  function clauseBlock(view) {
    const box = block("Rules and values it reads");
    for (const clause of view.clauses || []) {
      const line = node("div", null, "methods-clause");
      line.appendChild(node("strong", clause.clause_id + " "));
      line.appendChild(node("span", stateWord(clause), "method-state is-" + clause.state));
      if (clause.text) line.appendChild(node("p", clause.text));
      if (clause.value !== null && clause.value !== undefined) {
        line.appendChild(node("p", "Value: " + clause.value + (clause.unit ? " " + clause.unit : "") +
          (clause.method_ref ? " (section " + clause.method_ref + ")" : ""), "hint"));
      }
      for (const read of clause.reads || []) {
        line.appendChild(node("p", "Read as " + read.key + " = " + read.value, "hint"));
      }
      box.appendChild(line);
    }
    if (view.patch) {
      box.appendChild(node("p", "Proposed values are in template patch " + view.patch.patch_id + " (" +
        view.patch.status + "). Confirming this measure confirms them. An administrator then applies the patch.", "hint"));
    }
    return box;
  }

  function skippedBlock(view) {
    const box = block("Sections the build skipped");
    const skipped = view.skipped || [];
    if (!skipped.length) box.appendChild(node("p", "No section was skipped.", "hint"));
    for (const item of skipped) {
      box.appendChild(node("p", "Section " + item.section_id + (item.title ? " " + item.title : "") +
        " was skipped: " + item.reason));
    }
    const anyway = view.build_anyway || [];
    box.appendChild(node("p", anyway.length
      ? "Built anyway, as recorded: " + anyway.join(", ") + "."
      : "No section was built anyway. To build a skipped section, ask for it when the build is run.", "hint"));
    return box;
  }

  function checksBlock(view) {
    const box = block("Checks");
    const review = view.review || {};
    box.appendChild(node("p", "Automated review: " + (review.result || "not recorded") +
      (review.cleared_by ? " (a hold was cleared by " + review.cleared_by + ")" : "") +
      ". It can only hold; it never approves."));
    for (const reason of review.reasons || []) box.appendChild(node("p", reason, "hint"));
    const checks = view.checks || {};
    const stat = checks.static || {};
    box.appendChild(node("p", "Static check: " + (stat.passed ? "passed" : "did not pass") + "."));
    for (const finding of stat.findings || []) box.appendChild(node("p", finding, "hint"));
    const tests = checks.tests || {};
    const sources = Object.keys(tests.by_source || {}).map((k) => k + " " + tests.by_source[k]).join(", ");
    box.appendChild(node("p", "Tests: " + (tests.passed ? "all passed" : "not all passed") + ", " + tests.ran +
      " run (" + sources + ")."));
    const sens = checks.sensitivity || {};
    if (sens.how) box.appendChild(node("p", "Sensitivity check: " + sens.how, "hint"));
    for (const note of view.notes || []) box.appendChild(node("p", note, "lesson-notice"));
    return box;
  }

  function exampleBlock(view) {
    const box = block("Worked examples");
    const examples = view.examples || [];
    if (!examples.length) box.appendChild(node("p", "The methodology gave no worked example for this measure.", "hint"));
    for (const example of examples) {
      const item = node("div", null, "methods-example");
      item.appendChild(node("strong", example.name));
      item.appendChild(node("p", "Scores given: " + example.inputs.join(", ")));
      item.appendChild(node("p", "The document says: " + example.quote, "methods-quote"));
      item.appendChild(node("p", "It should be: " + answerLines(example.expected).join("; ")));
      item.appendChild(node("p", "The function returned: " + (example.returned
        ? answerLines(example.returned).join("; ") : "nothing")));
      const word = example.match === true ? "methods-match is-yes" : example.match === false
        ? "methods-match is-no" : "methods-match";
      item.appendChild(node("p", exampleWord(example), word));
      box.appendChild(item);
    }
    for (const found of view.cases || []) {
      box.appendChild(node("p", "Your case " + found.name + ": scores " + found.inputs.join(", ") +
        ", expected " + answerLines(found.expected).join("; ") + (found.passed ? " (passed)." : "."), "hint"));
    }
    return box;
  }

  function option(select, value) {
    const made = node("option", value);
    made.value = value;
    select.appendChild(made);
  }

  function tryRow(form, index) {
    const row = node("div", null, "methods-row");
    const subject = node("select");
    subject.setAttribute("aria-label", "Subject");
    for (const name of form.subjects || []) option(subject, name);
    const term = node("input");
    term.type = "number";
    term.min = "1";
    term.value = String(index + 1);
    term.setAttribute("aria-label", "Term");
    const score = node("input");
    score.type = "text";
    score.setAttribute("aria-label", "Score");
    score.setAttribute("autocomplete", "off");
    score.placeholder = "Score";
    for (const part of [subject, term, score]) row.appendChild(part);
    row.read = () => ({ subject: subject.value, term: term.value, score: score.value });
    return row;
  }

  function tryBlock(view) {
    const form = view.try || {};
    const box = block("Try your own");
    box.appendChild(node("p", form.notice, "lesson-notice"));
    box.appendChild(node("p", "Scores are from 0 to " + form.scale_max + ". The function runs on the server, " +
      "in the sandbox. You see its exact answer and the values it used.", "hint"));
    const stage = node("select");
    stage.id = "methods-stage";
    stage.setAttribute("aria-label", "Stage");
    for (const name of form.stages || []) option(stage, name);
    box.appendChild(stage);
    const rows = node("div", null, "methods-rows");
    const made = [];
    const add = () => {
      const row = tryRow(form, made.length);
      made.push(row);
      rows.appendChild(row);
    };
    for (let i = 0; i < 3; i += 1) add();
    box.appendChild(rows);
    const result = node("div", null, "methods-result");
    result.id = "methods-result";
    const line = node("p", "", "message");
    line.id = "methods-try-message";
    line.setAttribute("role", "status");
    const actions = node("div", null, "methods-actions");
    actions.appendChild(button("Add a row", "ghost compact", add));
    actions.appendChild(button("Run on these scores", "primary", () => runTry(stage, made, result)));
    for (const part of [actions, line, result]) box.appendChild(part);
    return box;
  }

  async function runTry(stage, rowViews, result) {
    if (working || !current) return;
    working = true;
    result.replaceChildren();
    say("methods-try-message", "Running it.", false);
    const rows = rowViews.map((row) => row.read());
    const reply = await postJson(path(current, "try"), tryBody(stage.value, rows));
    working = false;
    if (!api || !current) return;
    if (!reply.ok || reply.body.status !== "ok") {
      say("methods-try-message", words(reply), true);
      return;
    }
    say("methods-try-message", "", false);
    lastTry = { stage: stage.value, rows, answer: reply.body.answer };
    showAnswer(reply.body, result);
  }

  function showAnswer(body, result) {
    result.appendChild(node("h3", "The function's answer"));
    for (const line of answerLines(body.answer)) result.appendChild(node("p", line));
    result.appendChild(node("h3", "Rules and values it used"));
    for (const clause of body.clauses || []) {
      result.appendChild(node("p", clause.clause_id + ": " + stateWord(clause), "hint"));
    }
    for (const key of Object.keys(body.values_used || {}).sort()) {
      result.appendChild(node("p", key + " = " + body.values_used[key], "hint"));
    }
    result.appendChild(caseForm(body));
  }

  function caseForm(body) {
    const box = node("div", null, "methods-case");
    box.appendChild(node("h3", "Your expected answer"));
    box.appendChild(node("p", "Type what you expected for these scores. It is stored only if the function " +
      "gives the same answer, and then it counts as a test from outside the build.", "hint"));
    const grid = node("div", null, "methods-expected");
    const inputs = {};
    for (const field of current.output_fields || []) {
      const label = node("label", field);
      const input = node("input");
      input.type = "text";
      input.setAttribute("autocomplete", "off");
      label.appendChild(input);
      grid.appendChild(label);
      inputs[field] = input;
    }
    box.appendChild(grid);
    let agreed = false;
    const line = node("p", "", "message");
    line.id = "methods-case-message";
    line.setAttribute("role", "status");
    const take = button("Use the answer shown", "ghost compact", () => {
      agreed = true;
      for (const field of Object.keys(inputs)) {
        const shown = body.answer[field];
        inputs[field].value = shown ? shown.exact : "";
      }
    });
    const save = button("Save as my expected answer", "primary", async () => {
      if (!lastTry || !current) return;
      const typed = {};
      for (const field of Object.keys(inputs)) typed[field] = inputs[field].value;
      const reply = await postJson(path(current, "case"),
        caseBody(lastTry.stage, lastTry.rows, typed, agreed));
      if (!api || !current) return;
      if (reply.ok && reply.body.status === "saved") {
        say("methods-case-message", "Saved as " + reply.body.name + ".", false);
        openDemo(current.measure_id, current.version);
        return;
      }
      const shown = reply.body && reply.body.answer ? " The function gave: " + answerLines(reply.body.answer).join("; ") + "." : "";
      say("methods-case-message", words(reply) + shown, true);
    });
    const actions = node("div", null, "methods-actions");
    actions.appendChild(take);
    actions.appendChild(save);
    for (const part of [actions, line]) box.appendChild(part);
    return box;
  }

  async function decide(action, note) {
    const reply = await postJson(path(current, action), noteBody(note));
    if (!api || !current) return;
    if (reply.ok) {
      const done = reply.body;
      showHome();
      say("methods-message", (done.status === "confirmed"
        ? "Confirmed by " + done.teacher_name + ". " + done.next_step
        : "Rejected. " + done.next_step), false);
      loadList();
      return;
    }
    say("methods-decision-message", words(reply), true);
    paintDecision();
  }

  function decisionBlock(view) {
    const box = block("Your decision");
    box.appendChild(node("p", "Confirming as " + api.me.name + ". This name will be recorded as the signed-in approver " +
      "(the teacher) who confirmed this measure and the values it reads. Only you can confirm or " +
      "reject as yourself."));
    const note = node("textarea");
    note.id = "methods-note";
    note.rows = 3;
    note.setAttribute("aria-label", "Note");
    note.placeholder = "A note (needed to reject)";
    box.appendChild(note);
    const gaps = node("div", null, "methods-gaps");
    gaps.id = "methods-gaps";
    const line = node("p", "", "message");
    line.id = "methods-decision-message";
    line.setAttribute("role", "status");
    const confirm = button("Confirm this measure", "primary", () => {
      if (confirmState(current).enabled) decide("confirm", note.value);
    });
    confirm.id = "methods-confirm";
    const reject = button("Reject this version", "ghost", () => {
      if (rejectState(current, note.value).enabled) decide("reject", note.value);
    });
    reject.id = "methods-reject";
    note.addEventListener("input", paintDecision);
    const actions = node("div", null, "methods-actions");
    actions.appendChild(confirm);
    actions.appendChild(reject);
    for (const part of [gaps, actions, line]) box.appendChild(part);
    return box;
  }

  function paintDecision() {
    const confirm = el("methods-confirm");
    if (!confirm || !current) return;
    const note = el("methods-note").value;
    const on = confirmState(current);
    confirm.disabled = !on.enabled;
    el("methods-reject").disabled = !rejectState(current, note).enabled;
    const gaps = el("methods-gaps");
    gaps.replaceChildren();
    if (!on.enabled) gaps.appendChild(node("p", on.why, "lesson-notice"));
  }

  function renderDemo() {
    const view = current;
    const screen = el("methods-demo");
    screen.replaceChildren();
    el("methods-home").hidden = true;
    screen.hidden = false;
    const top = node("div", null, "methods-top");
    top.appendChild(button("Back to the list", "ghost compact", () => { showHome(); loadList(); }));
    screen.appendChild(top);
    screen.appendChild(node("h2", view.name));
    screen.appendChild(node("p", view.measure_id + " version " + view.version + " · section " + view.method_ref, "hint"));
    screen.appendChild(node("p", view.description));
    for (const part of [originBlock(view), clauseBlock(view), skippedBlock(view), checksBlock(view),
      exampleBlock(view)]) screen.appendChild(part);
    if (view.version_status === "awaiting_demo") {
      screen.appendChild(tryBlock(view));
      screen.appendChild(decisionBlock(view));
      paintDecision();
    }
    screen.scrollIntoView();
  }

  // ---- what app.js calls -----------------------------------------------------------------------
  function show(given) {
    api = given;
    if (!current) loadList();
  }

  function reset() {
    api = null;
    current = null;
    lastTry = null;
    working = false;
    for (const id of ["methods-list", "methods-demo", "methods-held"]) {
      const area = el(id);
      if (area) area.replaceChildren();
    }
    if (el("methods-demo")) el("methods-demo").hidden = true;
    if (el("methods-home")) el("methods-home").hidden = false;
    if (el("methods-message")) el("methods-message").textContent = "";
    if (el("methods-waiting")) el("methods-waiting").textContent = "";
    showCount(0);
  }

  if (typeof window !== "undefined") {
    window.methodsLogic = logic;
    window.methodsView = { show, reset, badge };
  }
})();
