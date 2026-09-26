const API = "https://bot.r0meo1.ru/agent-extension/lead";
const LEADS_API = "https://bot.r0meo1.ru/agent-extension/leads";
const STATUS_API = "https://bot.r0meo1.ru/agent-extension/status";
const EXPORT_API = "https://bot.r0meo1.ru/agent-extension/export.csv";
const CRM_TODAY_API = "https://bot.r0meo1.ru/agent-extension/crm/today";
const CRM_TIMELINE_API = "https://bot.r0meo1.ru/agent-extension/crm/timeline";
const CRM_TASK_API = "https://bot.r0meo1.ru/agent-extension/crm/task";
const CRM_QUOTE_API = "https://bot.r0meo1.ru/agent-extension/crm/quote";
const CRM_REACTION_API = "https://bot.r0meo1.ru/agent-extension/crm/reaction";
const CRM_ACTIVITY_API = "https://bot.r0meo1.ru/agent-extension/crm/activity";
const CRM_OUTCOME_API = "https://bot.r0meo1.ru/agent-extension/crm/outcome";
let activeRequestId = "";
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
  const manifest = chrome.runtime.getManifest();
  $("versionBadge").textContent = "v" + manifest.version;

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

  if (!state.agentToken) {
    setConnectionState("Не подключено", "idle");
    return;
  }
  await refreshConnectionState();
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

function setConnectionState(text, state="idle") {
  const node = $("connectionState");
  node.textContent = text;
  node.className = "connection-state" + (state === "idle" ? "" : " " + state);
}

function connectionMessage(error) {
  const status = Number(error?.status || 0);
  if (status === 401 || status === 403) return "Привязка недействительна. Сохрани новый Agent token.";
  if (status >= 500) return "CRM временно недоступна.";
  return "Не удалось подключиться к CRM.";
}

async function refreshConnectionState() {
  setConnectionState("Проверяю подключение…", "checking");
  const results = await Promise.allSettled([loadLeads(), loadToday()]);
  const failed = results.find((item) => item.status === "rejected");
  if (failed) {
    const message = connectionMessage(failed.reason);
    setConnectionState(message, "bad");
    setStatus("CRM: " + message);
    return false;
  }
  setConnectionState("Подключено", "ok");
  return true;
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
    await refreshConnectionState();
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

function crmDate(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(raw);
  const date = new Date(hasZone ? raw : raw + "Z");
  return Number.isNaN(date.getTime()) ? null : date;
}

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
  accepted: "Подходит",
  rejected: "Отказ"
};

async function crmFetch(url, options={}) {
  const state = await chrome.storage.local.get({ agentToken: "" });
  if (!state.agentToken) {
    const error = new Error("Сначала сохрани Agent token.");
    error.status = 401;
    throw error;
  }
  const headers = {
    ...(options.headers || {}),
    "Authorization": "Bearer " + state.agentToken
  };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    const error = new Error(data.error || ("HTTP " + response.status));
    error.status = response.status;
    throw error;
  }
  return data;
}

function renderToday(tasks) {
  const box = $("todayTasks");
  box.textContent = "";
  $("todayEmpty").hidden = Boolean(tasks.length);

  tasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "task-card";

    const head = document.createElement("div");
    head.className = "lead-head";
    const title = document.createElement("strong");
    title.textContent = TASK_LABELS[task.type] || task.type;
    const due = document.createElement("small");
    const dueDate = crmDate(task.dueAt);
    due.textContent = dueDate ? dueDate.toLocaleString("ru-RU") : "";
    head.append(title, due);

    const meta = document.createElement("p");
    meta.className = "lead-meta";
    const party = task.childAges?.length
      ? task.adults + " взр. + дети " + task.childAges.join(", ")
      : task.adults + " взр.";
    meta.textContent = [
      task.destination || "Направление не указано",
      task.origin ? "из " + task.origin : "",
      task.dates,
      party,
      task.budget ? formatRub(task.budget) : "",
      task.channel ? task.channel + (task.sourceTag ? " / " + task.sourceTag : "") : "",
      task.note
    ].filter(Boolean).join(" · ");

    const actions = document.createElement("div");
    actions.className = "task-actions";
    const open = document.createElement("button");
    open.className = "secondary";
    open.textContent = "Открыть";
    open.addEventListener("click", () => openTimeline(task.requestId));
    const done = document.createElement("button");
    done.className = "secondary";
    done.textContent = "Готово";
    done.addEventListener("click", () => completeTask(task.taskId, task.requestId, done));
    actions.append(open, done);

    card.append(head, meta, actions);
    box.append(card);
  });
}

