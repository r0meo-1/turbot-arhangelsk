const API = "https://bot.r0meo1.ru/agent-extension/lead";
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
  } catch (error) {
    setStatus("Не отправлено: " + (error.message || "ошибка"));
  } finally {
    $("send").disabled = false;
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
});
$("send").addEventListener("click", sendLead);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.candidates) {
    renderCandidates(changes.candidates.newValue || []);
  }
});

load();
