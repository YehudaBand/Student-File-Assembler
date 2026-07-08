const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let roster = [];
let checklist = [];
let compulsorySections = [];
let naModalContext = null;
let lastRunResults = {};

function badge(text, kind) {
  return `<span class="badge badge-${kind}">${text}</span>`;
}

function renderConfig(cfg) {
  const el = $("#config");
  const driveOk = cfg.drive_configured ? "✓ configured" : "✗ GDRIVE_ROOT_FOLDER_ID missing";
  const uploadOk = cfg.upload_enabled ? "✓ upload enabled" : "✗ no Drive upload (set GDRIVE_OUTPUT_FOLDER_ID)";
  el.textContent = `drive=${driveOk}  •  ${uploadOk}  •  output=${cfg.output_dir}`;
}

function renderSections(sections) {
  const el = $("#ref-sections");
  el.innerHTML = `<table class="ref-table">
    <thead><tr><th>#</th><th>Section Key</th><th>Description</th><th>Sub-documents</th></tr></thead>
    <tbody>${sections.map((s) => {
      const subs = (s.sub_docs || []).map((d) => `<li>${d}</li>`).join("");
      const isCompulsory = compulsorySections.includes(s.key);
      const reqTag = isCompulsory ? ' <span class="tag tag-required">required</span>' : "";
      return `<tr>
        <td>${s.number}</td>
        <td><code>${s.key}</code>${reqTag}</td>
        <td>${s.name}</td>
        <td><ul class="ref-subdocs">${subs}</ul></td>
      </tr>`;
    }).join("")}</tbody>
  </table>`;
}

function renderNaCell(row) {
  const na = row.na_documents || [];
  if (!na.length) {
    return `<span class="muted-dash">—</span>
            <button class="na-edit" data-folder="${row.folder_name}">edit</button>`;
  }
  const chips = na.map((s) => `<span class="na-chip">${s}</span>`).join("");
  return `${chips}<button class="na-edit" data-folder="${row.folder_name}">edit</button>`;
}

function renderFolderCell(row) {
  const name = row.folder_name;
  if (row.drive_folder_link) {
    return `<a class="folder-link" href="${row.drive_folder_link}" target="_blank" title="Open student folder in Drive">${name} ↗</a>`;
  }
  return name;
}

function renderPackageCell(row) {
  const runResult = lastRunResults[row.folder_name];
  const parts = [];

  let outputBadge;
  let outputTitle = "";

  if (runResult && runResult.status === "ERROR") {
    outputBadge = badge("error", "error");
    outputTitle = (runResult.messages || []).join("; ");
  } else if (runResult && runResult.status === "INCOMPLETE") {
    outputBadge = badge("incomplete", "incomplete");
  } else if (runResult && runResult.status === "OK") {
    outputBadge = badge("ready", "ok");
  } else if (row.existing_output === "OK") {
    outputBadge = badge("ready", "ok");
  } else if (row.existing_output === "INCOMPLETE") {
    outputBadge = badge("incomplete", "incomplete");
  } else {
    outputBadge = badge("—", "none");
  }

  const driveLink = (runResult && runResult.drive_link) || row.drive_link;
  if (driveLink) {
    parts.push(`<a class="output-link drive-link" href="${driveLink}" target="_blank" title="Open package in Google Drive">${outputBadge} ↗</a>`);
  } else if (row.output_filename) {
    const href = `/output/${encodeURIComponent(row.output_filename)}`;
    parts.push(`<a class="output-link" href="${href}" target="_blank" title="${row.output_filename}">${outputBadge}</a>`);
  } else if (outputTitle) {
    parts.push(`<span title="${outputTitle.replace(/"/g, "&quot;")}">${outputBadge}</span>`);
  } else {
    parts.push(outputBadge);
  }

  return parts.join(" ");
}

function renderRosterRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.folder = row.folder_name;

  const sectionCount = row.section_count || 13;
  const presentSections = Object.keys(row.section_counts || {}).length;
  const coverageCell = `<span class="frac">${presentSections}/${sectionCount} sections</span>`;

  const lastName = row.last_name || `<span class="muted-dash">—</span>`;
  const starsId = row.stars_id || `<span class="muted-dash">—</span>`;

  tr.innerHTML = `
    <td><input type="checkbox" class="row-check" data-folder="${row.folder_name}" /></td>
    <td class="folder">${renderFolderCell(row)}</td>
    <td class="mono">${lastName}</td>
    <td class="mono">${starsId}</td>
    <td class="na">${renderNaCell(row)}</td>
    <td class="coverage-cell">${coverageCell}</td>
    <td class="package-cell">${renderPackageCell(row)}</td>
    <td><button class="row-delete" data-folder="${row.folder_name}" title="Remove saved settings">×</button></td>
  `;
  return tr;
}

