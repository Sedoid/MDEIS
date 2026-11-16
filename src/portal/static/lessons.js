"use strict";
// The Lessons page. Everything shown is put in with textContent, never as markup. It makes no
// request of its own: app.js hands it the page's one call() when the page is opened, and every post
// goes through postJson() below, which sends the CSRF token. The lists in the form, the changes, the
// evidence sentences and the wording of a refusal all come from the server. This file holds no
// stage, area, term, rule or number about a class.
//
// Two groups of code: the plain functions (`window.lessonsLogic`, which a test runs on their own),
// and the view (`window.lessonsView`, which app.js opens and resets).
(function () {
  const LESSONS = "api/lessons";
  const SUGGESTION_LABEL = "Suggestion, the tutor decides";
  const STATUS_WORDS = {
    proposed: "Waiting for your decision",
    approved: "Approved",
    rejected: "Rejected",
    superseded: "Replaced by a later plan",
  };
  const LIMIT_NOTE = "The wording check finds only the words and phrases your school's rulebook " +
    "lists. It cannot find every phrase that sounds like a judgement of a child. Read every " +
    "change before you approve.";

  // ---- plain functions -------------------------------------------------------------------------
  function suggestionText(edit) {
    const found = edit && edit.suggestion;
    if (typeof found === "string") return found;
    if (found && typeof found.text === "string") return found.text;
    return "";
  }

  // Approve is on only when the plan is still open, its checks have not failed, and at least one
  // of its own changes is accepted. The server checks all of this again; this only shows it.
  function approveState(plan, decisions) {
    const chosen = decisions || {};
    const ids = (plan.edits || []).map((edit) => edit.id).filter((id) => chosen[id] === "accepted");
    if (plan.plan_status !== "proposed") {
      return { enabled: false, ids: [], why: "This plan is no longer waiting for a decision." };
    }
    if (plan.checks_passed === false) {
      return { enabled: false, ids: [], why: "The checks did not pass, so this cannot be approved." };
    }
    if (!ids.length) {
      return { enabled: false, ids: [], why: "Accept at least one change to approve this plan." };
    }
    return { enabled: true, ids, why: "" };
  }

  // The text cut into pieces, each marked with the change it belongs to or with null. Offsets are
  // counted in whole characters (code points), as the server counts them. A piece that is out of
  // range or overlaps an earlier one is left unmarked, so the pieces always join back to the text.
  function segments(text, changes, side) {
    const chars = Array.from(text || "");
    const spans = (changes || [])
      .map((c) => (side === "original"
        ? [c.start, c.end, c.edit_id]
        : [c.new_start, c.new_end, c.edit_id]))
      .filter(([from, to]) => Number.isInteger(from) && Number.isInteger(to) && from >= 0)
      .sort((a, b) => a[0] - b[0]);
    const out = [];
    let at = 0;
    for (const [from, to, id] of spans) {
      if (from < at || to < from || to > chars.length) continue;
      if (from > at) out.push({ text: chars.slice(at, from).join(""), id: null });
      out.push({ text: chars.slice(from, to).join(""), id });
      at = to;
    }
    if (at < chars.length) out.push({ text: chars.slice(at).join(""), id: null });
    return out;
  }

  function createBody(values) {
    return {
      stage: values.stage,
      area: values.area,
      term: Number(values.term),
      objective: values.objective,
      notes: values.notes,
    };
  }

  function approveBody(ids) {
    return { accepted_edit_ids: ids.slice() };
  }

  function newestFirst(plans) {
    const when = (plan) => Date.parse(plan.created_at) || 0;
    return [...plans].sort((a, b) => {
      if (when(a) !== when(b)) return when(b) - when(a);
      return String(a.plan_id) < String(b.plan_id) ? 1 : -1;
    });
  }

  // A change a check turned away: its number, the check and the reason. Never its words.
  function droppedLine(item) {
    const checks = (item.lc || []).join(", ") || "a check";
    return "Change " + item.id + " was not shown. It did not pass " + checks + ": " + item.reason;
  }

  function noticeLine(notice) {
    return "Rule " + notice.clause_id + " lists a word that appears in your " + notice.where +
      ". No change was refused for it.";
  }

  const logic = { suggestionText, approveState, segments, createBody, approveBody, newestFirst,
    droppedLine, noticeLine };

  // ---- the view --------------------------------------------------------------------------------
  let api = null;       // { call, text, csrf, me }, given by app.js each time the page is opened
  let options = null;   // the form's lists, from the server
  let current = null;   // the plan on screen
  let decisions = {};   // edit id -> "accepted" or "rejected"; kept here until the plan is approved
  let wired = false;
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
    return body.detail || body.statement || body.error || "That did not work. Nothing was changed.";
  }

  function say(id, message, bad) {
    const line = el(id);
    line.textContent = message;
    line.className = bad ? "message bad" : "message";
  }

  // ---- the home page: the form and the saved plans ----------------------------------------------
  function fillSelect(select, items) {
    select.replaceChildren();
    for (const item of items) {
      const option = node("option", item.label);
      option.value = item.value;
      select.appendChild(option);
    }
  }

  function fillAreas() {
    const stage = (options.stages || []).find((s) => s.stage === el("lesson-stage").value);
    const areas = stage ? stage.areas : [];
    fillSelect(el("lesson-area"), areas.map((a) => ({
      value: a.area, label: a.foundation ? a.area + " (foundation)" : a.area,
    })));
  }

  function fillForm() {
    el("lesson-school").textContent = options.school || "not set";
    fillSelect(el("lesson-stage"), (options.stages || []).map((s) => ({ value: s.stage, label: s.stage })));
    fillSelect(el("lesson-term"), (options.terms || []).map((t) => ({ value: String(t), label: "Term " + t })));
    fillAreas();
    el("lesson-limit").textContent = options.max_notes_chars
      ? "Notes may be up to " + options.max_notes_chars + " characters. Rulebook: " +
        options.template_version + "."
      : "";
  }

  async function loadOptions() {
    const reply = await getJson(LESSONS + "/options");
    if (!api) return;  // signed out while waiting
    if (!reply.ok) {
      say("lesson-message", words(reply), true);
      el("lesson-submit").disabled = true;
      return;
    }
    options = reply.body;
    el("lesson-submit").disabled = false;
    say("lesson-message", "", false);
    fillForm();
  }

  function planRow(plan) {
    const row = node("button", null, "lesson-row-button");
    row.type = "button";
    row.appendChild(node("strong", plan.plan_id));
    const parts = [plan.stage, plan.area, plan.term ? "term " + plan.term : null,
      STATUS_WORDS[plan.plan_status || plan.status] || plan.plan_status || plan.status]
      .filter(Boolean);
    row.appendChild(node("span", " " + parts.join(" · "), "hint"));
    row.addEventListener("click", () => openPlan(plan.plan_id));
    return row;
  }

  async function loadList() {
    const box = el("lesson-list");
    const reply = await getJson(LESSONS);
    if (!api) return;
    box.replaceChildren();
    if (!reply.ok) {
      box.appendChild(node("p", reply.status === 501
        ? "The list of saved plans is not available yet."
        : words(reply), "hint"));
      return;
    }
    const plans = Array.isArray(reply.body.plans) ? reply.body.plans : [];
    if (!plans.length) {
      box.appendChild(node("p", "No saved plans yet.", "hint"));
      return;
    }
    for (const plan of newestFirst(plans)) box.appendChild(planRow(plan));
  }

  function showHome() {
    current = null;
    decisions = {};
    el("lesson-review").hidden = true;
    el("lesson-review").replaceChildren();
    el("lessons-home").hidden = false;
  }

  async function submitLesson(event) {
    event.preventDefault();
    if (working || !options) return;
    working = true;
    const submit = el("lesson-submit");
    submit.disabled = true;
    el("lesson-outcome").replaceChildren();
    say("lesson-message", "Working on it. This can take a minute.", false);
    const reply = await postJson(LESSONS, createBody({
      stage: el("lesson-stage").value,
      area: el("lesson-area").value,
      term: el("lesson-term").value,
      objective: el("lesson-objective").value,
      notes: el("lesson-notes").value,
    }));
    working = false;
    submit.disabled = false;
    if (!api) return;
    const made = reply.body && reply.body.outcome === "stored" && reply.body.plan;
    if (reply.ok && made && reply.body.plan.plan_id) {
      say("lesson-message", "", false);
      await openPlan(reply.body.plan.plan_id);
      loadList();
      return;
    }
    say("lesson-message", words(reply), true);
    const out = el("lesson-outcome");
    const list = node("ul", null, "lesson-notes-list");
    for (const item of (reply.body && reply.body.dropped) || []) list.appendChild(node("li", droppedLine(item)));
    for (const line of (reply.body && reply.body.reasons) || []) {
      if (typeof line === "string") list.appendChild(node("li", line));
    }
    if (list.children.length) out.appendChild(list);
  }

  // ---- the review page --------------------------------------------------------------------------
  async function openPlan(planId) {
    const reply = await getJson(LESSONS + "/" + encodeURIComponent(planId));
    if (!api) return;
    if (!reply.ok || !reply.body.plan) {
      say("lesson-message", words(reply), true);
      return;
    }
    current = reply.body.plan;
    decisions = {};
    const taken = current.accepted_edit_ids || [];
    for (const edit of current.edits || []) {
      if (current.plan_status === "approved") decisions[edit.id] = taken.includes(edit.id) ? "accepted" : "rejected";
    }
    renderReview();
  }

  function column(title, text, changes, side, decided) {
    const box = node("div", null, "lesson-col");
    box.appendChild(node("h3", title));
    const body = node("div", null, "lesson-text");
    for (const piece of segments(text, changes, side)) {
      if (piece.id === null) {
        body.appendChild(document.createTextNode(piece.text));
      } else {
        const marked = node("mark", piece.text, "lesson-change");
        if (decided && decided[piece.id] === "rejected") marked.className += " left-out";
        marked.dataset.edit = piece.id;
        marked.title = "Change " + piece.id;
        body.appendChild(marked);
      }
    }
    box.appendChild(body);
    return box;
  }

  function evidenceBlock(edit) {
    const box = node("div", null, "lesson-evidence");
    box.appendChild(node("h4", "Evidence"));
    const lines = Array.isArray(edit.evidence) ? edit.evidence : [];
    for (const line of lines) box.appendChild(node("p", line, "lesson-pre"));
    return box;
  }

  function card(edit, open) {
    const box = node("article", null, "lesson-card");
    box.dataset.edit = edit.id;
    box.appendChild(node("h3", "Change " + edit.id + " · " + String(edit.type || "").replace(/_/g, " ")));
    const pair = node("div", null, "lesson-pair");
    const was = node("div");
    was.appendChild(node("h4", "Original"));
    was.appendChild(node("p", edit.anchor_quote, "lesson-pre"));
    const now = node("div");
    now.appendChild(node("h4", "Replacement"));
    now.appendChild(node("p", edit.replacement, "lesson-pre"));
    pair.appendChild(was);
    pair.appendChild(now);
    box.appendChild(pair);
    box.appendChild(evidenceBlock(edit));
    const idea = node("div", null, "lesson-suggestion");
    idea.appendChild(node("h4", SUGGESTION_LABEL));
    idea.appendChild(node("p", suggestionText(edit), "lesson-pre"));
    box.appendChild(idea);
    const bar = node("div", null, "lesson-actions");
    if (open) {
      const accept = button("Accept", "ghost compact", () => decide(edit.id, "accepted"));
      const reject = button("Reject", "ghost compact", () => decide(edit.id, "rejected"));
      accept.dataset.choice = "accepted";
      reject.dataset.choice = "rejected";
      bar.appendChild(accept);
      bar.appendChild(reject);
    } else {
      bar.appendChild(node("span", decisions[edit.id] === "accepted" ? "Accepted" : "Left out", "pill"));
    }
    box.appendChild(bar);
    return box;
  }

  function decide(id, choice) {
    decisions[id] = decisions[id] === choice ? undefined : choice;
    paintDecisions();
  }

  // Colours the cards and the marked spans, and turns Approve on or off. No re-drawing: what the
  // tutor was reading stays where it is.
  function paintDecisions() {
    for (const box of document.querySelectorAll("#lesson-review .lesson-card")) {
      const chosen = decisions[box.dataset.edit];
      box.classList.toggle("is-accepted", chosen === "accepted");
      box.classList.toggle("is-rejected", chosen === "rejected");
      for (const choice of box.querySelectorAll("button[data-choice]")) {
        choice.setAttribute("aria-pressed", String(choice.dataset.choice === chosen));
      }
    }
    for (const mark of document.querySelectorAll("#lesson-review mark.lesson-change")) {
      mark.classList.toggle("left-out", decisions[mark.dataset.edit] === "rejected");
    }
    const state = approveState(current, decisions);
    const approve = el("lesson-approve");
    if (approve) {
      approve.disabled = !state.enabled;
      el("lesson-approve-why").textContent = state.enabled
        ? "Accepted: " + state.ids.length + " of " + current.edits.length + " changes."
        : state.why;
    }
  }

  async function finish(path, body) {
    el("lesson-approve").disabled = true;
    el("lesson-reject").disabled = true;
    const reply = await postJson(path, body);
    if (!api || !current) return;
    if (reply.ok) {
      await openPlan(current.plan_id);
      loadList();
      return;
    }
    say("lesson-review-message", words(reply), true);
    paintDecisions();
    el("lesson-reject").disabled = false;
  }

  function approveBar() {
    const bar = node("div", null, "lesson-approve box");
    bar.appendChild(node("p", "Approving as " + api.me.name + ". This name will be recorded as the " +
      "approver, and only you, the author, can approve this plan."));
    const approve = button("Approve this plan", "primary", () => {
      const state = approveState(current, decisions);
      if (state.enabled) {
        finish(LESSONS + "/" + encodeURIComponent(current.plan_id) + "/approve", approveBody(state.ids));
      }
    });
    approve.id = "lesson-approve";
    approve.disabled = true;
    const reject = button("Reject the whole plan", "ghost", () => {
      finish(LESSONS + "/" + encodeURIComponent(current.plan_id) + "/reject", {});
    });
    reject.id = "lesson-reject";
    const why = node("p", "", "hint");
    why.id = "lesson-approve-why";
    const line = node("p", "", "message");
    line.id = "lesson-review-message";
    line.setAttribute("role", "status");
    for (const part of [approve, reject, why, line]) bar.appendChild(part);
    return bar;
  }

  function closedNote(plan) {
    const box = node("div", null, "lesson-approve box");
    if (plan.plan_status === "approved") {
      const when = plan.approved_at ? new Date(plan.approved_at).toLocaleString() : "";
      box.appendChild(node("p", "Approved by " + plan.approver_name + (when ? " on " + when : "") +
        ". This plan can no longer be changed."));
    } else {
      box.appendChild(node("p", "This plan is " + (STATUS_WORDS[plan.plan_status] || plan.plan_status)
        .toLowerCase() + ". It can no longer be changed."));
    }
    const pins = [];
    const found = plan.pins || {};
    pins.push("Rulebook version: " + (found.template_version || plan.template_version));
    if (found.template_sha256) pins.push("Rulebook fingerprint: " + found.template_sha256);
    if (found.methodology_document_id) pins.push("Methodology: " + found.methodology_document_id);
    if (plan.model) pins.push("Written by model: " + plan.model);
    pins.push("Plan number: " + plan.plan_id + ". Every step is in the action log under this number.");
    for (const line of pins) box.appendChild(node("p", line, "hint"));
    return box;
  }

  function renderReview() {
    const plan = current;
    const open = plan.plan_status === "proposed";
    const view = el("lesson-review");
    view.replaceChildren();
    el("lessons-home").hidden = true;
    view.hidden = false;
    const top = node("div", null, "lesson-top");
    top.appendChild(button("Back to your lessons", "ghost compact", () => { showHome(); loadList(); }));
    view.appendChild(top);
    view.appendChild(node("h2", plan.stage + " · " + plan.area + " · term " + plan.term + " · " +
      (STATUS_WORDS[plan.plan_status] || plan.plan_status)));
    view.appendChild(node("p", "Objective (locked, never changed): " + plan.objective, "hint"));
    for (const item of plan.dropped || []) view.appendChild(node("p", droppedLine(item), "lesson-notice"));
    for (const item of plan.notices || []) view.appendChild(node("p", noticeLine(item), "lesson-notice"));
    view.appendChild(node("p", LIMIT_NOTE, "lesson-notice"));
    const cols = node("div", null, "lesson-cols");
    const changes = (plan.diff && plan.diff.changes) || [];
    cols.appendChild(column("Original", plan.original_text, changes, "original", decisions));
    const approved = plan.plan_status === "approved" && typeof plan.final_text === "string";
    if (approved) cols.appendChild(column("Approved version", plan.final_text, [], "revised", null));
    else cols.appendChild(column("Revised", plan.revised_text, changes, "revised", decisions));
    view.appendChild(cols);
    view.appendChild(node("h2", "Changes (" + (plan.edits || []).length + ")"));
    const cards = node("div", null, "lesson-cards");
    for (const edit of plan.edits || []) cards.appendChild(card(edit, open));
    view.appendChild(cards);
    view.appendChild(open ? approveBar() : closedNote(plan));
    if (open) paintDecisions();
    view.scrollIntoView();
  }

  // ---- what app.js calls -----------------------------------------------------------------------
  function show(given) {
    api = given;
    if (!wired) {
      el("lesson-form").addEventListener("submit", submitLesson);
      el("lesson-stage").addEventListener("change", () => { if (options) fillAreas(); });
      wired = true;
    }
    if (!options) loadOptions();
    if (!current) loadList();
  }

  function reset() {
    api = null;
    options = null;
    current = null;
    decisions = {};
    working = false;
    for (const id of ["lesson-list", "lesson-outcome", "lesson-review"]) {
      const area = el(id);
      if (area) area.replaceChildren();
    }
    for (const id of ["lesson-objective", "lesson-notes"]) {
      const field = el(id);
      if (field) field.value = "";
    }
    if (el("lesson-review")) el("lesson-review").hidden = true;
    if (el("lessons-home")) el("lessons-home").hidden = false;
    if (el("lesson-message")) el("lesson-message").textContent = "";
  }

  if (typeof window !== "undefined") {
    window.lessonsLogic = logic;
    window.lessonsView = { show, reset };
  }
})();
