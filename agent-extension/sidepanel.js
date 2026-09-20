const API = "https://bot.r0meo1.ru/agent-extension/lead";
const LEADS_API = "https://bot.r0meo1.ru/agent-extension/leads";
const STATUS_API = "https://bot.r0meo1.ru/agent-extension/status";
const EXPORT_API = "https://bot.r0meo1.ru/agent-extension/export.csv";
const CRM_TODAY_API = "https://bot.r0meo1.ru/agent-extension/crm/today";
const CRM_TIMELINE_API = "https://bot.r0meo1.ru/agent-extension/crm/timeline";
const CRM_QUOTE_API = "https://bot.r0meo1.ru/agent-extension/crm/quote";
const CRM_QUOTE_REACTION_API = "https://bot.r0meo1.ru/agent-extension/crm/quote-reaction";
const CRM_TASK_API = "https://bot.r0meo1.ru/agent-extension/crm/task";
const CRM_TASK_STATUS_API = "https://bot.r0meo1.ru/agent-extension/crm/task-status";
const CRM_ACTIVITY_API = "https://bot.r0meo1.ru/agent-extension/crm/activity";
const CRM_OUTCOME_API = "https://bot.r0meo1.ru/agent-extension/crm/outcome";
const fieldIds = ["name","phone","destination","origin","dates","people","budget","consent"];

const $ = (id) => document.getElementById(id);

const SENSITIVE_QUERY_KEYS = new Set([
  "token", "accesstoken", "authtoken", "auth", "authorization",
  "session", "sessionid", "key", "apikey", "secret", "clientsecret",
  "password", "passwd", "signature", "sign", "code",
  "authorizationcode", "oauthcode"
]);

function normalizeQueryKey(key) {
  return String(key || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function sanitizeCandidateUrl(value) {
  if (!value) return "";
  let parsed;
  try {
    parsed = new URL(String(value));
  } catch (_) {
    return "";
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return "";
  if (!parsed.hostname || parsed.username || parsed.password) return "";

  parsed.hash = "";
  for (const key of [...parsed.searchParams.keys()]) {
    if (SENSITIVE_QUERY_KEYS.has(normalizeQueryKey(key))) {
      parsed.searchParams.delete(key);
    }
  }
  return parsed.toString();
}

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
    loadToday().catch(() => {});
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
    url.textContent = sanitizeCandidateUrl(item.url) || "";
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
    url: sanitizeCandidateUrl(tab.url),
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
      .filter((item) => item && typeof item === "object")
      .map((item) => ({ ...item, url: sanitizeCandidateUrl(item.url) }))
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

const TASK_LABELS = {
  build_selection: "Сделать подбор",
  send_options: "Отправить варианты",
  call_back: "Перезвонить",
  check_price: "Проверить цену",
  flights: "Авиабилеты",
  visa: "Виза",
  documents: "Документы",
  next_contact: "Следующий контакт"
};

const REACTION_LABELS = {
  draft: "Черновик",
  sent: "Отправлено",
  viewed: "Посмотрел",
  too_expensive: "Дорого",
  thinking: "Думает",
  wants_alternative: "Нужен другой вариант",
  accepted: "Согласен",
  rejected: "Отказ"
};

function authHeaders(token, json=false) {
  const headers = { "Authorization": "Bearer " + token };
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

async function crmFetch(url, options={}) {
  const state = await chrome.storage.local.get({ agentToken: "" });
  if (!state.agentToken) throw new Error("Сначала сохрани Agent token.");
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...authHeaders(state.agentToken, Boolean(options.body))
    }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || ("HTTP " + response.status));
  }
  return data;
}

function fmtDateTime(value) {
  if (!value) return "";
  const date = new Date(String(value).endsWith("Z") ? value : value + "Z");
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU");
}

function requestMeta(req={}) {
  const people = [];
  if (req.adults) people.push(req.adults + " взр.");
  if (Array.isArray(req.childAges) && req.childAges.length) {
    people.push("дети " + req.childAges.join(", "));
  }
  return [
    req.name || "",
    req.phone || "",
    req.destination || "",
    req.origin ? "из " + req.origin : "",
    req.dates || "",
    people.join(" + "),
    req.budget ? formatRub(req.budget) : "",
    req.sourceTag ? "source: " + req.sourceTag : ""
  ].filter(Boolean).join(" · ");
}

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


function renderToday(tasks) {
  const box = $("todayTasks");
  box.textContent = "";
  $("todayEmpty").hidden = Boolean(tasks.length);

  tasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "today-task";

    const head = document.createElement("div");
    head.className = "lead-head";
    const title = document.createElement("strong");
    title.textContent = TASK_LABELS[task.type] || task.type;
    const due = document.createElement("small");
    due.textContent = "до " + fmtDateTime(task.dueAt);
    head.append(title, due);

    const meta = document.createElement("div");
    meta.className = "lead-meta";
    meta.textContent = requestMeta(task.request || {});

    const note = document.createElement("p");
    note.className = "task-note";
    note.textContent = task.note || "";

    const actions = document.createElement("div");
    actions.className = "task-actions";
    const open = document.createElement("button");
    open.className = "secondary";
    open.textContent = "История";
    open.addEventListener("click", async () => {
      $("crmRequestId").value = task.requestId;
      await loadTimeline(task.requestId);
    });
    const done = document.createElement("button");
    done.className = "secondary";
    done.textContent = "Готово";
    done.addEventListener("click", async () => {
      done.disabled = true;
      try {
        await crmFetch(CRM_TASK_STATUS_API, {
          method: "POST",
          body: JSON.stringify({ taskId: task.taskId, status: "done" })
        });
        setStatus("Задача закрыта.", true);
        await loadToday();
        if ($("crmRequestId").value === task.requestId) {
          await loadTimeline(task.requestId);
        }
      } catch (error) {
        setStatus("Задача не обновлена: " + (error.message || "ошибка"));
      } finally {
        done.disabled = false;
      }
    });
    actions.append(open, done);
    card.append(head, meta, note, actions);
    box.append(card);
  });
}