async function loadToday() {
  const now = new Date();
  const params = new URLSearchParams({
    now: now.toISOString(),
    tzOffsetMinutes: String(now.getTimezoneOffset()),
    limit: "100"
  });
  const data = await crmFetch(CRM_TODAY_API + "?" + params.toString());
  renderToday(data.tasks || []);
}

async function completeTask(taskId, requestId, button) {
  button.disabled = true;
  try {
    await crmFetch(CRM_TASK_API, {
      method: "POST",
      body: JSON.stringify({ taskId, requestId, status: "done" })
    });
    setStatus("Задача закрыта.", true);
    await loadToday();
    if (activeRequestId) await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Задача не закрыта: " + (error.message || "ошибка"));
  } finally {
    button.disabled = false;
  }
}

function latestReactionFor(quote, events) {
  const matches = (events || []).filter((item) => item.quote_id === quote.quote_id);
  return matches.length ? matches[matches.length - 1].reaction : quote.reaction;
}

function renderTimeline(timeline) {
  const trip = timeline.request || {};
  activeRequestId = trip.request_id || "";
  $("activeRequestId").textContent = activeRequestId;
  $("timelineEmpty").hidden = true;
  $("timeline").hidden = false;

  const childAges = (trip.children || []).map((item) => item.age);
  $("tripSummary").textContent = [
    trip.primary_destination || "Направление не указано",
    trip.departure_city ? "из " + trip.departure_city : "",
    trip.dates_text,
    trip.nights_min ? (
      trip.nights_min === trip.nights_max
        ? trip.nights_min + " ноч."
        : trip.nights_min + "–" + trip.nights_max + " ноч."
    ) : "",
    trip.adults ? trip.adults + " взр." : "",
    childAges.length ? "дети: " + childAges.join(", ") : "",
    trip.budget_amount ? formatRub(trip.budget_amount) : "",
    trip.attribution?.source_tag ? "src=" + trip.attribution.source_tag : ""
  ].filter(Boolean).join(" · ");

  const quoteBox = $("quoteHistory");
  quoteBox.textContent = "";
  const quotes = timeline.quotes || [];
  const reactions = timeline.quote_reactions || [];
  if (!quotes.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Предложений пока нет.";
    quoteBox.append(empty);
  }
  quotes.forEach((quote) => {
    const node = document.createElement("article");
    node.className = "quote-card";
    const title = document.createElement("strong");
    title.textContent = quote.hotel + " · " + formatRub(quote.price_amount);
    const meta = document.createElement("small");
    meta.textContent = [
      quote.operator,
      quote.carrier,
      quote.meal_plan,
      crmDate(quote.calculated_at)?.toLocaleString("ru-RU") || ""
    ].filter(Boolean).join(" · ");

    const reaction = document.createElement("select");
    const current = latestReactionFor(quote, reactions);
    Object.entries(REACTION_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === current;
      reaction.append(option);
    });
    const reactionNote = document.createElement("input");
    reactionNote.placeholder = "Комментарий к реакции";
    const saveReaction = document.createElement("button");
    saveReaction.className = "secondary";
    saveReaction.textContent = "Сохранить реакцию";
    saveReaction.addEventListener("click", () => addReaction(
      quote.quote_id, reaction.value, reactionNote.value, saveReaction
    ));

    node.append(title, meta, reaction, reactionNote, saveReaction);
    quoteBox.append(node);
  });

  const outcome = timeline.outcome || {};
  const allowedOutcomes = new Set(["paused", "won", "lost"]);
  $("outcomeStatus").value = allowedOutcomes.has(outcome.status) ? outcome.status : "paused";
  $("outcomeReason").value = String(outcome.reason || "").slice(0, 500);

  const activityBox = $("activityHistory");
  activityBox.textContent = "";
  const activities = [...(timeline.activities || [])].reverse().slice(0, 12);
  if (activities.length) {
    const heading = document.createElement("strong");
    heading.className = "activity-title";
    heading.textContent = "Последние контакты и изменения";
    activityBox.append(heading);
  }
  activities.forEach((activity) => {
    const row = document.createElement("p");
    row.className = "activity-row";
    row.textContent = [
      crmDate(activity.created_at)?.toLocaleString("ru-RU") || "",
      activity.summary
    ].filter(Boolean).join(" · ");
    activityBox.append(row);
  });
}

async function openTimeline(requestId) {
  const data = await crmFetch(
    CRM_TIMELINE_API + "?requestId=" + encodeURIComponent(requestId)
  );
  renderTimeline(data.timeline || {});
}

