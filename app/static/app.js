"use strict";

const state = {
  websites: [],
  filter: "all",
  search: "",
  loading: true,
  toastTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const formatTime = (value) => {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
};
const statusLabel = (value) => ({
  HEALTHY: "Healthy", DEGRADED: "Degraded", DOWN: "Down", UNKNOWN: "Unknown",
}[value] || "Unknown");
const checkStatusClass = (value) => ({ PASS: "state-healthy", WARN: "state-degraded", FAIL: "state-down", UNKNOWN: "state-unknown" }[value] || "state-unknown");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).join("; ");
    } catch (_) { /* keep the HTTP fallback */ }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3400);
}

function setConnection(ready, label) {
  const indicator = $("#connection-indicator");
  indicator.classList.toggle("is-ready", ready);
  indicator.classList.toggle("is-error", !ready);
  $("#connection-label").textContent = label;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function button(label, action, siteId, extraClass = "") {
  const element = makeElement("button", `action-button ${extraClass}`.trim(), label);
  element.type = "button";
  element.dataset.action = action;
  element.dataset.siteId = String(siteId);
  return element;
}

function displayHttp(site) {
  const check = (site.latest_checks || []).find((item) => item.check_name === "http");
  if (!check || !check.value) return { main: "No result", detail: "Awaiting first check" };
  const code = check.value.status_code;
  return {
    main: code ? `HTTP ${code}` : check.status,
    detail: check.message || "HTTP availability",
  };
}

function displayLatency(site) {
  const check = (site.latest_checks || []).find((item) => item.check_name === "latency");
  if (!check || check.duration_ms === null || check.duration_ms === undefined) {
    return { main: "—", detail: "Not measured" };
  }
  return { main: `${Number(check.duration_ms).toFixed(0)} ms`, detail: check.message || "Response time" };
}

function createSiteCard(site) {
  const card = makeElement("article", `site-card${site.enabled ? "" : " site-disabled"}`);
  const identity = makeElement("div", "site-identity");
  const favicon = makeElement("span", "site-favicon", (site.name || "?").slice(0, 1));
  favicon.setAttribute("aria-hidden", "true");
  const identityCopy = makeElement("div");
  const name = makeElement("span", "site-name", site.name);
  const link = makeElement("a", "site-url", site.url);
  link.href = site.url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = site.url;
  identityCopy.append(name, link);
  identity.append(favicon, identityCopy);

  const health = site.health || {};
  const status = health.state || "UNKNOWN";
  const stateCell = makeElement("div", "state-cell");
  stateCell.append(makeElement("span", `state-pill state-${status.toLowerCase()}`, statusLabel(status)));

  const http = displayHttp(site);
  const httpCell = makeElement("div", "site-http");
  httpCell.append(makeElement("span", "cell-label", "HTTP status"));
  const httpValue = makeElement("div");
  httpValue.append(makeElement("div", "cell-value", http.main), makeElement("div", "cell-subvalue", http.detail));
  httpCell.append(httpValue);

  const latency = displayLatency(site);
  const latencyCell = makeElement("div", "site-latency");
  latencyCell.append(makeElement("span", "cell-label", "Response time"));
  const latencyValue = makeElement("div");
  latencyValue.append(makeElement("div", "cell-value", latency.main), makeElement("div", "cell-subvalue", latency.detail));
  latencyCell.append(latencyValue);

  const lastCell = makeElement("div", "site-last-check");
  lastCell.append(makeElement("span", "cell-label", "Last check"));
  lastCell.append(makeElement("span", "cell-value", formatTime(site.last_checked_at)));

  const actions = makeElement("div", "site-actions");
  actions.append(button("Details", "details", site.id));
  const checkButton = button("Check now", "check", site.id, "check-now-button");
  actions.append(checkButton);
  actions.append(button(site.enabled ? "Pause" : "Enable", "toggle", site.id));
  actions.append(button("Edit", "edit", site.id));
  const deleteButton = button("×", "delete", site.id, "action-menu-button");
  deleteButton.setAttribute("aria-label", `Delete ${site.name}`);
  deleteButton.title = "Delete website";
  actions.append(deleteButton);

  card.append(identity, stateCell, httpCell, latencyCell, lastCell, actions);
  return card;
}

function render() {
  const loading = $("#loading-state");
  const empty = $("#empty-state");
  const list = $("#site-list");
  const footer = $("#panel-footer");
  const error = $("#dashboard-error");
  loading.hidden = !state.loading;
  error.hidden = true;
  if (state.loading) return;

  const query = state.search.trim().toLowerCase();
  const filtered = state.websites.filter((site) => {
    const health = site.health?.state || "UNKNOWN";
    const stateMatches = state.filter === "all" || health === state.filter;
    const searchMatches = !query || `${site.name} ${site.url}`.toLowerCase().includes(query);
    return stateMatches && searchMatches;
  });
  const healthy = state.websites.filter((site) => site.health?.state === "HEALTHY").length;
  const down = state.websites.filter((site) => site.health?.state === "DOWN").length;
  const degraded = state.websites.filter((site) => ["DEGRADED", "UNKNOWN"].includes(site.health?.state || "UNKNOWN")).length;
  $("#total-count").textContent = String(state.websites.length);
  $("#healthy-count").textContent = String(healthy);
  $("#degraded-count").textContent = String(degraded);
  $("#down-count").textContent = String(down);
  $("#last-updated").textContent = `Updated ${new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date())}`;

  list.replaceChildren();
  empty.hidden = state.websites.length > 0;
  if (state.websites.length === 0) {
    footer.hidden = true;
    return;
  }
  if (filtered.length === 0) {
    const noMatches = makeElement("div", "loading-state", "No websites match this search or filter.");
    list.append(noMatches);
  } else {
    filtered.forEach((site) => list.append(createSiteCard(site)));
  }
  footer.hidden = false;
  $("#site-count-label").textContent = `${filtered.length} of ${state.websites.length} website${state.websites.length === 1 ? "" : "s"}`;
}

async function loadDashboard({ quiet = false } = {}) {
  if (!quiet) {
    state.loading = true;
    render();
  }
  try {
    const [dashboard, health] = await Promise.all([api("/api/dashboard"), api("/api/health")]);
    state.websites = dashboard.websites || [];
    state.loading = false;
    render();
    setConnection(health.status === "ok", health.status === "ok" ? "Service operational" : "Database issue");
  } catch (error) {
    state.loading = false;
    render();
    setConnection(false, "Service unavailable");
    const banner = $("#dashboard-error");
    banner.textContent = `Could not load monitoring data: ${error.message}`;
    banner.hidden = false;
  }
}

function openSiteDialog(site = null) {
  const dialog = $("#site-dialog");
  const form = $("#site-form");
  form.reset();
  $("#site-form-error").hidden = true;
  $("#site-id").value = site ? String(site.id) : "";
  $("#site-dialog-eyebrow").textContent = site ? "EDIT MONITOR" : "NEW MONITOR";
  $("#site-dialog-title").textContent = site ? "Edit website" : "Add a website";
  $("#site-save-button").textContent = site ? "Save changes" : "Add website";
  $("#site-name").value = site?.name || "";
  $("#site-url").value = site?.url || "";
  $("#site-interval").value = site ? String(site.interval_seconds) : "";
  $("#site-timeout").value = site ? String(site.timeout_seconds) : "";
  dialog.showModal();
  window.setTimeout(() => $("#site-name").focus(), 20);
}

function findSite(id) { return state.websites.find((site) => site.id === Number(id)); }

async function showDetails(site) {
  const dialog = $("#detail-dialog");
  const content = $("#detail-content");
  $("#detail-title").textContent = site.name;
  content.replaceChildren(makeElement("div", "loading-state", "Loading monitoring evidence…"));
  dialog.showModal();
  try {
    const detail = await api(`/api/websites/${site.id}`);
    content.replaceChildren();
    const url = makeElement("p", "detail-url", detail.url);
    const health = detail.health || {};
    const stateCard = makeElement("div", "detail-state-card");
    const stateContent = makeElement("div");
    stateContent.append(makeElement("span", `state-pill state-${(health.state || "UNKNOWN").toLowerCase()}`, statusLabel(health.state)));
    const reason = health.reason || {};
    stateContent.append(makeElement("p", "detail-reason", reason.message || "No monitoring result yet."));
    if (reason.code) stateContent.append(makeElement("p", "cell-subvalue", `Reason code: ${reason.code}`));
    stateCard.append(stateContent, makeElement("span", "detail-time", `Last check ${formatTime(detail.last_checked_at)}`));
    content.append(url, stateCard);

    content.append(makeElement("h3", "detail-section-title", "Latest checks"));
    const checks = detail.latest_checks || [];
    if (!checks.length) {
      content.append(makeElement("p", "no-incident", "Checks will appear after the first monitoring cycle."));
    } else {
      const table = makeElement("table", "check-table");
      const head = makeElement("thead");
      const headerRow = makeElement("tr");
      ["Check", "Result", "Duration", "Details"].forEach((label) => headerRow.append(makeElement("th", "", label)));
      head.append(headerRow);
      const body = makeElement("tbody");
      checks.forEach((check) => {
        const row = makeElement("tr");
        row.append(makeElement("td", "check-name", check.check_name));
        const resultCell = makeElement("td");
        resultCell.append(makeElement("span", `state-pill ${checkStatusClass(check.status)}`, check.status));
        row.append(resultCell);
        const duration = check.duration_ms === null || check.duration_ms === undefined ? "—" : `${Number(check.duration_ms).toFixed(1)} ms`;
        row.append(makeElement("td", "", duration));
        const value = check.value && check.value.status_code ? `HTTP ${check.value.status_code} · ${check.value.final_url || ""}` : (check.message || "—");
        row.append(makeElement("td", "check-detail", value));
        body.append(row);
      });
      table.append(head, body);
      content.append(table);
    }

    content.append(makeElement("h3", "detail-section-title", "Incident status"));
    if (detail.active_incident) {
      const incident = makeElement("div", "incident-callout");
      incident.textContent = `Open incident #${detail.active_incident.id} · started ${formatTime(detail.active_incident.started_at)}. It resolves after confirmed healthy recovery.`;
      content.append(incident);
    } else {
      content.append(makeElement("p", "no-incident", "No open incident."));
    }
    const transitions = detail.recent_transitions || [];
    if (transitions.length) {
      content.append(makeElement("h3", "detail-section-title", "Recent state transitions"));
      transitions.slice(0, 5).forEach((transition) => {
        content.append(makeElement("p", "no-incident", `${transition.from_state} → ${transition.to_state} · ${formatTime(transition.occurred_at)} · ${transition.reason?.code || ""}`));
      });
    }
  } catch (error) {
    content.replaceChildren(makeElement("p", "form-error", `Could not load details: ${error.message}`));
  }
}

async function submitSite(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const errorElement = $("#site-form-error");
  errorElement.hidden = true;
  if (!form.reportValidity()) return;
  const id = $("#site-id").value;
  const payload = {
    name: $("#site-name").value.trim(),
    url: $("#site-url").value.trim(),
  };
  const intervalSeconds = $("#site-interval").value.trim();
  const timeoutSeconds = $("#site-timeout").value.trim();
  if (intervalSeconds) payload.interval_seconds = Number(intervalSeconds);
  if (timeoutSeconds) payload.timeout_seconds = Number(timeoutSeconds);
  const saveButton = $("#site-save-button");
  saveButton.disabled = true;
  try {
    await api(id ? `/api/websites/${id}` : "/api/websites", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    $("#site-dialog").close();
    showToast(id ? "Website settings saved." : "Website added. Its first check will run shortly.");
    await loadDashboard({ quiet: true });
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
}

async function onSiteAction(event) {
  const target = event.target.closest("button[data-action]");
  if (!target) return;
  const site = findSite(target.dataset.siteId);
  if (!site) return;
  const action = target.dataset.action;
  if (action === "details") return showDetails(site);
  if (action === "edit") return openSiteDialog(site);
  if (action === "delete") {
    if (!window.confirm(`Delete ${site.name} and its stored check/incident history? This cannot be undone.`)) return;
    try {
      await api(`/api/websites/${site.id}`, { method: "DELETE" });
      showToast("Website and its stored history deleted.");
      await loadDashboard({ quiet: true });
    } catch (error) { showToast(error.message, true); }
    return;
  }
  if (action === "toggle") {
    try {
      await api(`/api/websites/${site.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !site.enabled }) });
      showToast(site.enabled ? "Monitoring paused." : "Monitoring enabled.");
      await loadDashboard({ quiet: true });
    } catch (error) { showToast(error.message, true); }
    return;
  }
  if (action === "check") {
    target.disabled = true;
    target.textContent = "Checking…";
    try {
      const result = await api(`/api/websites/${site.id}/check`, { method: "POST" });
      showToast(`Check complete: ${statusLabel(result.state)}.`);
      await loadDashboard({ quiet: true });
    } catch (error) { showToast(error.message, true); }
    finally { target.disabled = false; target.textContent = "Check now"; }
  }
}

function closeDialogs(event) {
  const closeButton = event.target.closest("[data-close-dialog]");
  if (!closeButton) return;
  const dialog = document.getElementById(closeButton.dataset.closeDialog);
  if (dialog?.open) dialog.close();
}

async function initialize() {
  $("#add-site-button").addEventListener("click", () => openSiteDialog());
  $("#empty-add-button").addEventListener("click", () => openSiteDialog());
  $("#site-form").addEventListener("submit", submitSite);
  $("#site-list").addEventListener("click", onSiteAction);
  document.addEventListener("click", closeDialogs);
  $("#search-input").addEventListener("input", (event) => { state.search = event.target.value; render(); });
  $("#state-filter").addEventListener("change", (event) => { state.filter = event.target.value; render(); });
  await loadDashboard();
  window.setInterval(() => loadDashboard({ quiet: true }), 30000);
}

document.addEventListener("DOMContentLoaded", initialize);
