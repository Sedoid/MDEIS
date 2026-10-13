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
  document.title = "Sign in · MDEIS";
  $("shell").hidden = true;
  $("chat-frame").removeAttribute("src");
  $("rows").replaceChildren();
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
  const box = document.createElement("details");
  box.appendChild(text("summary", "Show the changes"));
  box.appendChild(text("div", issues.join("\n") || "No changes listed.", "diff"));
  const command = "make ack DOC=" + doc.document_id;
  const copy = text("button", "Copy the acknowledge command", "btn ghost");
  copy.type = "button";
  copy.addEventListener("click", () => {
    if (navigator.clipboard) navigator.clipboard.writeText(command);
    copy.textContent = "Copied: " + command;
  });
  box.appendChild(copy);
  cell.appendChild(box);
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
  const rows = $("rows");
  rows.replaceChildren();
  let busy = false;
  for (const doc of body.documents) {
    const row = document.createElement("tr");
    const cell = (label, content) => {
      const td = document.createElement("td");
      td.dataset.label = label;
      if (typeof content === "string") td.textContent = content;
      else td.appendChild(content);
      row.appendChild(td);
      return td;
    };
    cell("File", doc.original_name);
    cell("Kind", doc.kind === "results" ? "Report card" : "Methodology");
    cell("Status", text("span", PLAIN[doc.status] || doc.status, "pill s-" + doc.status));
    const detail = cell("Detail", describe(doc));
    rows.appendChild(row);
    busy = busy || doc.status === "pending" || doc.status === "processing";
    if (doc.status === "needs_review" && me.admin) review(doc, detail);
  }
  $("empty").hidden = body.documents.length > 0;
  document.querySelector("table").hidden = body.documents.length === 0;
  if (busy) timer = setTimeout(refresh, 2000);
}

async function send(kind, file) {
  const reply = await call("api/documents?kind=" + kind, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
  return { ok: reply.ok, offline: reply.status === 0, message: reply.body.error || "" };
}

async function submit(event) {
  event.preventDefault();
  const form = event.target;
  const files = Array.from(form.elements.file.files);
  if (!files.length) return say("Choose a file first.", true);
  const button = form.querySelector("button");
  button.disabled = true;
  let failed = 0;
  let offline = false;
  for (const file of files) {
    const result = await send(form.dataset.kind, file);
    if (!result.ok) {
      failed += 1;
      say(file.name + ": " + (result.message || "not accepted."), true);
      if (result.offline) {  // stop here and keep the chosen files, so trying again is one click
        offline = true;
        break;
      }
    }
  }
  if (!failed) say((files.length === 1 ? "Uploaded." : files.length + " files uploaded.") +
    " Progress is under Files.", false);
  if (!offline) {
    form.reset();
    form.querySelector(".picked").textContent = "";
  }
  button.disabled = false;
  await refresh();
}

function pickedNames(form) {
  const files = Array.from(form.elements.file.files);
  form.querySelector(".picked").textContent = files.length
    ? files.length + (files.length === 1 ? " file chosen: " : " files chosen: ") +
      files.map((file) => file.name).join(", ")
    : "";
}

function wireDrop(form) {
  const zone = form.querySelector(".drop");
  form.elements.file.addEventListener("change", () => pickedNames(form));
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
    form.elements.file.files = event.dataTransfer.files;
    pickedNames(form);
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
  start();
});
