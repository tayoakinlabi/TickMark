"use strict";

// The whole client. No framework and no build step: this page has one form and
// one table, and a toolchain to produce it would be a larger commitment than
// the thing it produces.
//
// Everything that comes back from the server ultimately came out of somebody's
// workbook — sheet names, formulas, cell text. It is all inserted with
// textContent, never innerHTML, so a sheet named after a <script> tag stays a
// sheet name. The report route escapes its own output for the same reason.

const SEVERITIES = ["high", "medium", "low", "info"];
const RECENT_KEY = "tickmark.recent";
const MAX_RECENT = 8;

const form = document.getElementById("audit-form");
const pathInput = document.getElementById("path");
const runButton = document.getElementById("run");
const statusBox = document.getElementById("status");
const warningsBox = document.getElementById("warnings");
const results = document.getElementById("results");
const template = document.getElementById("file-template");

function show(element, visible) {
  element.hidden = !visible;
}

function setStatus(message, kind) {
  statusBox.textContent = message;
  statusBox.className = "status" + (kind ? " " + kind : "");
  show(statusBox, Boolean(message));
}

// Remembering the last few paths is the one bit of state worth keeping, and it
// belongs to this browser alone. Wrapped because storage throws in a private
// window rather than returning null.
function recentPaths() {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return [];
  }
}

function rememberPath(value) {
  try {
    const kept = [value, ...recentPaths().filter((p) => p !== value)].slice(0, MAX_RECENT);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(kept));
  } catch (err) {
    /* a remembered path is a convenience, never a requirement */
  }
}

function renderRecent() {
  const paths = recentPaths();
  let list = document.getElementById("recent");
  if (!paths.length) {
    if (list) list.remove();
    return;
  }
  if (!list) {
    list = document.createElement("datalist");
    list.id = "recent";
    pathInput.setAttribute("list", "recent");
    document.body.appendChild(list);
  }
  list.replaceChildren();
  for (const path of paths) {
    const option = document.createElement("option");
    option.value = path;
    list.appendChild(option);
  }
}

function tile(severity, count) {
  const box = document.createElement("span");
  box.className = "tile " + severity + (count ? "" : " zero");
  const n = document.createElement("strong");
  n.textContent = String(count);
  const label = document.createElement("span");
  label.textContent = severity;
  box.append(n, label);
  return box;
}

function coverageLine(coverage) {
  if (!coverage || !coverage.total) return "";
  const refused = coverage.total - coverage.verified;
  let text =
    "Recomputed " + coverage.compared + " of " + coverage.total + " formulas.";
  if (refused > 0) {
    text +=
      " " +
      refused +
      " fell outside the evaluated subset and were not checked — not checked is not the same as correct.";
  }
  if (coverage.verified && !coverage.compared) {
    text += " This workbook stores no calculated values to compare against.";
  }
  return text;
}

function renderFile(file) {
  const node = template.content.cloneNode(true);
  const article = node.querySelector(".file");
  node.querySelector(".file-name").textContent = file.name;

  if (file.error) {
    article.classList.add("unreadable");
    const row = document.createElement("p");
    row.className = "error";
    row.textContent = "Could not read this file: " + file.error;
    article.querySelector(".findings").remove();
    article.querySelector(".report-link").remove();
    article.appendChild(row);
    return node;
  }

  const tiles = node.querySelector(".tiles");
  for (const severity of SEVERITIES) {
    tiles.appendChild(tile(severity, (file.counts && file.counts[severity]) || 0));
  }

  const link = node.querySelector(".report-link");
  if (file.report === null || file.report === undefined) {
    link.remove();
  } else {
    link.href = "/api/report/" + file.report;
  }

  node.querySelector(".coverage").textContent = coverageLine(file.coverage);

  const body = node.querySelector("tbody");
  if (!file.findings.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.className = "clean";
    cell.textContent = "No findings. Read the coverage line above before treating that as clean.";
    row.appendChild(cell);
    body.appendChild(row);
  }
  for (const finding of file.findings) {
    const row = document.createElement("tr");

    const severity = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + finding.severity;
    badge.textContent = finding.severity;
    severity.appendChild(badge);

    const where = document.createElement("td");
    where.className = "where";
    where.textContent = finding.location;

    const what = document.createElement("td");
    const summary = document.createElement("div");
    summary.textContent = finding.summary;
    what.appendChild(summary);
    if (finding.formula) {
      const code = document.createElement("code");
      code.textContent = finding.formula;
      what.appendChild(code);
    }
    if (finding.explanation) {
      const why = document.createElement("p");
      why.className = "why";
      why.textContent = finding.explanation;
      what.appendChild(why);
    }

    row.append(severity, where, what);
    body.appendChild(row);
  }
  return node;
}

function renderWarnings(warnings) {
  warningsBox.replaceChildren();
  if (!warnings || !warnings.length) {
    show(warningsBox, false);
    return;
  }
  const heading = document.createElement("strong");
  heading.textContent = "From your tickmark.toml:";
  const list = document.createElement("ul");
  for (const warning of warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    list.appendChild(item);
  }
  warningsBox.append(heading, list);
  show(warningsBox, true);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = pathInput.value.trim();
  if (!path) return;

  runButton.disabled = true;
  setStatus("Auditing… large workbooks take a few seconds each.", "working");
  results.replaceChildren();
  show(results, false);
  show(warningsBox, false);

  try {
    const response = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Same-origin only; the session cookie is SameSite=Strict and travels
      // with this automatically.
      credentials: "same-origin",
      body: JSON.stringify({
        path: path,
        recursive: document.getElementById("recursive").checked,
        evaluate: document.getElementById("evaluate").checked,
        include_integers: document.getElementById("include-integers").checked,
      }),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(payload.error || "That did not work.", "error");
      return;
    }

    rememberPath(path);
    renderRecent();
    renderWarnings(payload.warnings);

    const files = payload.files || [];
    const total = files.reduce(
      (sum, file) => sum + (file.findings ? file.findings.length : 0),
      0
    );
    const unreadable = files.filter((f) => f.error).length;
    let message =
      "Audited " + files.length + (files.length === 1 ? " workbook" : " workbooks") +
      ", " + total + (total === 1 ? " finding" : " findings") + ".";
    if (unreadable) {
      message += " " + unreadable + " could not be read.";
    }
    setStatus(message, total ? "found" : "clean");

    for (const file of files) {
      results.appendChild(renderFile(file));
    }
    show(results, true);
  } catch (err) {
    setStatus("Could not reach Tickmark. Has the window that started it been closed?", "error");
  } finally {
    runButton.disabled = false;
  }
});

renderRecent();