async function addQuote() {
  if (!activeRequestId) return setStatus("Сначала открой заявку.");
  const hotel = $("quoteHotel").value.trim();
  const priceAmount = Number($("quotePrice").value);
  if (!hotel || !Number.isFinite(priceAmount) || priceAmount <= 0) {
    return setStatus("Для предложения нужны отель и цена.");
  }
  try {
    await crmFetch(CRM_QUOTE_API, {
      method: "POST",
      body: JSON.stringify({
        requestId: activeRequestId,
        hotel,
        priceAmount,
        operator: $("quoteOperator").value.trim(),
        mealPlan: $("quoteMeal").value.trim(),
        currency: "RUB",
        reaction: "draft"
      })
    });
    $("quoteHotel").value = "";
    $("quotePrice").value = "";
    $("quoteOperator").value = "";
    $("quoteMeal").value = "";
    setStatus("Предложение добавлено в историю.", true);
    await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Предложение не сохранено: " + (error.message || "ошибка"));
  }
}

async function addReaction(quoteId, reaction, note, button) {
  button.disabled = true;
  try {
    await crmFetch(CRM_REACTION_API, {
      method: "POST",
      body: JSON.stringify({
        quoteId,
        requestId: activeRequestId,
        reaction,
        note
      })
    });
    setStatus("Реакция клиента сохранена.", true);
    await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Реакция не сохранена: " + (error.message || "ошибка"));
  } finally {
    button.disabled = false;
  }
}

async function addActivity() {
  if (!activeRequestId) return setStatus("Сначала открой заявку.");
  const summary = $("activitySummary").value.trim();
  if (!summary) return setStatus("Напиши результат контакта.");
  try {
    await crmFetch(CRM_ACTIVITY_API, {
      method: "POST",
      body: JSON.stringify({
        requestId: activeRequestId,
        type: "call",
        summary
      })
    });
    $("activitySummary").value = "";
    setStatus("Результат контакта записан.", true);
    await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Контакт не сохранён: " + (error.message || "ошибка"));
  }
}

async function saveOutcome() {
  if (!activeRequestId) return setStatus("Сначала открой заявку.");
  const status = $("outcomeStatus").value;
  const reason = $("outcomeReason").value.trim().slice(0, 500);
  if (!["paused", "won", "lost"].includes(status)) {
    return setStatus("Некорректный результат заявки.");
  }
  const button = $("saveOutcome");
  button.disabled = true;
  try {
    await crmFetch(CRM_OUTCOME_API, {
      method: "POST",
      body: JSON.stringify({
        requestId: activeRequestId,
        status,
        reason
      })
    });
    setStatus("Результат заявки сохранён.", true);
    await loadToday();
    await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Результат не сохранён: " + (error.message || "ошибка"));
  } finally {
    button.disabled = false;
  }
}

async function clearPairing() {
  await chrome.storage.local.remove("agentToken");
  $("token").value = "";
  activeRequestId = "";
  $("activeRequestId").textContent = "";
  $("timeline").hidden = true;
  $("timelineEmpty").hidden = false;
  renderLeads([]);
  renderToday([]);
  setConnectionState("Не подключено", "idle");
  setStatus("Локальная привязка очищена.", true);
}

async function addTask() {
  if (!activeRequestId) return setStatus("Сначала открой заявку.");
  const dueAt = $("taskDueAt").value;
  if (!dueAt) return setStatus("Укажи время задачи.");
  try {
    await crmFetch(CRM_TASK_API, {
      method: "POST",
      body: JSON.stringify({
        requestId: activeRequestId,
        type: $("taskType").value,
        dueAt: new Date(dueAt).toISOString(),
        priority: 2,
        note: $("taskNote").value.trim()
      })
    });
    $("taskNote").value = "";
    setStatus("Задача добавлена.", true);
    await loadToday();
    await openTimeline(activeRequestId);
  } catch (error) {
    setStatus("Задача не добавлена: " + (error.message || "ошибка"));
  }
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
  const data = await crmFetch(LEADS_API + "?limit=30");
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
    await loadToday().catch((error) => {
      setStatus("Очередь не обновлена: " + connectionMessage(error));
    });
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
  const token = $("token").value.trim();
  if (!token) {
    setConnectionState("Не подключено", "idle");
    return setStatus("Вставь Agent token.");
  }
  await chrome.storage.local.set({ agentToken: token });
  setStatus("Токен сохранён локально.", true);
  await refreshConnectionState();
});
$("clearToken").addEventListener("click", clearPairing);
$("refreshLeads").addEventListener("click", () => {
  loadLeads().catch((error) => setStatus("CRM: " + error.message));
});
$("refreshToday").addEventListener("click", () => {
  loadToday().catch((error) => setStatus("Очередь: " + error.message));
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

load().catch(() => {
  setConnectionState("Не удалось загрузить Agent Desk.", "bad");
  setStatus("Не удалось загрузить Agent Desk.");
});
