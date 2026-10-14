"use strict";
// The portal page. Everything shown is put in with textContent, never as markup.

const $ = (id) => document.getElementById(id);
const PLAIN = {
  pending: "Waiting to be read",
  processing: "Being read",
  done: "Read",
  needs_review: "Read; a person must check it",
  failed: "Could not be read",
};
const FAILURES = {
  not_a_pdf: "This is not a PDF file.",
  unreadable_row: "A row of marks could not be read.",
  unreadable: "The file could not be read.",
  no_text: "The file has no readable text.",
  too_large: "The file is too large.",
};
let me = null;
let csrf = null;
let timer = null;
let config = {};
const VIEWS = ["upload", "files", "chat"];
const TITLES = { upload: "Upload", files: "Your files", chat: "Chat" };

const OFFLINE = "Could not reach the server. Check the connection and try again.";

// Every call to the portal goes through here. A dropped connection comes back as status 0 with a
// plain message, so no caller has to handle a thrown error.
async function call(path, options) {
  try {
    const reply = await fetch(path, { credentials: "same-origin", ...options });
    return { ok: reply.ok, status: reply.status, body: await reply.json().catch(() => ({})) };
  } catch (error) {
    return { ok: false, status: 0, body: { error: OFFLINE } };
  }
}

const get = (path) => call(path);

function text(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
}

function say(message, bad) {
  const line = $("message");
  line.textContent = message;
  line.className = bad ? "message bad" : "message";
}

function applyTheme(name) {
  if (name) document.documentElement.dataset.theme = name;
  else delete document.documentElement.dataset.theme;
}

function savedTheme() {
  try {
    return localStorage.getItem("mdeis-theme");
  } catch (error) {
    return null;
  }
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem("mdeis-theme", next);
  } catch (error) {
    // storage is blocked: the choice lasts until the page is closed
  }
}

// The sidebar can shrink to icons. The choice is kept per browser, and the page works without it.
function setSidebar(collapsed) {
  $("shell").classList.toggle("collapsed", collapsed);
  const label = collapsed ? "Expand the sidebar" : "Collapse the sidebar";
  $("collapse").setAttribute("aria-expanded", String(!collapsed));
  $("collapse").setAttribute("aria-label", label);
  $("collapse").title = label;
}

function toggleSidebar() {
  const collapsed = !$("shell").classList.contains("collapsed");
  setSidebar(collapsed);
  try {
    localStorage.setItem("mdeis-sidebar", collapsed ? "collapsed" : "open");
  } catch (error) {
    // storage is blocked: the choice lasts until the page is closed
  }
}

