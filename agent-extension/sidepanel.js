const API = "https://bot.r0meo1.ru/agent-extension/lead";
const LEADS_API = "https://bot.r0meo1.ru/agent-extension/leads";
const STATUS_API = "https://bot.r0meo1.ru/agent-extension/status";
const EXPORT_API = "https://bot.r0meo1.ru/agent-extension/export.csv";
const fieldIds = ["name","phone","destination","origin","dates","people","budget","consent"];

const $ = (id) => document.getElementById(id);

async function load() {
  const state = await chrome.storage.local.get({
    draft: {},
    candidates: [],
    agentToken: ""
  });
  const draft = state.draft || {};
  for (const id of fieldIds) {
    const el = $(id);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = Boolean(draft[id]);
    else if (draft[id] != null) el.value = draft[id];
    el.addEventListener("input", saveDraft);
    el.addEventListener("change", saveDraft);
  }
  $("token").value = state.agentToken || "";
  renderCandidates(state.candidates || []);
  if (state.agentToken) {
    loadLeads().catch(() => {});
  }
}

async function saveDraft() {
  const draft = {};
  for (const id of fieldIds) {
    const el = $(id);
    draft[id] = el.type === "checkbox" ? el.checked : el.value.trim();
  }
  await chrome.storage.local.set({ draft });
}

function renderCandidates(candidates) {
  const box = $("candidates");
  box.textContent = "";
  $("empty").hidden = Boolean(candidates.length);

  candidates.forEach((item) => {
    const node = document.createElement("article");
    node.className = "candidate";
    const title = document.createElement("strong");
    title.textContent = item.title || "Тур";
    const url = document.createElement("small");
    url.textContent = item.url || "";
    const text = document.createElement("p");
    text.textContent = item.selection || "";
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.textContent = "×";
    remove.title = "Удалить";
    remove.addEventListener("click", () => removeCandidate(item.id));
    node.append(title, url, text, remove);
    box.append(node);
  });
}

async function removeCandidate(id) {
  const state = await chrome.storage.local.get({ candidates: [] });
  const candidates = (state.candidates || []).filter((item) => item.id !== id);
  await chrome.storage.local.set({ candidates });
  renderCandidates(candidates);
}

async function captureCurrentPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) return;
  const state = await chrome.storage.local.get({ candidates: [] });
  const candidates = state.candidates || [];
  candidates.unshift({
    id: crypto.randomUUID(),
    title: tab.title || "Тур",
    url: tab.url,
    selection: "",
    createdAt: Date.now()
  });
  await chrome.storage.local.set({ candidates: candidates.slice(0, 30) });
  renderCandidates(candidates.slice(0, 30));
}

function setStatus(text, ok=false) {
  const status = $("status");
  status.textContent = text;
  status.className = "status " + (ok ? "ok" : "bad");
}

async function sendLead() {
  await saveDraft();
  const state = await chrome.storage.local.get({
    draft: {},
    candidates: [],
    agentToken: ""
  });
  const d = state.draft || {};
  if (!state.agentToken) return setStatus("Сначала сохрани Agent token.");
  if (!d.consent) return setStatus("Нужно отметить согласие клиента.");

  const required = ["name","phone","destination","origin","dates","people","budget"];
  if (required.some((key) => !String(d[key] || "").trim())) {
    return setStatus("Заполни обязательные поля клиента.");
  }

  const active = await chrome.tabs.query({ active: true, currentWindow: true });
  let activeService = "";
  try { activeService = new URL(active[0]?.url || "").hostname; } catch (_) {}

  const requestId = "agent-" + Date.now() + "-" + crypto.randomUUID().slice(0, 8);
  const body = {
    name: d.name,
    phone: d.phone,
    destination: d.destination,
    origin: d.origin,
    dates: d.dates,
    people: d.people,
    budget: d.budget,
    consent: true,
    requestId,
    active_service: activeService,
    candidates: (state.candidates || []).slice(0, 10)
  };

  $("send").disabled = true;
  setStatus("Отправляю…");
  try {
    const response = await fetch(API, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + state.agentToken
      },
      body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || ("HTTP " + response.status));
    }
    setStatus("Заявка #" + data.leadId + " отправлена Наталье.", true);
    await chrome.storage.local.set({
      lastLeadId: data.leadId,
      candidates: []
    });
    renderCandidates([]);
    await loadLeads().catch(() => {});
  } catch (error) {
    setStatus("Не отправлено: " + (error.message || "ошибка"));
  } finally {
    $("send").disabled = false;
  }
}

const STATUS_LABELS = {
  new: "Новая",
  working: "В работе",
  waiting: "Ждём клиента",
  won: "Продано",
  lost: "Потеряно"
};