function renderRoster(rows) {
  const tbody = $("#roster tbody");
  tbody.innerHTML = "";

  if (!rows.length) {
    $("#empty-msg").classList.remove("hidden");
    $("#roster").classList.add("hidden");
    $("#empty-msg").innerHTML = `No student folders found. Click <strong>Sync from Drive</strong> to scan files.`;
    return;
  }
  $("#empty-msg").classList.add("hidden");
  $("#roster").classList.remove("hidden");

  for (const row of rows) {
    tbody.appendChild(renderRosterRow(row));
  }
}

function selectedFolders() {
  return $$(".row-check:checked").map((c) => c.dataset.folder);
}

function updateRowForFolder(folderName) {
  const row = roster.find((r) => r.folder_name === folderName);
  if (!row) return;
  const tr = $(`#roster tbody tr[data-folder="${CSS.escape(folderName)}"]`);
  if (!tr) return;
  tr.querySelector(".package-cell").innerHTML = renderPackageCell(row);
}

function highlightActiveRow(current, doneFolders) {
  $$("#roster tbody tr").forEach((tr) => {
    tr.classList.remove("row-active", "row-done");
  });
  if (current) {
    const tr = $(`#roster tbody tr[data-folder="${CSS.escape(current)}"]`);
    if (tr) tr.classList.add("row-active");
  }
  for (const folder of doneFolders) {
    const tr = $(`#roster tbody tr[data-folder="${CSS.escape(folder)}"]`);
    if (tr && !tr.classList.contains("row-active")) tr.classList.add("row-done");
  }
}

function updateProgress(elFill, elLabel, done, total, current) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  elFill.style.width = `${pct}%`;
  let label = total > 0 ? `${done} / ${total}` : "…";
  if (current) label += ` — ${current}`;
  elLabel.textContent = label;
}

async function loadRoster() {
  const res = await fetch("/api/students");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#status-bar").textContent = `Roster error: ${err.error || res.statusText}`;
    return;
  }
  roster = await res.json();
  renderRoster(roster);
}

async function loadChecklist() {
  const res = await fetch("/api/checklist");
  checklist = await res.json();
  renderSections(checklist);
}

async function loadCompulsory() {
  const res = await fetch("/api/compulsory");
  compulsorySections = await res.json();
  if (checklist.length) renderSections(checklist);
}

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  renderConfig(cfg);
}