// Chat at full width hides the sidebar and the notice bar, and asks the browser for real full
// screen where it allows it. Esc leaves full screen and brings the layout back.
function setWide(on) {
  $("shell").classList.toggle("chat-wide", on);
  $("wide").setAttribute("aria-pressed", String(on));
  $("wide-label").textContent = on ? "Exit full screen" : "Full screen";
  if (on && document.fullscreenEnabled && !document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else if (!on && document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}

// One view at a time, chosen by the address (#/upload, #/files, #/chat), so Back and links work.
function route() {
  if (!me) return;
  const wanted = location.hash.replace("#/", "");
  const name = VIEWS.includes(wanted) ? wanted : "upload";
  for (const view of VIEWS) $("view-" + view).hidden = view !== name;
  for (const link of document.querySelectorAll(".nav a")) {
    if (link.dataset.view === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("main").classList.toggle("is-chat", name === "chat");
  if (name !== "chat" && $("shell").classList.contains("chat-wide")) setWide(false);
  document.title = TITLES[name] + " · MDEIS";
  $("main").scrollTop = 0;
}

async function start() {
  const settings = await get("api/config");
  if (settings.status === 0) {
    showSignIn();
    $("signin-message").textContent = OFFLINE;
    return;
  }
  config = settings.body;
  $("chat-link").href = config.chat_url;
  const who = await get("api/me");
  if (who.status !== 200) return showSignIn();
  me = who.body;
  $("signin").hidden = true;
  $("shell").hidden = false;
  $("signout").hidden = false;
  $("who-name").textContent = me.name;
  $("who-role").hidden = !me.admin;
  $("form-methodology").hidden = !me.admin;
  route();
  csrf = (await get("api/csrf")).body.csrf;
  await handOverLogin();
  if (config.frame_chat) {
    $("chat-frame").src = config.chat_url;
    $("chat-frame").hidden = false;
  }
  $("chat-fallback").hidden = Boolean(config.frame_chat);
  await refresh();
}

// Open WebUI keeps its own copy of the login in this browser's storage and trusts that over the
// cookie. The chat is on this same address, so the copy is put there before the chat is opened.
async function handOverLogin() {
  const reply = await call("api/chat-session", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  if (!reply.ok) return;
  try {
    if (reply.body.token) localStorage.setItem("token", reply.body.token);
  } catch (error) {
    // storage is blocked: the chat will ask for its own sign-in
  }
}

function showSignIn() {
  me = null;
  clearTimeout(timer);
  if ($("shell").classList.contains("chat-wide")) setWide(false);
  document.title = "Sign in · MDEIS";
  $("shell").hidden = true;
  $("chat-frame").removeAttribute("src");
  $("groups").replaceChildren();
  $("signin").hidden = false;
}

async function signIn(event) {
  event.preventDefault();
  const message = $("signin-message");
  message.textContent = "";
  const button = $("signin-button");
  button.disabled = true;
  button.textContent = "Signing in…";
  for (const id of ["email", "password"]) $(id).removeAttribute("aria-invalid");
  const reply = await call("api/signin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: $("email").value, password: $("password").value }),
  });
  if (reply.status !== 0) $("password").value = "";  // kept if the request never left, to retry
  button.disabled = false;
  button.textContent = "Sign in";
  if (!reply.ok) {
    message.textContent = reply.body.error || "Sign-in did not work.";
    for (const id of ["email", "password"]) $(id).setAttribute("aria-invalid", "true");
    $("password").focus();
    return;
  }
  await start();
}

async function signOut() {
  await call("api/signout", { method: "POST", headers: { "X-CSRF-Token": csrf } });
  csrf = null;
  try {
    localStorage.removeItem("token");
  } catch (error) {
    // nothing to remove
  }
  showSignIn();
}

const PARALLEL = 3; // uploads in flight at once
const ACCEPT = { results: [".pdf"], methodology: [".pdf", ".md"] };
const ACCEPT_WORDS = { results: "a PDF", methodology: "a PDF or Markdown file" };
const WHY_LEFT = { blocked: "not drawn", not_enough_evidence: "too few scores" };
const queues = new Map(); // form -> the files chosen for it
const stops = new Map(); // form -> a function that stops its running upload
const choices = new Map(); // group key -> open or closed, as the person left it

function yearShort(year) {
  const [first, second] = year.split("/");
  return second ? first + "/" + second.slice(-2) : year;
}

function when(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short",
    year: "numeric" }) : "";
}

// The file's name, as a link that opens the file itself in a new tab.
function fileLink(doc) {
  const link = document.createElement("a");
  link.href = "api/documents/" + encodeURIComponent(doc.document_id) + "/file";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = "Open in a new tab";
  link.textContent = doc.original_name;
  return link;
}

function describe(doc) {
  if (doc.status === "failed") {
    const where = doc.failure_page ? " (page " + doc.failure_page +
      (doc.failure_row ? ", row " + doc.failure_row : "") + ")" : "";
    return (FAILURES[doc.failure_kind] || "It could not be read.") + where;
  }
  if (doc.duplicate_of) return "The same file as " + doc.duplicate_of + ". Nothing was added.";
  if (doc.status === "needs_review") return "Changes are listed below.";
  return "";
}

function ackCommand(doc) {
  const name = (me.name || "").replace(/["\\$`]/g, "");
  return "make ack DOC=" + doc.document_id + ' BY="' + name + '" NOTE="checked"';
}

let methodStamp = 0; // a late answer for an older drawing of the panel is dropped

async function review(doc, container, stamp) {
  const full = await get("api/documents/" + encodeURIComponent(doc.document_id));
  if (stamp !== methodStamp) return;
  const issues = (full.body.issues || []).map((issue) =>
    [issue.kind, issue.clause_id, issue.detail].filter(Boolean).join(": "));
  const box = document.createElement("details");
  box.appendChild(text("summary", "Show the changes"));
  box.appendChild(text("div", issues.join("\n") || "No changes listed.", "diff"));
  const command = ackCommand(doc);
  const copy = text("button", "Copy the acknowledge command", "btn ghost");
  copy.type = "button";
  copy.addEventListener("click", () => {
    if (navigator.clipboard) navigator.clipboard.writeText(command);
    copy.textContent = "Copied: " + command;
  });
  box.appendChild(copy);
  container.appendChild(box);
}

// ---- the methodology panel: what is in force, what waits for a person, what came before --------
function renderMethod(state, documents) {
  const panel = $("method-panel");
  panel.replaceChildren();
  methodStamp += 1;
  const stamp = methodStamp;
  const reading = documents.filter((d) => d.kind === "methodology" &&
    (d.status === "pending" || d.status === "processing"));
  const line = (pill, doc, detail) => {
    const row = document.createElement("div");
    row.className = "method-row";
    row.appendChild(pill);
    const name = document.createElement("strong");
    name.appendChild(fileLink(doc));
    row.appendChild(name);
    if (detail) row.appendChild(text("span", detail, "method-meta"));
    return row;
  };
  const inForce = state && state.in_force;
  if (inForce) {
    const by = inForce.acknowledged_by ? "checked by " + inForce.acknowledged_by + " on " +
      when(inForce.acknowledged_at) : "in force";
    panel.appendChild(line(text("span", "In force", "pill s-done"), inForce,
      by + " · " + inForce.document_id));
    panel.appendChild(text("p", "Reports already written stay pinned to this one. After a " +
      "replacement they show as out of date; they are never rewritten.", "hint"));
  } else {
    panel.appendChild(text("p", "No methodology yet. Reports are written without one and say so.",
      "hint"));
  }
  for (const doc of reading) {
    panel.appendChild(line(text("span", PLAIN[doc.status], "pill s-" + doc.status), doc,
      doc.document_id));
  }
  for (const doc of (state && state.waiting) || []) {
    const row = line(text("span", "Waiting for a person to check it", "pill s-needs_review"),
      doc, doc.document_id);
    panel.appendChild(row);
    review(doc, panel, stamp);
  }
  const earlier = (state && state.earlier) || [];
  if (earlier.length) {
    const box = document.createElement("details");
    box.appendChild(text("summary", "Earlier uploads (" + earlier.length + ")"));
    for (const doc of earlier) {
      let note = "Replaced";
      if (doc.duplicate_of) note = "Same file as " + doc.duplicate_of + "; nothing was added";
      else if (doc.status === "failed") note = describe(doc);
      const item = document.createElement("p");
      item.className = "method-earlier";
      item.appendChild(fileLink(doc));
      item.appendChild(document.createTextNode(" · " + note));
      box.appendChild(item);
    }
    panel.appendChild(box);
  }
  $("method-drop-title").textContent = inForce
    ? "Choose a file to replace it, or drop it here"
    : "Choose a PDF or Markdown file or drop it here";
}

// ---- the files, grouped by class and school year -------------------------------------------------
function groupOf(doc) {
  if (doc.status === "pending" || doc.status === "processing") {
    return { key: "~reading", title: "Being read", rank: 0, open: true };
  }
  if (doc.status === "failed") {
    return { key: "~failed", title: "Could not be read", rank: 1, open: true };
  }
  if (doc.status === "needs_review") {
    return { key: "~review", title: "Needs checking", rank: 2, open: true };
  }
  if (doc.duplicate_of || !doc.stage) {
    return { key: "~same", title: "Same file uploaded again", rank: 9, open: false };
  }
  const year = doc.academic_year || "";
  return { key: doc.stage + "|" + year, title: doc.stage + " · " + yearShort(year), rank: 5,
    open: null, year, stage: doc.stage };
}

function byGroup(a, b) {
  if (a.rank !== b.rank) return a.rank - b.rank;
  if (a.year !== b.year) return a.year < b.year ? 1 : -1;
  return (a.stage || "").localeCompare(b.stage || "", undefined, { numeric: true });
}

function fileRow(doc) {
  const row = document.createElement("tr");
  const cell = (label, content) => {
    const td = document.createElement("td");
    td.dataset.label = label;
    if (typeof content === "string") td.textContent = content;
    else td.appendChild(content);
    row.appendChild(td);
  };
  cell("File", fileLink(doc));
  cell("Status", text("span", PLAIN[doc.status] || doc.status, "pill s-" + doc.status));
  cell("Detail", describe(doc));
  return row;
}

function groupBox(group, wanted) {
  const box = document.createElement("details");
  box.className = "group";
  box.open = choices.has(group.key) ? choices.get(group.key) : wanted;
  const read = group.docs.filter((d) => d.status === "done").length;
  const cards = group.docs.length + (group.docs.length === 1 ? " card" : " cards");
  const summary = document.createElement("summary");
  summary.appendChild(text("span", group.title, "g-title"));
  const all = group.rank !== 5 || read === group.docs.length;
  summary.appendChild(text("span", all ? cards : cards + ", " + read + " read", "g-count"));
  box.appendChild(summary);
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const name of ["File", "Status", "Detail"]) headRow.appendChild(text("th", name));
  head.appendChild(headRow);
  const body = document.createElement("tbody");
  const docs = [...group.docs].sort((a, b) =>
    a.original_name.localeCompare(b.original_name, undefined, { numeric: true }));
  for (const doc of docs) body.appendChild(fileRow(doc));
  table.appendChild(head);
  table.appendChild(body);
  wrap.appendChild(table);
  box.appendChild(wrap);
  box.addEventListener("toggle", () => {
    if (box.open === wanted) choices.delete(group.key);
    else choices.set(group.key, box.open);
  });
  return box;
}

function renderFiles(documents) {
  const cards = documents.filter((doc) => doc.kind === "results");
  const groups = new Map();
  for (const doc of cards) {
    const found = groupOf(doc);
    if (!groups.has(found.key)) groups.set(found.key, { ...found, docs: [] });
    groups.get(found.key).docs.push(doc);
  }
  const ordered = [...groups.values()].sort(byGroup);
  const few = ordered.filter((g) => g.rank === 5).length <= 3;
  const area = $("groups");
  area.replaceChildren();
  for (const group of ordered) area.appendChild(groupBox(group, group.open === null ? few : group.open));
  const count = (status) => cards.filter((d) => d.status === status).length;
  const waiting = count("pending") + count("processing");
  const parts = [cards.length + (cards.length === 1 ? " file" : " files"), count("done") + " read"];
  if (waiting) parts.push(waiting + " being read");
  if (count("failed")) parts.push(count("failed") + " could not be read");
  $("summary").textContent = cards.length ? parts.join(" · ") : "";
  $("empty").hidden = cards.length > 0;
}

async function refresh() {
  const { status, body } = await get("api/documents");
  clearTimeout(timer);
  if (status === 0) {  // the connection dropped: keep what is shown and try again shortly
    timer = setTimeout(refresh, 5000);
    return;
  }
  if (status === 401) {
    showSignIn();
    $("signin-message").textContent = "Your session ended. Sign in again.";
    return;
  }
  if (status !== 200) return;
  renderFiles(body.documents);
  let method = null;
  if (me.admin) {
    const reply = await get("api/methodology");
    if (reply.status === 200) method = reply.body;
  }
  if (me.admin) renderMethod(method, body.documents);
  const busy = body.documents.some((d) => d.status === "pending" || d.status === "processing");
  if (busy) timer = setTimeout(refresh, 2000);
}

// ---- uploading: many files, or a whole folder, a few at a time -----------------------------------
async function send(kind, file) {
  const reply = await call("api/documents?kind=" + kind, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
  return { ok: reply.ok, offline: reply.status === 0, message: reply.body.error || "" };
}

function chosen(kind, files) {
  const ok = [];
  const skipped = [];
  for (const file of files) {
    const name = file.name.toLowerCase();
    if (!name.startsWith(".") && ACCEPT[kind].some((end) => name.endsWith(end))) ok.push(file);
    else skipped.push(file);
  }
  const path = (file) => file.webkitRelativePath || file.name;
  ok.sort((a, b) => path(a).localeCompare(path(b), undefined, { numeric: true }));
  return { ok, skipped };
}

function setQueue(form, files) {
  const kind = form.dataset.kind;
  const { ok, skipped } = chosen(kind, Array.from(files));
  queues.set(form, ok);
  let words = ok.length ? ok.length + (ok.length === 1 ? " file chosen" : " files chosen") : "";
  if (ok.length && ok.length <= 3) words += ": " + ok.map((file) => file.name).join(", ");
  if (skipped.length) {
    const names = skipped.slice(0, 3).map((file) => file.name).join(", ") +
      (skipped.length > 3 ? " and " + (skipped.length - 3) + " more" : "");
    words += (words ? ". " : "") + "Skipped, not " + ACCEPT_WORDS[kind] + ": " + names + ".";
  }
  form.querySelector(".picked").textContent = words;
}

async function uploadAll(form, files) {
  const kind = form.dataset.kind;
  const box = form.querySelector(".progress");
  const bar = box.querySelector("progress");
  const label = box.querySelector(".progress-text");
  const problems = [];
  let finished = 0;
  let next = 0;
  let stopped = false;
  let offline = false;
  stops.set(form, () => { stopped = true; });
  box.hidden = false;
  bar.max = files.length;
  bar.value = 0;
  const show = () => {
    label.textContent = finished + " of " + files.length + " uploaded" +
      (problems.length ? " · " + problems.length + " not accepted" : "");
  };
  show();
  const lane = async () => {
    while (!stopped && next < files.length) {
      const file = files[next];
      next += 1;
      const result = await send(kind, file);
      if (!result.ok) {
        problems.push(file.name + ": " + (result.message || "not accepted."));
        if (result.offline) {  // stop here and keep the chosen files, so trying again is one click
          offline = true;
          stopped = true;
        }
      }
      finished += 1;
      bar.value = finished;
      show();
    }
  };
  await Promise.all(Array.from({ length: Math.min(PARALLEL, files.length) }, lane));
  stops.delete(form);
  return { finished, problems, offline, stopped, total: files.length };
}

async function submit(event) {
  event.preventDefault();
  const form = event.target;
  const files = queues.get(form) || [];
  if (!files.length) return say("Choose a file first.", true);
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  const done = await uploadAll(form, files);
  const bad = done.problems.length;
  let note = done.finished - bad + " of " + done.total + " uploaded.";
  if (done.stopped && !done.offline) note += " Stopped.";
  if (bad) {
    note += " " + done.problems.slice(0, 3).join(" ") +
      (bad > 3 ? " And " + (bad - 3) + " more." : "");
  } else if (!done.stopped) {
    note += " Progress is under Files.";
  }
  say(note, bad > 0 || done.stopped);
  if (!done.offline) {
    queues.delete(form);
    form.reset();
    form.querySelector(".picked").textContent = "";
  }
  button.disabled = false;
  await refresh();
}

function wireDrop(form) {
  const zone = form.querySelector(".drop");
  form.elements.file.addEventListener("change", () => setQueue(form, form.elements.file.files));
  const folder = form.querySelector(".folder");
  if (folder) {
    folder.addEventListener("change", () => setQueue(form, folder.files));
    form.querySelector(".pick-folder").addEventListener("click", () => folder.click());
  }
  const stop = form.querySelector(".stop");
  if (stop) stop.addEventListener("click", () => { const halt = stops.get(form); if (halt) halt(); });
  for (const name of ["dragenter", "dragover"]) {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("over");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    zone.addEventListener(name, () => zone.classList.remove("over"));
  }
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    setQueue(form, event.dataTransfer.files);
  });
}

function revealPassword() {
  const field = $("password");
  const show = field.type === "password";
  field.type = show ? "text" : "password";
  $("reveal").textContent = show ? "Hide" : "Show";
  $("reveal").setAttribute("aria-pressed", String(show));
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(savedTheme());
  for (const form of document.querySelectorAll(".box")) {
    form.addEventListener("submit", submit);
    wireDrop(form);
  }
  $("signin-form").addEventListener("submit", signIn);
  $("signout").addEventListener("click", signOut);
  $("theme").addEventListener("click", toggleTheme);
  $("reveal").addEventListener("click", revealPassword);
  window.addEventListener("hashchange", route);
  try {
    setSidebar(localStorage.getItem("mdeis-sidebar") === "collapsed");
  } catch (error) {
    setSidebar(false);
  }
  $("collapse").addEventListener("click", toggleSidebar);
  $("wide").addEventListener("click", () => setWide(!$("shell").classList.contains("chat-wide")));
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && $("shell").classList.contains("chat-wide")) setWide(false);
  });
  start();
});
