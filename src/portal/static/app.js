"use strict";
// The upload page. Everything shown is put in with textContent, never as markup.

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

async function get(path) {
  const reply = await fetch(path, { credentials: "same-origin" });
  return { status: reply.status, body: await reply.json().catch(() => ({})) };
}

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

async function start() {
  config = (await get("api/config")).body;
  $("chat-link").href = config.chat_url;
  const who = await get("api/me");
  if (who.status !== 200) return showSignIn();
  me = who.body;
  $("signin").hidden = true;
  $("signout").hidden = false;
  $("who").textContent = "Signed in as " + me.name + (me.admin ? " (administrator)" : "");
  $("upload").hidden = false;
  $("status").hidden = false;
  $("chat").hidden = false;
  $("form-methodology").hidden = !me.admin;
  if (config.frame_chat) {
    $("chat-frame").src = config.chat_url;
    $("chat-frame").hidden = false;
  }
  csrf = (await get("api/csrf")).body.csrf;
  await refresh();
}

function showSignIn() {
  me = null;
  clearTimeout(timer);
  $("who").textContent = "Not signed in";
  $("signout").hidden = true;
  for (const id of ["upload", "status", "chat"]) $(id).hidden = true;
  $("chat-frame").removeAttribute("src");
  $("rows").replaceChildren();
  $("signin").hidden = false;
}

async function signIn(event) {
  event.preventDefault();
  const message = $("signin-message");
  message.textContent = "";
  const button = event.target.querySelector("button");
  button.disabled = true;
  const reply = await fetch("api/signin", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: $("email").value, password: $("password").value }),
  });
  const body = await reply.json().catch(() => ({}));
  $("password").value = "";
  button.disabled = false;
  if (!reply.ok) {
    message.textContent = body.error || "Sign-in did not work.";
    return;
  }
  await start();
}

async function signOut() {
  await fetch("api/signout", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf },
  });
  csrf = null;
  showSignIn();
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

async function review(doc, cell) {
  const full = await get("api/documents/" + encodeURIComponent(doc.document_id));
  const issues = (full.body.issues || []).map((issue) =>
    [issue.kind, issue.clause_id, issue.detail].filter(Boolean).join(": "));
  cell.appendChild(text("div", issues.join("\n") || "No changes listed.", "diff"));
  const command = "make ack DOC=" + doc.document_id;
  const copy = text("button", "Copy the acknowledge command", "plain");
  copy.type = "button";
  copy.addEventListener("click", () => {
    if (navigator.clipboard) navigator.clipboard.writeText(command);
    copy.textContent = "Copied: " + command;
  });
  cell.appendChild(copy);
}

async function refresh() {
  const { status, body } = await get("api/documents");
  if (status !== 200) return;
  const rows = $("rows");
  rows.replaceChildren();
  let busy = false;
  for (const doc of body.documents) {
    const row = document.createElement("tr");
    row.appendChild(text("td", doc.original_name));
    row.appendChild(text("td", doc.kind === "results" ? "Report card" : "Methodology"));
    row.appendChild(text("td", PLAIN[doc.status] || doc.status, "s-" + doc.status));
    const detail = text("td", describe(doc));
    row.appendChild(detail);
    rows.appendChild(row);
    busy = busy || doc.status === "pending" || doc.status === "processing";
    if (doc.status === "needs_review" && me.admin) review(doc, detail);
  }
  $("empty").hidden = body.documents.length > 0;
  clearTimeout(timer);
  if (busy) timer = setTimeout(refresh, 2000);
}

async function send(kind, file) {
  const reply = await fetch("api/documents?kind=" + kind, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf, "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
  const body = await reply.json().catch(() => ({}));
  return { ok: reply.ok, message: body.error || "" };
}

async function submit(event) {
  event.preventDefault();
  const form = event.target;
  const files = Array.from(form.elements.file.files);
  if (!files.length) return say("Choose a file first.", true);
  const button = form.querySelector("button");
  button.disabled = true;
  let failed = 0;
  for (const file of files) {
    const result = await send(form.dataset.kind, file);
    if (!result.ok) {
      failed += 1;
      say(file.name + ": " + (result.message || "not accepted."), true);
    }
  }
  if (!failed) say(files.length === 1 ? "Uploaded." : files.length + " files uploaded.", false);
  form.reset();
  button.disabled = false;
  await refresh();
}

document.addEventListener("DOMContentLoaded", () => {
  for (const form of document.querySelectorAll(".box")) form.addEventListener("submit", submit);
  $("signin-form").addEventListener("submit", signIn);
  $("signout").addEventListener("click", signOut);
  start();
});