async function syncDrive() {
  $("#btn-sync").disabled = true;
  $("#status-bar").textContent = "Syncing from Drive...";
  $("#sync-progress").classList.remove("hidden");
  updateProgress($("#sync-progress-fill"), $("#sync-progress-label"), 0, 0, "Listing folders...");

  const res = await fetch("/api/sync", { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#status-bar").textContent = `Sync error: ${err.error || res.statusText}`;
    $("#btn-sync").disabled = false;
    $("#sync-progress").classList.add("hidden");
    return;
  }

  const data = await res.json();
  roster = data.students || [];
  renderRoster(roster);

  updateProgress($("#sync-progress-fill"), $("#sync-progress-label"), data.scanned || 0, data.total || 0, "");
  $("#btn-sync").disabled = false;
  setTimeout(() => $("#sync-progress").classList.add("hidden"), 1500);

  let msg = `Sync done: ${data.scanned || 0}/${data.total || 0} scanned`;
  if (data.added) msg += `, ${data.added} new`;
  if (data.errors && data.errors.length) {
    msg += `. Errors: ${data.errors.slice(0, 3).join(" | ")}`;
    if (data.errors.length > 3) msg += ` (+${data.errors.length - 3} more)`;
  }
  $("#status-bar").textContent = msg;
}

async function updateStudent(folder, patch) {
  const res = await fetch(`/api/students/${encodeURIComponent(folder)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#status-bar").textContent = `Update failed: ${err.error || res.statusText}`;
    return null;
  }
  return await res.json();
}

async function deleteStudent(folder) {
  if (!confirm(`Clear saved settings for ${folder}? (Folder remains in Drive.)`)) return;
  await fetch(`/api/students/${encodeURIComponent(folder)}`, { method: "DELETE" });
  loadRoster();
}

// -------- N/A modal --------

function openNaModal(folder) {
  const row = roster.find((r) => r.folder_name === folder);
  if (!row) return;
  naModalContext = { folder_name: folder, currentSet: new Set(row.na_documents || []) };
  $("#na-folder").textContent = folder;

  const container = $("#na-checklist");
  container.innerHTML = checklist.map((doc) => {
    const checked = naModalContext.currentSet.has(doc.key) ? "checked" : "";
    return `<label class="checklist-row">
      <input type="checkbox" data-key="${doc.key}" ${checked} />
      <span class="num">${doc.number}.</span>
      <code>${doc.key}</code>
      <span>${doc.name}</span>
    </label>`;
  }).join("");

  $("#na-modal").classList.remove("hidden");
}

async function saveNaModal() {
  const boxes = $$("#na-checklist input[type=checkbox]:checked");
  const na_documents = boxes.map((b) => b.dataset.key);
  const updated = await updateStudent(naModalContext.folder_name, { na_documents });
  if (updated) {
    $("#na-modal").classList.add("hidden");
    naModalContext = null;
    loadRoster();
  }
}

function closeNaModal() {
  $("#na-modal").classList.add("hidden");
  naModalContext = null;
}

// -------- Compulsory sections modal --------

function openCompulsoryModal() {
  const container = $("#compulsory-checklist");
  container.innerHTML = checklist.map((doc) => {
    const checked = compulsorySections.includes(doc.key) ? "checked" : "";
    return `<label class="checklist-row">
      <input type="checkbox" data-key="${doc.key}" ${checked} />
      <span class="num">${doc.number}.</span>
      <code>${doc.key}</code>
      <span>${doc.name}</span>
    </label>`;
  }).join("");
  $("#compulsory-modal").classList.remove("hidden");
}

async function saveCompulsoryModal() {
  const boxes = $$("#compulsory-checklist input[type=checkbox]:checked");
  const sections = boxes.map((b) => b.dataset.key);
  const res = await fetch("/api/compulsory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sections }),
  });
  if (res.ok) {
    compulsorySections = await res.json();
    renderSections(checklist);
    $("#compulsory-modal").classList.add("hidden");
    $("#status-bar").textContent = `Compulsory sections updated (${compulsorySections.length} selected).`;
  }
}

function closeCompulsoryModal() {
  $("#compulsory-modal").classList.add("hidden");
}

// -------- Pre-flight modal --------

function openPreflightModal() {
  $("#pf-modal").classList.remove("hidden");
  renderPreflight();
}

function closePreflightModal() {
  $("#pf-modal").classList.add("hidden");
}

function renderPreflight() {
  const totalStudents = roster.length;
  const header = `<div class="hint" style="margin-bottom:10px">
    <strong>${totalStudents}</strong> student(s) in roster.
    <strong>${compulsorySections.length}</strong> compulsory section(s).
  </div>`;

  if (!totalStudents) {
    $("#pf-body").innerHTML = header + `<div class="empty">Roster is empty.</div>`;
    return;
  }

  const body = roster.map((r) => {
    const sectionCount = r.section_count || 13;
    const presentSections = Object.keys(r.section_counts || {}).length;
    const statsLine = `${presentSections}/${sectionCount} sections with files`;

    return `<div class="pf-student">
      <div class="pf-student-header">
        <div class="pf-student-name">${r.folder_name}</div>
        <div class="pf-student-stats">${statsLine}</div>
      </div>
    </div>`;
  }).join("");

  $("#pf-body").innerHTML = header + body;
}

// -------- Run (one student per request) --------

async function runJob() {
  const students = selectedFolders();
  if (!students.length) {
    $("#status-bar").textContent = "Select at least one student.";
    return;
  }

  lastRunResults = {};
  const allLog = [];
  const allReportRows = [];
  const doneFolders = [];

  $("#btn-run").disabled = true;
  $("#log").textContent = "";
  $("#results").innerHTML = "";
  $("#run-progress").classList.remove("hidden");

  let okCount = 0;
  let incCount = 0;
  let errCount = 0;

  for (let i = 0; i < students.length; i++) {
    const folder = students[i];
    const n = i + 1;
    updateProgress($("#run-progress-fill"), $("#run-progress-label"), i, students.length, folder);
    $("#status-bar").textContent = `Running ${n}/${students.length}: ${folder}...`;
    highlightActiveRow(folder, doneFolders);

    const res = await fetch(`/api/run/${encodeURIComponent(folder)}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));

    if (data.log) {
      allLog.push(...data.log);
      $("#log").textContent = allLog.join("\n");
      $("#log").scrollTop = $("#log").scrollHeight;
    }

    lastRunResults[folder] = {
      status: data.status || "ERROR",
      messages: data.messages || [],
      output: data.output,
      drive_link: data.drive_link,
    };

    if (data.report_rows) allReportRows.push(...data.report_rows);

    if (data.status === "OK") okCount++;
    else if (data.status === "INCOMPLETE") incCount++;
    else errCount++;

    const row = roster.find((r) => r.folder_name === folder);
    if (row && data.drive_link) row.drive_link = data.drive_link;
    updateRowForFolder(folder);

    doneFolders.push(folder);
    updateProgress($("#run-progress-fill"), $("#run-progress-label"), n, students.length, folder);
  }

  highlightActiveRow("", doneFolders);
  $("#btn-run").disabled = false;
  setTimeout(() => $("#run-progress").classList.add("hidden"), 1500);

  $("#status-bar").textContent =
    `Done. OK: ${okCount}, incomplete: ${incCount}, errors: ${errCount}`;

  if (allReportRows.length) {
    await writeBatchReport(allReportRows);
  }
}

