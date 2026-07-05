// =====================================================================
// REVIEW ARTIFACT — NOT APP CODE.
// Interaction layer for the ENG-004 UI prototype gate (SES-004).
// Simulates the standards' behaviors with fake data; the real app
// implements them server-side per the data-platform standards.
// =====================================================================

/* eslint-disable */
"use strict";

// ---------------------------------------------------------------- state
const state = {
  rows: [...ENGAGEMENTS],          // triage-ordered fake "server" result
  filtered: [...ENGAGEMENTS],
  selected: new Set(),
  focusedIdx: 0,
  search: "",
  sort: [],                        // [{col, dir}] — multi-sort (REQ-025)
  searchHistory: ["pending", "zoom", "bakery"],
  multiMode: false,
  currentScreen: "engagements",
  prepSession: null,               // {engId, sessionId}
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html !== undefined) n.innerHTML = html; return n; };

// Default triage order (REQ-072 notes): pending acceptance → imminent
// next session → open action items → the rest.
function triageSort(rows) {
  return [...rows].sort((a, b) => {
    const r = (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9);
    if (r !== 0) return r;
    const an = a.nextSession || "9999", bn = b.nextSession || "9999";
    if (an !== bn) return an < bn ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}
state.rows = triageSort(state.rows);
state.filtered = [...state.rows];

// ------------------------------------------------------------- helpers
function fmtDate(d) {
  if (!d) return "—";
  const [date, time] = d.split(" ");
  const [y, m, day] = date.split("-");
  const mn = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1];
  return time ? `${mn} ${+day}, ${time}` : `${mn} ${+day}, ${y}`;
}
function chipClass(status) {
  return { "Pending Acceptance": "pending", "Assigned": "assigned", "Active": "active", "On Hold": "onhold", "Dormant": "dormant" }[status] || "dormant";
}
function toast(msg, ms = 3200) {
  document.querySelectorAll(".toast").forEach(t => t.remove());
  const t = el("div", "toast", msg);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
function statusMsg(msg, ms = 2200) {
  $("status-mid").textContent = msg;
  if (ms) setTimeout(() => { if ($("status-mid").textContent === msg) $("status-mid").textContent = ""; }, ms);
}

// Educate-voice dialog (SKL-111 #1): what happened → why → what next.
function educateDialog(title, bodyHtml, actions) {
  closeDialogs();
  const back = el("div", "dialog-backdrop");
  const d = el("div", "dialog");
  d.appendChild(el("h3", null, title));
  d.appendChild(el("div", "dialog-body", bodyHtml));
  const acts = el("div", "dialog-actions");
  (actions || [{ label: "OK" }]).forEach(a => {
    const b = el("button", "btn " + (a.style || "ghost"), a.label);
    b.onclick = () => { back.remove(); if (a.onClick) a.onClick(); };
    acts.appendChild(b);
  });
  d.appendChild(acts);
  back.appendChild(d);
  back.onclick = (e) => { if (e.target === back) back.remove(); };
  document.body.appendChild(back);
}
function closeDialogs() { document.querySelectorAll(".dialog-backdrop, .menu, .dropdown, .palette").forEach(n => n.remove()); }

// ------------------------------------------------------------ the grid
function applyFilters() {
  const q = state.search.toLowerCase();
  // Search scopes to DISPLAYED columns only (REQ-020) and narrows the
  // view's own filter (the view already filtered to the 5 statuses).
  state.filtered = state.rows.filter(r =>
    !q || [r.name, r.status, r.contact, r.email, fmtDate(r.lastSession), fmtDate(r.nextSession), String(r.totalSessions)]
      .some(v => v.toLowerCase().includes(q)));
  if (state.sort.length) {
    state.filtered.sort((a, b) => {
      for (const s of state.sort) {
        let av = a[s.col] ?? "", bv = b[s.col] ?? "";
        if (typeof av === "number" || typeof bv === "number") { av = +av || 0; bv = +bv || 0; }
        if (av < bv) return s.dir === "asc" ? -1 : 1;
        if (av > bv) return s.dir === "asc" ? 1 : -1;
      }
      return 0;
    });
  }
  renderGrid();
}

function renderGrid() {
  const tbody = $("eng-grid-body");
  tbody.innerHTML = "";
  const emptyBox = $("grid-empty");
  const table = $("eng-grid");

  if (!state.filtered.length) {
    table.style.display = "none";
    emptyBox.style.display = "block";
    // Filtered-to-zero educate state (REQ-030): never let a filter
    // masquerade as missing data.
    emptyBox.innerHTML = state.search
      ? `No rows match "<b>${state.search}</b>" — ${state.rows.length} engagements are hidden by this search.<br>
         <button class="btn ghost" onclick="clearSearch()">Clear search</button>`
      : `This view has no rows. <i>My Active Engagements</i> shows engagements with status
         Active, Pending Acceptance, Assigned, On Hold, or Dormant assigned to you.`;
  } else {
    table.style.display = "";
    emptyBox.style.display = "none";
  }

  state.filtered.forEach((r, i) => {
    const tr = el("tr", "grid-row" + (state.selected.has(r.id) ? " selected" : "") + (i === state.focusedIdx ? " focused" : ""));
    tr.dataset.id = r.id;
    const chk = el("td", "check-col", state.multiMode ? `<input type="checkbox" ${state.selected.has(r.id) ? "checked" : ""}>` : "");
    tr.appendChild(chk);
    tr.appendChild(el("td", null, `<b>${r.name}</b>`));
    tr.appendChild(el("td", null, `<span class="chip ${chipClass(r.status)}">${r.status}</span>`));
    tr.appendChild(el("td", null, r.contact));
    tr.appendChild(el("td", null, r.email));
    tr.appendChild(el("td", null, fmtDate(r.lastSession)));
    tr.appendChild(el("td", null, fmtDate(r.nextSession)));
    tr.appendChild(el("td", null, String(r.totalSessions)));

    tr.onclick = (e) => rowClick(r, i, e);
    tr.ondblclick = () => openEngagementPopout(r.id);
    tr.oncontextmenu = (e) => { e.preventDefault(); showActionsMenu(e.clientX, e.clientY); };
    tbody.appendChild(tr);
  });

  renderStatusBar();
  renderSortMarks();
  renderPreview();
}

function rowClick(r, i, e) {
  state.focusedIdx = i;
  if (e.ctrlKey || e.metaKey) {
    state.multiMode = true; // multi-select reveals the checkbox column (REQ-023)
    state.selected.has(r.id) ? state.selected.delete(r.id) : state.selected.add(r.id);
  } else if (e.shiftKey) {
    state.multiMode = true;
    const anchor = state.filtered.findIndex(x => state.selected.has(x.id));
    const [from, to] = [Math.min(anchor < 0 ? i : anchor, i), Math.max(anchor < 0 ? i : anchor, i)];
    for (let k = from; k <= to; k++) state.selected.add(state.filtered[k].id);
  } else {
    state.selected = new Set([r.id]);
    state.multiMode = false;
  }
  renderGrid();
}

function renderStatusBar() {
  // Server-side truth (REQ-026): counts are over the entire filtered
  // set (the fake data IS the entire set here).
  const total = state.filtered.length;
  const sel = state.selected.size;
  // Keep-selection-with-notice (REQ-023): report selected rows hidden
  // by the current filter instead of silently deselecting.
  const hidden = [...state.selected].filter(id => !state.filtered.some(r => r.id === id)).length;
  let right = `${total} rows`;
  if (sel) right += `, ${sel} selected`;
  if (hidden) right += ` <span class="sel-note">(${hidden} selected not in current filter)</span>`;
  $("status-right").innerHTML = right;
  $("status-left").textContent = "View: My Active Engagements";
}

// Multi-column sorting (REQ-025): click = sole sort, shift-click adds.
function headerClick(th, e) {
  const col = th.dataset.col;
  if (!col) return;
  const existing = state.sort.find(s => s.col === col);
  if (e.shiftKey) {
    if (!existing) state.sort.push({ col, dir: "asc" });
    else if (existing.dir === "asc") existing.dir = "desc";
    else state.sort = state.sort.filter(s => s.col !== col);
  } else {
    if (existing && state.sort.length === 1) existing.dir = existing.dir === "asc" ? "desc" : "asc";
    else state.sort = [{ col, dir: "asc" }];
  }
  // Header sorting is a temporary view modification (REQ-025).
  $("view-modified").style.display = state.sort.length ? "inline" : "none";
  applyFilters();
}
function renderSortMarks() {
  document.querySelectorAll("#eng-grid-head th").forEach(th => {
    const col = th.dataset.col;
    if (!col) return;
    th.innerHTML = th.textContent.replace(/[▲▼] ?\d?$/, "").trim();
    const idx = state.sort.findIndex(s => s.col === col);
    if (idx >= 0) {
      const s = state.sort[idx];
      th.innerHTML += ` <span class="sort-mark">${s.dir === "asc" ? "▲" : "▼"}${state.sort.length > 1 ? " " + (idx + 1) : ""}</span>`;
    }
  });
}

function clearSearch() {
  state.search = "";
  $("grid-search").value = "";
  applyFilters();
}
window.clearSearch = clearSearch;

// ------------------------------------------------------------- preview
// Docked read-optimized preview following the selection (REQ-012);
// engagement preview leads with notes + open action items (REQ-073/074).
function currentRow() {
  if (state.selected.size === 1) return state.rows.find(r => r.id === [...state.selected][0]);
  return state.filtered[state.focusedIdx] || null;
}

function renderPreview() {
  const pane = $("preview-pane");
  const r = currentRow();
  if (!r) { pane.innerHTML = `<p class="preview-hint">Select an engagement row to preview it here. The preview always follows the selected row.</p>`; return; }
  const held = (SESSIONS[r.id] || []).filter(s => s.status === "Held");
  const co = COMPANIES[r.companyId];
  const cl = CLIENTS[r.clientId];

  let rollup = "";
  if (!held.length) {
    rollup = `<p class="preview-hint">No sessions held yet — notes and action items will aggregate here from each session (you never open sessions one by one to find them).</p>`;
  } else {
    rollup = held.slice(0, 3).map(s => `
      <div class="rollup-item ${s.actionItems ? "open-ai" : ""}">
        <div class="ri-head">Session ${fmtDate(s.date)} — notes & action items</div>
        <div>${s.notes ? s.notes.split(". ").slice(0, 2).join(". ") + "." : ""}</div>
        ${s.actionItems || ""}
      </div>`).join("");
    if (held.length > 3) rollup += `<p class="preview-hint">…rollup continues (${held.length - 3} more sessions) — full rollup on the session prep surface.</p>`;
  }

  pane.innerHTML = `
    <h2>${r.name}</h2>
    <div class="preview-sub"><span class="chip ${chipClass(r.status)}">${r.status}</span> · ${r.totalSessions} sessions · ${r.id}</div>
    <div class="preview-section">
      <h3>Notes &amp; open action items (all sessions)</h3>
      ${rollup}
    </div>
    <div class="preview-section">
      <h3>Engagement</h3>
      <dl class="kv">
        <dt>Summary</dt><dd class="editable" title="Double-click to edit just this field">${r.summary}</dd>
        <dt>Next session</dt><dd>${fmtDate(r.nextSession)}</dd>
        <dt>Last session</dt><dd>${fmtDate(r.lastSession)}</dd>
      </dl>
    </div>
    <div class="preview-section">
      <h3>Client · Company · Contacts (click to open)</h3>
      <button class="link-row" onclick="openClientPopout('${r.clientId}')">🏛 Client — ${co.name} (since ${cl.since})</button>
      <button class="link-row" onclick="openCompanyPopout('${r.companyId}')">🏢 Company — ${co.name}</button>
      ${r.contactIds.map(cid => `<button class="link-row" onclick="openContactPopout('${cid}')">👤 ${CONTACTS[cid].name} — ${CONTACTS[cid].role}</button>`).join("")}
    </div>
    <div class="preview-section">
      <h3>Sessions</h3>
      ${(SESSIONS[r.id] || []).map(s => `<button class="link-row" onclick="openPrep('${r.id}','${s.id}')">📅 ${fmtDate(s.date)} — ${s.status}${s.status === "Scheduled" ? " → open prep surface" : ""}</button>`).join("") || `<p class="preview-hint">No sessions yet.</p>`}
      ${r.status === "Pending Acceptance" ? `<p class="preview-hint" style="margin-top:6px">Pending acceptance: use <b>Accept Assignment</b> (Other Actions) — then send the intro email and schedule the first session (REQ-076).</p>` : ""}
    </div>
    <p class="preview-hint">Read-optimized preview — no edit controls. Edit via the Edit action, or double-click a field for the per-field edit window.</p>
  `;
  pane.querySelectorAll("dd.editable").forEach(dd => {
    dd.ondblclick = () => perFieldEditWindow(r, "Summary", dd.textContent);
  });
}

// Per-field edit window (REQ-035) — simulated pop-out.
function perFieldEditWindow(r, fieldLabel, value) {
  const p = makePopout(`Edit field — ${fieldLabel} (${r.name})`, `
    <p style="font-size:var(--type-1);color:var(--slot-text-dim);margin-bottom:6px">
      Single-field edit: Save commits just this field with a concurrency check. No full-record save.</p>
    <textarea style="width:100%;height:90px;font:inherit;padding:6px">${value}</textarea>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">
      <button class="btn" data-save>Save</button>
      <button class="btn ghost" data-cancel>Cancel</button>
    </div>`);
  p.querySelector("[data-save]").onclick = () => {
    r.summary = p.querySelector("textarea").value;
    p.remove(); renderPreview();
    toast("Field saved (single-field write, rowVersion checked). All open windows showing this record update immediately.");
  };
  p.querySelector("[data-cancel]").onclick = () => p.remove();
}

// ------------------------------------------------------------ pop-outs
// Simulated pop-outs: in the product these are REAL browser windows
// pinned to their record (SKL-113); several may be open at once.
let popoutOffset = 0;
function makePopout(title, bodyHtml) {
  const p = el("div", "popout");
  popoutOffset = (popoutOffset + 26) % 130;
  p.style.left = (320 + popoutOffset) + "px";
  p.style.top = (110 + popoutOffset) + "px";
  p.innerHTML = `
    <div class="popout-titlebar">${title}<span class="sim-note">(real browser window in the product)</span><button title="Close">✕</button></div>
    <div class="popout-body">${bodyHtml}</div>`;
  p.querySelector(".popout-titlebar button").onclick = () => p.remove();
  // draggable
  const bar = p.querySelector(".popout-titlebar");
  bar.onmousedown = (e) => {
    if (e.target.tagName === "BUTTON") return;
    const sx = e.clientX - p.offsetLeft, sy = e.clientY - p.offsetTop;
    const move = (ev) => { p.style.left = (ev.clientX - sx) + "px"; p.style.top = (ev.clientY - sy) + "px"; };
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };
  document.body.appendChild(p);
  return p;
}

function kvBlock(pairs) {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

window.openContactPopout = (cid) => {
  const c = CONTACTS[cid]; const co = COMPANIES[c.companyId];
  makePopout(`Contact — ${c.name}`, `
    <h3>${c.name}</h3>
    ${kvBlock([["Role", c.role], ["Company", co.name], ["Email", `<a href="mailto:${c.email}">${c.email}</a>`], ["Phone", c.phone], ["Notes", c.notes]])}`);
};
window.openCompanyPopout = (coid) => {
  const co = COMPANIES[coid];
  makePopout(`Company — ${co.name}`, `
    <h3>${co.name}</h3>
    ${kvBlock([["Type", co.type + " (company subclass — REQ-086)"], ["Industry", co.industry], ["Address", co.address], ["Phone", co.phone], ["Website", `<a href="https://${co.website}" onclick="return false">${co.website}</a>`], ["Employees", co.employees], ["Founded", co.founded]])}`);
};
window.openClientPopout = (clid) => {
  const cl = CLIENTS[clid]; const co = COMPANIES[cl.companyId];
  makePopout(`Client — ${co.name}`, `
    <h3>${co.name} <span style="font-size:var(--type-1);color:var(--slot-text-dim)">(client subclass of company)</span></h3>
    ${kvBlock([["Client since", cl.since], ["Program", cl.program], ["Referral source", cl.referral], ["Stage", cl.stage], ["Company record", `<button class="link-row" onclick="openCompanyPopout('${cl.companyId}')">Open company</button>`]])}`);
};
window.openEngagementPopout = (eid) => {
  const r = state.rows.find(x => x.id === eid);
  const held = (SESSIONS[eid] || []).filter(s => s.status === "Held");
  makePopout(`Engagement — ${r.name}`, `
    <h3>${r.name} <span class="chip ${chipClass(r.status)}">${r.status}</span></h3>
    ${kvBlock([["Primary contact", r.contact], ["Email", r.email], ["Sessions held", held.length], ["Next session", fmtDate(r.nextSession)], ["Summary", r.summary]])}
    <p style="font-size:var(--type-1);color:var(--slot-text-dim);margin-top:6px">
    Pop-out is pinned to this record; the docked preview keeps following the grid selection.</p>`);
};

// -------------------------------------------------------- actions menu
// Never hide, never disable (REQ-021): every action always listed;
// invalid invocations explain in educate voice.
const ACTIONS = [
  { label: "Schedule Session", run: actScheduleSession },
  { label: "Send Email (templated)", run: actSendEmail },
  "sep",
  { label: "View", run: () => { const r = needOne("View"); if (r) openEngagementPopout(r.id); } },
  { label: "Edit", run: actEdit },
  { label: "New Engagement", run: () => educateDialog("New Engagement", "Engagements are created by CBM staff when a client is matched with a mentor. Mentors work assigned engagements rather than creating them. If you believe a client of yours is missing, contact the program office — programs@cbmentors.org.") },
  "sep",
  { label: "Accept Assignment", run: actAccept },
  { label: "Decline Assignment", run: actDecline },
  { label: "Put On Hold", run: actHold },
  "sep",
  { label: "Export (CSV / Excel)", run: actExport },
  { label: "Print", run: () => { statusMsg("Preparing print rendering of the current view…"); setTimeout(() => toast("Print view opens in a new window rendered exactly as this view shows (columns, sort, filters, search)."), 600); } },
  "sep",
  { label: "Help", run: () => showHelp("engagements-grid") },
];

function needOne(actionName) {
  if (state.selected.size === 1) return state.rows.find(r => r.id === [...state.selected][0]);
  educateDialog(`${actionName} needs exactly one engagement`,
    state.selected.size === 0
      ? `<b>What happened:</b> ${actionName} ran with no row selected.<br><b>Why it can't run:</b> it operates on a single engagement.<br><b>What next:</b> click an engagement row to select it, then run ${actionName} again.`
      : `<b>What happened:</b> ${actionName} ran with ${state.selected.size} rows selected.<br><b>Why it can't run:</b> it operates on exactly one engagement at a time.<br><b>What next:</b> keep one row selected (plain click replaces the selection), then run ${actionName} again.`);
  return null;
}

function actScheduleSession() {
  const r = needOne("Schedule Session"); if (!r) return;
  educateDialog(`Schedule Session — ${r.name}`, `
    <dl class="kv"><dt>Date &amp; time</dt><dd><input type="datetime-local" value="2026-07-14T10:00" style="font:inherit;padding:2px"></dd>
    <dt>Conference</dt><dd><label><input type="radio" name="conf" checked> Create org-hosted Zoom meeting automatically</label><br>
    <label><input type="radio" name="conf"> Paste an existing meeting link</label></dd></dl>
    <div class="educate">On save: the meeting is created under the CBM organization account, the Conference Link fills automatically (REQ-080), and ${r.contact} receives a calendar invite carrying the link (REQ-078).</div>`,
    [{ label: "Schedule & Invite", style: "", onClick: () => { statusMsg("Creating org Zoom meeting…"); setTimeout(() => toast(`Session scheduled. Invite sent to ${r.email} with the meeting link. A notification will confirm delivery.`), 700); } },
     { label: "Cancel" }]);
}

function actSendEmail() {
  const r = needOne("Send Email"); if (!r) return;
  educateDialog(`Send Email — ${r.contact} (${r.name})`, `
    <dl class="kv"><dt>To</dt><dd>${r.email}</dd>
    <dt>Template</dt><dd><select style="font:inherit;padding:2px">
      <option>Mentor introduction (post-acceptance)</option>
      <option>Session follow-up & action items</option>
      <option>Resource share</option>
      <option>Re-engagement check-in (dormant)</option>
    </select></dd></dl>
    <div class="educate">Templated outbound email (REQ-077): the template merges this engagement's fields; you review before it sends. Template list is staff-maintained.</div>`,
    [{ label: "Preview & Send", style: "", onClick: () => toast(`Email prepared from template for ${r.email} — preview would open here.`) }, { label: "Cancel" }]);
}

function actEdit() {
  const r = needOne("Edit"); if (!r) return;
  educateDialog(`Edit — ${r.name}`, `The Edit action opens the full-screen edit form (REQ-032): all fields editable, layout matching the read view scaled up, Save/Cancel, dirty-guard on leave. <br><br><i>The full edit form is out of scope for this prototype gate — it follows the forms standard (SKL-114) exactly.</i>`);
}

function actAccept() {
  const r = needOne("Accept Assignment"); if (!r) return;
  if (r.status !== "Pending Acceptance") {
    educateDialog("Accept Assignment can't run on this engagement", `
      <b>What happened:</b> Accept Assignment ran on <b>${r.name}</b>.<br>
      <b>Why it can't run:</b> its status is <b>${r.status}</b> — only engagements in <b>Pending Acceptance</b> can be accepted.<br>
      <b>What next:</b> select a pending engagement (the two at the top of this view), or use Other Actions appropriate to this status.`);
    return;
  }
  educateDialog(`Accept Assignment — ${r.name}`, `Accepting sets the status to <b>Assigned</b> and opens your first steps in place (REQ-076): send the introduction email (templated) and schedule the first session.`,
    [{ label: "Accept", style: "", onClick: () => { r.status = "Assigned"; state.rows = triageSort(state.rows); applyFilters(); toast(`${r.name} accepted — status is now Assigned. Next: intro email + first session (both in Other Actions).`); } },
     { label: "Cancel" }]);
}

function actDecline() {
  const r = needOne("Decline Assignment"); if (!r) return;
  if (r.status !== "Pending Acceptance") {
    educateDialog("Decline Assignment can't run on this engagement", `
      <b>What happened:</b> Decline Assignment ran on <b>${r.name}</b> (status <b>${r.status}</b>).<br>
      <b>Why it can't run:</b> only a <b>Pending Acceptance</b> assignment can be declined.<br>
      <b>What next:</b> if you need to step away from an active engagement, contact the program office to reassign it.`);
    return;
  }
  // Decline = status change only (REQ-066 as amended by DEC-071);
  // classified modifying, so it confirms.
  educateDialog(`Decline Assignment — ${r.name}`, `Declining sets this engagement's status to <b>Assignment Declined</b> and it leaves your list. Nothing is deleted — records are never physically deleted; staff sees the declined assignment and reassigns it.`,
    [{ label: "Decline Assignment", style: "danger", onClick: () => { r.status = "Assignment Declined"; state.rows = state.rows.filter(x => x.id !== r.id); state.selected.delete(r.id); applyFilters(); toast(`${r.name} declined — removed from your list (status change only; staff will reassign).`); } },
     { label: "Cancel" }]);
}

function actHold() {
  const r = needOne("Put On Hold"); if (!r) return;
  educateDialog(`Put On Hold — ${r.name}`, `On Hold means the <b>client requested a pause</b> (REQ-075). If the client has simply stopped responding, use Dormant instead — the board report distinguishes them.`,
    [{ label: "Put On Hold", style: "", onClick: () => { r.status = "On Hold"; state.rows = triageSort(state.rows); applyFilters(); toast(`${r.name} is now On Hold.`); } }, { label: "Cancel" }]);
}

function actExport() {
  // Export: selection if any, else entire filtered set (REQ-027).
  const scope = state.selected.size ? `${state.selected.size} selected rows` : `the entire filtered set (${state.filtered.length} rows)`;
  educateDialog("Export — Engagements", `
    Exports <b>${scope}</b> exactly as this view shows it: columns, order, formats, sort, filters, and the active search.<br>
    <dl class="kv" style="margin-top:8px"><dt>Format</dt><dd><label><input type="radio" name="xf" checked> Excel (.xlsx)</label> &nbsp;<label><input type="radio" name="xf"> CSV</label></dd>
    <dt>Values</dt><dd><label><input type="checkbox"> Export raw values (unformatted)</label></dd></dl>`,
    [{ label: "Export", style: "", onClick: () => {
        // Long runs are background tasks with progress (REQ-014/026/027).
        let pct = 0;
        const iv = setInterval(() => {
          pct += 25;
          statusMsg(`Exporting engagements… ${pct}% (about ${Math.max(1, 4 - pct / 25)}s left)`, 0);
          if (pct >= 100) { clearInterval(iv); statusMsg(""); bumpBell(`Export ready: Engagements — My Active Engagements (${scope})`); toast("Export finished — pick it up from the notification bell. You were free to keep working the whole time."); }
        }, 700);
      } }, { label: "Cancel" }]);
}

function showActionsMenu(x, y) {
  closeDialogs();
  const m = el("div", "menu");
  m.appendChild(el("div", "menu-note", `Actions run on the selected rows (${state.selected.size || "none"} selected). Nothing is ever hidden or disabled.`));
  ACTIONS.forEach(a => {
    if (a === "sep") { m.appendChild(el("div", "menu-sep")); return; }
    const b = el("button", null, a.label);
    b.onclick = () => { m.remove(); a.run(); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  const rect = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - rect.width - 8) + "px";
  m.style.top = Math.min(y, innerHeight - rect.height - 8) + "px";
}

// --------------------------------------------------------------- help
// Situation-specific help (REQ-043): separate window, never navigates
// the working window; unmapped pages go to help home with a note.
function showHelp(pageKey) {
  const mapped = { "engagements-grid": "Working your engagement list (My Active Engagements)", "session-prep": "Preparing for and conducting a session", "home": "Your Home panel and admin messages" }[pageKey];
  educateDialog("Help — opens in a separate window", mapped
    ? `In the product this opens the help site page “<b>${mapped}</b>” in a separate browser window — your working window never navigates away.`
    : `No page-specific help exists yet for this panel, so Help opens the help site's home with that note — never a dead link, never a hidden icon.`);
}

// ------------------------------------------------- bell & notifications
function bumpBell(text) {
  NOTIFICATIONS.unshift({ id: "N-" + Date.now(), text, detail: "engagements-2026-07-05.xlsx", time: "Just now", read: false, kind: "success" });
  updateBellBadge();
}
function updateBellBadge() {
  const unread = NOTIFICATIONS.filter(n => !n.read).length;
  const b = $("bell-badge");
  b.textContent = unread; b.style.display = unread ? "" : "none";
}
function showBell() {
  closeDialogs();
  const d = el("div", "dropdown");
  d.style.right = "12px"; d.style.top = (document.querySelector(".app-header").offsetHeight + 2) + "px";
  d.appendChild(el("div", "dd-head", "Notifications — background tasks & system events (read on view, expire over time)"));
  NOTIFICATIONS.forEach(n => {
    const item = el("div", "notif" + (n.read ? "" : " unread") + (n.kind === "error" ? " error" : ""),
      `<div class="n-text">${n.kind === "error" ? "⚠ " : "✓ "}${n.text}</div><div class="n-detail">${n.detail}</div><div class="n-time">${n.time}</div>`);
    d.appendChild(item);
    n.read = true; // read on view (REQ-014)
  });
  document.body.appendChild(d);
  setTimeout(updateBellBadge, 400);
  setTimeout(() => document.addEventListener("click", function h(e) { if (!d.contains(e.target)) { d.remove(); document.removeEventListener("click", h); } }), 0);
}

// ------------------------------------------------------- quick palette
function showPalette() {
  closeDialogs();
  const targets = [
    { label: "Home", kind: "panel", go: () => switchScreen("home") },
    { label: "Engagements — My Active Engagements", kind: "panel + view", go: () => switchScreen("engagements") },
    { label: "Engagements — Pending Acceptance", kind: "panel + view", go: () => switchScreen("engagements") },
    { label: "Contacts", kind: "panel", go: () => switchScreen("contacts") },
    { label: "Companies", kind: "panel", go: () => switchScreen("companies") },
    { label: "Clients", kind: "panel", go: () => switchScreen("clients") },
    { label: "Sessions", kind: "panel", go: () => switchScreen("sessions") },
    { label: "Resources", kind: "panel", go: () => switchScreen("resources") },
    { label: "Events", kind: "panel", go: () => switchScreen("events") },
  ];
  const p = el("div", "palette");
  p.innerHTML = `<input placeholder="Quick open — type a panel or view name… (Ctrl+K)"><div class="p-results"></div>`;
  const input = p.querySelector("input"), results = p.querySelector(".p-results");
  const render = (q) => {
    results.innerHTML = "";
    targets.filter(t => t.label.toLowerCase().includes(q.toLowerCase())).forEach(t => {
      const b = el("button", null, `${t.label}<span class="p-kind">${t.kind}</span>`);
      b.onclick = () => { p.remove(); t.go(); };
      results.appendChild(b);
    });
  };
  input.oninput = () => render(input.value);
  input.onkeydown = (e) => { if (e.key === "Escape") p.remove(); if (e.key === "Enter") { const f = results.querySelector("button"); if (f) f.click(); } };
  render("");
  document.body.appendChild(p);
  input.focus();
}

// ----------------------------------------------------------- home panel
function renderHome() {
  const wrap = $("home-grid");
  wrap.innerHTML = "";

  // Admin messages dashlet (REQ-011)
  const msgs = el("div", "dashlet");
  msgs.appendChild(el("div", "dashlet-head", `Messages from CBM <span class="dh-note">newest first · read state is per-user</span>`));
  ADMIN_MESSAGES.forEach(m => {
    const box = el("div", "msg" + (m.read ? "" : " unread") + (m.priority === "urgent" && !m.read ? " urgent" : ""));
    box.innerHTML = `
      <div class="m-title">${m.title}</div>
      <div class="m-meta">${m.postedBy} · ${m.date} · expires ${m.expires}${m.priority === "urgent" ? " · URGENT" : ""}</div>
      <div class="m-body">${m.body}</div>
      ${m.requiresAck ? `<div class="m-ack">${m.acked ? `<span class="ack-done">✓ Acknowledged</span>` : `<button class="btn secondary" data-ack>Acknowledge</button> <span style="font-size:var(--type-1);color:var(--slot-text-dim)">the admin sees who has not acknowledged</span>`}</div>` : ""}`;
    if (!m.read) { m.read = true; setTimeout(renderUrgentBanner, 300); } // auto-read on view
    const ack = box.querySelector("[data-ack]");
    if (ack) ack.onclick = () => { m.acked = true; renderHome(); toast("Acknowledgment recorded — visible to the administrator."); };
    msgs.appendChild(box);
  });
  wrap.appendChild(msgs);

  // Right column: the user's chosen dashlets (a dashlet = any view rendered small)
  const right = el("div");
  right.style.display = "grid"; right.style.gap = "12px";

  const pend = el("div", "dashlet");
  pend.appendChild(el("div", "dashlet-head", `Needs my acceptance <span class="dh-note">dashlet = view rendered small</span>`));
  const pt = el("table", "mini-table");
  DASHLET_PENDING.forEach(e2 => {
    const tr = el("tr", null, `<td><b>${e2.name}</b></td><td><span class="chip ${chipClass(e2.status)}">${e2.status}</span></td><td>${e2.contact}</td>`);
    tr.onclick = () => { switchScreen("engagements"); state.selected = new Set([e2.id]); state.focusedIdx = state.filtered.findIndex(x => x.id === e2.id); renderGrid(); };
    pt.appendChild(tr);
  });
  pend.appendChild(pt);
  right.appendChild(pend);

  const up = el("div", "dashlet");
  up.appendChild(el("div", "dashlet-head", `Upcoming sessions <span class="dh-note">next 30 days</span>`));
  const ut = el("table", "mini-table");
  DASHLET_UPCOMING.forEach(s => {
    const tr = el("tr", null, `<td>${s.date}</td><td><b>${s.engagement}</b></td><td>${s.contact}</td>`);
    tr.onclick = () => { switchScreen("engagements"); toast("Opens that engagement's session prep surface in the product."); };
    ut.appendChild(tr);
  });
  up.appendChild(ut);
  right.appendChild(up);

  wrap.appendChild(right);
}

// Urgent banner across every panel until read (REQ-011).
function renderUrgentBanner() {
  const slot = $("urgent-banner-slot");
  slot.innerHTML = "";
  ADMIN_MESSAGES.filter(m => m.priority === "urgent" && !m.read).forEach(m => {
    const b = el("div", "urgent-banner", `<b>URGENT</b> ${m.title} — posted by ${m.postedBy}, ${m.date}`);
    const go = el("button", "btn ghost", "Read on Home");
    go.onclick = () => switchScreen("home");
    b.appendChild(go);
    slot.appendChild(b);
  });
}

// -------------------------------------------------- session prep screen
// REQ-081: data-dense refresh of the whole engagement — status/history,
// consolidated notes across all sessions, full session history — plus
// note-taking (REQ-082) and the conference link (REQ-079).
window.openPrep = (engId, sessionId) => {
  state.prepSession = { engId, sessionId };
  switchScreen("prep");
};

function renderPrep() {
  const host = $("screen-prep");
  const ctx = state.prepSession || { engId: "ENG-1027", sessionId: "S-2701" };
  const r = state.rows.find(x => x.id === ctx.engId) || ENGAGEMENTS.find(x => x.id === ctx.engId);
  const sessions = SESSIONS[r.id] || [];
  const s = sessions.find(x => x.id === ctx.sessionId) || sessions[0];
  const held = sessions.filter(x => x.status === "Held");
  const co = COMPANIES[r.companyId];

  host.innerHTML = "";
  const wrap = el("div", "prep-wrap");

  // ---- main column: the refresh (read side)
  const main = el("div", "prep-main");
  main.innerHTML = `
    <div class="prep-header">
      <h2>${r.name} — Session ${fmtDate(s?.date)}</h2>
      <span class="chip ${chipClass(r.status)}">${r.status}</span>
      <button class="btn ghost" onclick="switchScreen('engagements')" style="margin-left:auto">← Back to Engagements</button>
    </div>
    <div class="prep-stats" style="margin-bottom:8px">
      <span>Sessions held: <b>${held.length}</b></span>
      <span>First session: <b>${held.length ? fmtDate(held[held.length - 1].date) : "—"}</b></span>
      <span>Last session: <b>${fmtDate(r.lastSession)}</b></span>
      <span>Primary contact: <b>${r.contact}</b> · ${r.email}</span>
      <span>Company: <b>${co.name}</b></span>
    </div>
    <div class="conf-bar">
      <button class="btn" onclick="toast('Launches the conference link — the app never hosts video (REQ-079).')">▶ Join Video Conference</button>
      <span class="conf-link">${s?.conferenceLink || "No conference link yet — paste one or let scheduling create the meeting (REQ-080)."}</span>
      <button class="icon-btn" title="Copy link" onclick="toast('Link copied.')">⧉</button>
    </div>
    <div class="card">
      <div class="card-head">Engagement summary</div>
      <div class="card-body">${r.summary}</div>
    </div>
    <div class="card">
      <div class="card-head">All notes &amp; action items across this engagement (newest first) — the consolidated refresh</div>
      <div class="card-body" id="prep-history"></div>
    </div>`;
  const histBox = main.querySelector("#prep-history");
  if (!held.length) histBox.innerHTML = `<p class="preview-hint">No sessions held yet. This panel fills with every session's notes and action items as the engagement runs.</p>`;
  held.forEach(h => {
    histBox.appendChild(el("div", "hist-item", `
      <div class="h-date">${fmtDate(h.date)}</div>
      <div class="h-notes">${h.notes}</div>
      ${h.actionItems ? `<div style="font-size:var(--type-1);color:var(--slot-text-dim);margin-top:2px">Action items:</div>${h.actionItems}` : ""}`));
  });

  // ---- side column: conduct (write side)
  const side = el("div", "prep-side");
  side.innerHTML = `
    <h3 style="font-size:var(--type-4);color:var(--slot-brand);margin-bottom:6px">This session — notes &amp; action items</h3>
    <p style="font-size:var(--type-1);color:var(--slot-text-dim);margin-bottom:8px">
      Entered during the call or shortly after (REQ-082). Action items are bulleted rich text — deliberately simple, no task records in v1.</p>
    <div class="editor-toolbar"><button onclick="document.execCommand('bold')"><b>B</b></button><button onclick="document.execCommand('italic')"><i>I</i></button><button onclick="document.execCommand('insertUnorderedList')">• List</button></div>
    <div class="notes-editor" contenteditable="true" id="prep-notes"><p><i>Session notes…</i></p></div>
    <h4 style="font-size:var(--type-2);margin:10px 0 4px;color:var(--slot-text-dim)">ACTION ITEMS</h4>
    <div class="notes-editor" contenteditable="true" style="min-height:80px" id="prep-ai"><ul><li></li></ul></div>
    <div style="display:flex;gap:8px;margin-top:10px">
      <button class="btn" onclick="toast('Notes saved to this session. The engagement rollup and every open window update immediately (same-user sync).')">Save Notes (Ctrl+S)</button>
      <button class="btn secondary" onclick="toast('Wrap-up: schedule the next session now, while concluding the call (REQ-078).')">Schedule Next Session</button>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="card-head">After the call — AI assist (REQ-083)</div>
      <div class="card-body" style="font-size:var(--type-2)">
        For meetings the app created, the transcript is retrieved from the conferencing platform and a <b>draft</b> summary with suggested action items lands here for your review — you remain the author of record. Pasting a transcript works when automation can't.
        <div style="margin-top:6px"><button class="btn ghost" onclick="toast('Draft summary would populate the notes editor for your review and edit.')">Review draft summary…</button></div>
      </div>
    </div>`;

  wrap.appendChild(main);
  wrap.appendChild(side);
  host.appendChild(wrap);
}

// ------------------------------------------------------ screen switching
const PLACEHOLDER_TEXT = {
  contacts: "Contacts", companies: "Companies", clients: "Clients",
  sessions: "Sessions", resources: "Resources", events: "Events",
};
function switchScreen(name) {
  state.currentScreen = name;
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.screen === name));
  if (name === "engagements") { $("screen-engagements").classList.add("active"); renderGrid(); }
  else if (name === "home") { $("screen-home").classList.add("active"); renderHome(); }
  else if (name === "prep") { $("screen-prep").classList.add("active"); renderPrep(); }
  else {
    $("screen-placeholder").classList.add("active");
    $("placeholder-body").innerHTML = `<b>${PLACEHOLDER_TEXT[name] || name}</b> — standard grid panel (same anatomy as Engagements: action bar / grid / status bar, views, preview).<br>
      Out of scope for this prototype gate; included so the navigation reads true (REQ-071).<br>
      <button class="btn ghost" style="margin-top:10px" onclick="switchScreen('engagements')">Back to Engagements</button>`;
  }
  renderUrgentBanner();
}
window.switchScreen = switchScreen;
window.toast = toast;

// ---------------------------------------------------------------- wiring
document.querySelectorAll(".nav-item").forEach(b => b.onclick = () => switchScreen(b.dataset.screen));
document.querySelectorAll(".nav-pin").forEach(b => b.onclick = () => { switchScreen("engagements"); });
$("pin-pending").onclick = () => {
  switchScreen("engagements");
  toast("In the product this pin opens the Engagements panel with the 'Pending Acceptance' view active (pin = panel + view reference).");
};

$("grid-search").addEventListener("input", (e) => {
  const v = e.target.value;
  // Live search from the 3rd character (REQ-020).
  state.search = v.length >= 3 ? v : "";
  if (v.length >= 3) statusMsg(`Searching displayed columns for "${v}" — server-side over the entire filtered set…`, 1200);
  applyFilters();
});
$("grid-search").addEventListener("focus", () => { /* last-5 search history would drop down here (REQ-020) */ });

document.querySelectorAll("#eng-grid-head th").forEach(th => th.onclick = (e) => headerClick(th, e));

$("act-schedule").onclick = actScheduleSession;
$("act-email").onclick = actSendEmail;
$("act-other").onclick = (e) => { const r = e.target.getBoundingClientRect(); showActionsMenu(r.left, r.bottom + 4); };
$("btn-view-edit").onclick = () => educateDialog("View settings", `The view editor defines: data source · displayed fields, column order/width/format · grouping (nested levels, tree, collapsed default) · row theme (height, colors, font, conditional formatting) · whether ad-hoc column filters are allowed (REQ-018).<br><br><i>System views are read-only — saving creates your own view (REQ-017). Out of scope for this gate.</i>`);
$("view-selector").onchange = (e) => {
  if (e.target.selectedIndex !== 0) {
    educateDialog("Views in this prototype", `Only <b>My Active Engagements</b> is wired with fake data in this prototype. In the product, choosing a view applies it instantly; your last-used view is remembered per grid (REQ-017).`);
    e.target.selectedIndex = 0;
  }
};

$("btn-bell").onclick = showBell;
$("btn-help").onclick = () => showHelp(state.currentScreen === "engagements" ? "engagements-grid" : state.currentScreen === "prep" ? "session-prep" : state.currentScreen === "home" ? "home" : "unmapped");
$("btn-user").onclick = (e) => {
  closeDialogs();
  const m = el("div", "menu");
  m.style.right = "8px"; m.style.top = (document.querySelector(".app-header").offsetHeight + 2) + "px";
  [["Frank Delgado — frank.delgado@cbmentors.org", null],
   ["Navigation style: Side menu ▸", "Choose tabs / side menu / group tree — switch anytime, pins survive (REQ-010)"],
   ["Startup: Engagements (last panel) ▸", "Open to Home or last panel; Home is the system default (REQ-011)"],
   ["Color template: Standard (CBM) ▸", "Standard / Compact / Large print / Dark — fixed slot structure (REQ-044)"],
   ["Manage my views & pins…", null], ["Help", null], ["Log out", "Explicit and total across all windows; dirty-window guards run first"]]
    .forEach(([label, note]) => {
      const b = el("button", null, label + (note ? `<div style="font-size:var(--type-1);color:var(--slot-text-dim)">${note}</div>` : ""));
      b.onclick = () => { m.remove(); toast("Preference surfaces are illustrative in this prototype."); };
      m.appendChild(b);
    });
  document.body.appendChild(m);
  const rect = m.getBoundingClientRect();
  m.style.left = (innerWidth - rect.width - 8) + "px";
  setTimeout(() => document.addEventListener("click", function h(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", h); } }), 0);
};
$("btn-palette").onclick = showPalette;

// Keyboard standard (REQ-024): arrows, space, ctrl+A, enter, /, menu key.
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); showPalette(); return; }
  if (state.currentScreen !== "engagements") return;
  const inInput = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) || document.activeElement.isContentEditable;
  if (e.key === "/" && !inInput) { e.preventDefault(); $("grid-search").focus(); return; }
  if (inInput) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const d = e.key === "ArrowDown" ? 1 : -1;
    state.focusedIdx = Math.max(0, Math.min(state.filtered.length - 1, state.focusedIdx + d));
    if (e.shiftKey) { state.multiMode = true; state.selected.add(state.filtered[state.focusedIdx].id); }
    renderGrid();
  } else if (e.key === " ") {
    e.preventDefault();
    const r = state.filtered[state.focusedIdx]; if (!r) return;
    state.multiMode = true;
    state.selected.has(r.id) ? state.selected.delete(r.id) : state.selected.add(r.id);
    renderGrid();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    // Select-all = the ENTIRE filtered result set (REQ-023).
    state.multiMode = true;
    state.filtered.forEach(r => state.selected.add(r.id));
    renderGrid();
    statusMsg("Select-all selects the entire filtered result set — not just visible rows.");
  } else if (e.key === "Enter") {
    const r = state.filtered[state.focusedIdx]; if (r) openEngagementPopout(r.id);
  } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
    e.preventDefault(); showActionsMenu(innerWidth / 2, innerHeight / 3);
  } else if (e.key === "Escape") {
    closeDialogs();
  }
});

// ------------------------------------------------------------ first load
renderUrgentBanner();
updateBellBadge();
// Mentor default: land on Engagements — My Active Engagements (REQ-072).
state.selected = new Set([state.filtered[0]?.id].filter(Boolean));
applyFilters();
statusMsg("Loading grid…", 900);