async function loadToday() {
  const data = await crmFetch(CRM_TODAY_API + "?limit=50");
  renderToday(data.tasks || []);
}

function renderTimeline(timeline) {
  const request = timeline.request || {};
  const client = timeline.client || {};
  $("timelineSummary").textContent = [
    client.name || "",
    client.phone || "",
    request.primary_destination || "",
    request.departure_city ? "из " + request.departure_city : "",
    request.dates_text || [request.date_from, request.date_to].filter(Boolean).join(" – "),
    request.budget_amount ? formatRub(request.budget_amount) : "",
    request.attribution?.source_tag ? "source: " + request.attribution.source_tag : ""
  ].filter(Boolean).join(" · ");

  const quoteBox = $("quoteHistory");
  quoteBox.textContent = "";
  const quotes = timeline.quotes || [];
  if (!quotes.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Предложений пока нет.";
    quoteBox.append(empty);
  }
  quotes.slice().reverse().forEach((quote) => {
    const item = document.createElement("article");
    item.className = "quote-item";
    const title = document.createElement("strong");
    title.textContent = quote.hotel || "Отель";
    const meta = document.createElement("div");
    meta.className = "lead-meta";
    meta.textContent = [
      quote.operator || "",
      quote.carrier || "",
      quote.meal_plan || "",
      quote.price_amount ? formatRub(quote.price_amount) : "",
      fmtDateTime(quote.calculated_at)
    ].filter(Boolean).join(" · ");

    const reaction = document.createElement("select");
    Object.entries(REACTION_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === quote.reaction;
      reaction.append(option);
    });
    reaction.addEventListener("change", async () => {
      try {
        await crmFetch(CRM_QUOTE_REACTION_API, {
          method: "POST",
          body: JSON.stringify({ quoteId: quote.quote_id, reaction: reaction.value })
        });
        setStatus("Реакция клиента сохранена.", true);
        await loadTimeline(request.request_id);
      } catch (error) {
        setStatus("Реакция не сохранена: " + (error.message || "ошибка"));
      }
    });

    item.append(title, meta, reaction);
    quoteBox.append(item);
  });

  const last = timeline.lastContact;
  $("lastContact").textContent = last
    ? (fmtDateTime(last.created_at) + " · " + last.type + " · " + last.summary)
    : "Нет записи.";

  const outcome = timeline.outcome || null;
  if (outcome) {
    $("outcomeStatus").value = outcome.status || "paused";
    $("outcomeReason").value = outcome.reason || "";
  }
}