function formatRub(value) {
  return new Intl.NumberFormat("ru-RU").format(Number(value || 0)) + " ₽";
}

function renderLeads(leads) {
  const box = $("leads");
  box.textContent = "";
  $("leadsEmpty").hidden = Boolean(leads.length);

  leads.forEach((lead) => {
    const card = document.createElement("article");
    card.className = "lead-card";

    const head = document.createElement("div");
    head.className = "lead-head";
    const name = document.createElement("strong");
    name.textContent = "#" + lead.id + " · " + (lead.name || "Клиент");
    const created = document.createElement("small");
    created.textContent = lead.createdAt
      ? new Date(lead.createdAt * 1000).toLocaleString("ru-RU")
      : "";
    head.append(name, created);

    const meta = document.createElement("div");
    meta.className = "lead-meta";
    meta.textContent = [
      lead.phone,
      lead.destination,
      lead.origin ? "из " + lead.origin : "",
      lead.dates,
      lead.budget ? formatRub(lead.budget) : "",
      lead.candidateCount ? "вариантов: " + lead.candidateCount : ""
    ].filter(Boolean).join(" · ");

    const controls = document.createElement("div");
    controls.className = "lead-controls";
    const select = document.createElement("select");
    Object.entries(STATUS_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === lead.status;
      select.append(option);
    });
    const followLabel = document.createElement("label");
    followLabel.className = "followup-label";
    followLabel.textContent = "Следующий контакт";
    const follow = document.createElement("input");
    follow.type = "date";
    follow.value = lead.followUpOn || "";
    followLabel.append(follow);

    const note = document.createElement("textarea");
    note.placeholder = "Заметка менеджера";
    note.value = lead.note || "";
    const save = document.createElement("button");
    save.className = "save-status";
    save.textContent = "Сохранить статус";
    save.addEventListener("click", () => updateLeadStatus(
      lead.id, select.value, note.value, follow.value, save
    ));
    controls.append(select, followLabel, note, save);

    card.append(head, meta, controls);
    box.append(card);
  });
}

async function loadLeads() {
  const state = await chrome.storage.local.get({ agentToken: "" });
  if (!state.agentToken) {
    renderLeads([]);
    return;
  }
  const response = await fetch(LEADS_API + "?limit=30", {
    headers: { "Authorization": "Bearer " + state.agentToken }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || ("HTTP " + response.status));
  }
  renderLeads(data.leads || []);
}

async function updateLeadStatus(leadId, status, note, followUpOn, button) {
  const state = await chrome.storage.local.get({ agentToken: "" });
  if (!state.agentToken) return setStatus("Сначала сохрани Agent token.");
  button.disabled = true;
  try {
    const response = await fetch(STATUS_API, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + state.agentToken
      },
      body: JSON.stringify({ leadId, status, note, followUpOn })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || ("HTTP " + response.status));
    }
    setStatus("Статус заявки #" + leadId + " сохранён.", true);
    await loadLeads();
  } catch (error) {
    setStatus("Статус не сохранён: " + (error.message || "ошибка"));
  } finally {
    button.disabled = false;
  }
}

async function exportLeads() {
  const state = await chrome.storage.local.get({ agentToken: "" });
  if (!state.agentToken) return setStatus("Сначала сохрани Agent token.");
  try {
    const response = await fetch(EXPORT_API, {
      headers: { "Authorization": "Bearer " + state.agentToken }
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || ("HTTP " + response.status));
    }
    const text = await response.text();
    const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "turbot-leads-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus("CSV выгружен.", true);
  } catch (error) {
    setStatus("CSV не выгружен: " + (error.message || "ошибка"));
  }
}


document.querySelectorAll("[data-open]").forEach((button) => {
  button.addEventListener("click", () => chrome.tabs.create({ url: button.dataset.open }));
});

$("capture").addEventListener("click", captureCurrentPage);
$("clearCandidates").addEventListener("click", async () => {
  await chrome.storage.local.set({ candidates: [] });
  renderCandidates([]);
});
$("saveToken").addEventListener("click", async () => {
  await chrome.storage.local.set({ agentToken: $("token").value.trim() });
  setStatus("Токен сохранён локально.", true);
  await loadLeads().catch((error) => setStatus("CRM: " + error.message));
});
$("refreshLeads").addEventListener("click", () => {
  loadLeads().catch((error) => setStatus("CRM: " + error.message));
});
$("exportLeads").addEventListener("click", exportLeads);
$("send").addEventListener("click", sendLead);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.candidates) {
    renderCandidates(changes.candidates.newValue || []);
  }
});

load();