async function writeBatchReport(rows) {
  const res = await fetch("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!res.ok) return;
  const data = await res.json();
  renderResults(data.report_filename, data.report_drive_link);
}

function renderResults(reportFilename, reportDriveLink) {
  const el = $("#results");
  el.innerHTML = "";
  if (!reportFilename) return;
  const driveAnchor = reportDriveLink
    ? `<a href="${reportDriveLink}" target="_blank" class="drive-link">↗ Drive</a>`
    : "";
  const localAnchor = reportDriveLink
    ? `<span class="muted">${reportFilename}</span>`
    : `<a href="/output/${encodeURIComponent(reportFilename)}" target="_blank">${reportFilename}</a>`;
  const card = document.createElement("div");
  card.className = "result-card";
  card.innerHTML = `<div><strong>HCM2 report</strong></div>
    <div>${localAnchor} &nbsp;&nbsp;${driveAnchor}</div>`;
  el.appendChild(card);
}

document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  loadCompulsory();
  loadChecklist().then(loadRoster);

  const refToggle = $("#ref-toggle");
  const refBody = $("#ref-body");
  const refBtn = $("#ref-collapse-btn");
  refToggle.addEventListener("click", () => {
    const collapsed = refBody.classList.toggle("hidden");
    refBtn.textContent = collapsed ? "▶" : "▼";
  });

  $("#btn-sync").addEventListener("click", syncDrive);
  $("#btn-run").addEventListener("click", runJob);
  $("#btn-select-all").addEventListener("click", () => {
    $$(".row-check").forEach((c) => (c.checked = true));
  });
  $("#btn-select-none").addEventListener("click", () => {
    $$(".row-check").forEach((c) => (c.checked = false));
  });
  $("#btn-select-missing").addEventListener("click", () => {
    $$(".row-check").forEach((c) => {
      const folder = c.dataset.folder;
      const row = roster.find((r) => r.folder_name === folder);
      c.checked = !row || (!row.drive_link && row.existing_output === null);
    });
  });
  $("#chk-header").addEventListener("change", (e) => {
    $$(".row-check").forEach((c) => (c.checked = e.target.checked));
  });

  $("#roster tbody").addEventListener("click", (e) => {
    if (e.target.matches(".na-edit")) {
      openNaModal(e.target.dataset.folder);
    } else if (e.target.matches(".row-delete")) {
      deleteStudent(e.target.dataset.folder);
    }
  });

  $("#na-cancel").addEventListener("click", closeNaModal);
  $("#na-save").addEventListener("click", saveNaModal);
  $("#na-modal").addEventListener("click", (e) => {
    if (e.target.id === "na-modal") closeNaModal();
  });

  $("#btn-compulsory").addEventListener("click", openCompulsoryModal);
  $("#compulsory-cancel").addEventListener("click", closeCompulsoryModal);
  $("#compulsory-save").addEventListener("click", saveCompulsoryModal);
  $("#compulsory-modal").addEventListener("click", (e) => {
    if (e.target.id === "compulsory-modal") closeCompulsoryModal();
  });

  $("#btn-preflight").addEventListener("click", openPreflightModal);
  $("#pf-close").addEventListener("click", closePreflightModal);
  $("#pf-modal").addEventListener("click", (e) => {
    if (e.target.id === "pf-modal") closePreflightModal();
  });
});