async function loadTimeline(requestId=null) {
  const id = String(requestId || $("crmRequestId").value || "").trim();
  if (!id) return setStatus("Укажи ID заявки.");
  $("crmRequestId").value = id;
  const data = await crmFetch(CRM_TIMELINE_API + "?requestId=" + encodeURIComponent(id));
  renderTimeline(data.timeline || {});
}

async function addQuote() {
  const requestId = $("crmRequestId").value.trim();
  const hotel = $("quoteHotel").value.trim();
  const priceAmount = Number($("quotePrice").value || 0);
  if (!requestId || !hotel) return setStatus("Для предложения нужны ID заявки и отель.");
  try {
    await crmFetch(CRM_QUOTE_API, {
      method: "POST",
      body: JSON.stringify({
        requestId,
        hotel,
        priceAmount,
        operator: $("quoteOperator").value.trim(),
        carrier: $("quoteCarrier").value.trim(),
        mealPlan: $("quoteMeal").value.trim(),
        reaction: $("quoteReaction").value,
        currency: "RUB"
      })
    });
    ["quoteHotel","quotePrice","quoteOperator","quoteCarrier","quoteMeal"].forEach((id) => $(id).value = "");
    setStatus("Предложение добавлено в историю.", true);
    await loadTimeline(requestId);
  } catch (error) {
    setStatus("Предложение не добавлено: " + (error.message || "ошибка"));
  }
}

async function addActivity() {
  const requestId = $("crmRequestId").value.trim();
  const summary = $("activitySummary").value.trim();
  if (!requestId || !summary) return setStatus("Нужны ID заявки и результат контакта.");
  try {
    await crmFetch(CRM_ACTIVITY_API, {
      method: "POST",
      body: JSON.stringify({
        requestId,
        type: $("activityType").value,
        summary
      })
    });
    $("activitySummary").value = "";
    setStatus("Контакт записан.", true);
    await loadTimeline(requestId);
  } catch (error) {
    setStatus("Контакт не записан: " + (error.message || "ошибка"));
  }
}

async function addTask() {
  const requestId = $("crmRequestId").value.trim();
  const dueLocal = $("taskDueAt").value;
  if (!requestId || !dueLocal) return setStatus("Нужны ID заявки и время задачи.");
  try {
    await crmFetch(CRM_TASK_API, {
      method: "POST",
      body: JSON.stringify({
        requestId,
        type: $("taskType").value,
        dueAt: new Date(dueLocal).toISOString(),
        priority: Number($("taskPriority").value || 3),
        note: $("taskNote").value.trim()
      })
    });
    $("taskNote").value = "";
    setStatus("Задача добавлена.", true);
    await loadToday();
    await loadTimeline(requestId);
  } catch (error) {
    setStatus("Задача не добавлена: " + (error.message || "ошибка"));
  }
}

async function saveOutcome() {
  const requestId = $("crmRequestId").value.trim();
  if (!requestId) return setStatus("Укажи ID заявки.");
  try {
    await crmFetch(CRM_OUTCOME_API, {
      method: "POST",
      body: JSON.stringify({
        requestId,
        status: $("outcomeStatus").value,
        reason: $("outcomeReason").value.trim()
      })
    });
    setStatus("Результат заявки сохранён.", true);
    await loadTimeline(requestId);
  } catch (error) {
    setStatus("Результат не сохранён: " + (error.message || "ошибка"));
  }
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
  await loadToday().catch((error) => setStatus("Сегодня: " + error.message));
});
$("refreshLeads").addEventListener("click", () => {
  loadLeads().catch((error) => setStatus("CRM: " + error.message));
});
$("refreshToday").addEventListener("click", () => {
  loadToday().catch((error) => setStatus("Сегодня: " + error.message));
});
$("loadTimeline").addEventListener("click", () => {
  loadTimeline().catch((error) => setStatus("История: " + error.message));
});
$("addQuote").addEventListener("click", addQuote);
$("addActivity").addEventListener("click", addActivity);
$("addTask").addEventListener("click", addTask);
$("saveOutcome").addEventListener("click", saveOutcome);
$("exportLeads").addEventListener("click", exportLeads);
$("send").addEventListener("click", sendLead);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.candidates) {
    renderCandidates(changes.candidates.newValue || []);
  }
});

load();
