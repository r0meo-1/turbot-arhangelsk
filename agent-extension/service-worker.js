const MENU_ID = "turbot-add-selection";

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

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Добавить в TurBot Agent Desk",
    contexts: ["selection", "page", "link"]
  });

  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => {});
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID) return;

  const candidate = {
    id: crypto.randomUUID(),
    title: (tab && tab.title) || "Тур",
    url: sanitizeCandidateUrl(info.linkUrl || info.pageUrl || (tab && tab.url) || ""),
    selection: (info.selectionText || "").trim(),
    createdAt: Date.now()
  };

  const state = await chrome.storage.local.get({ candidates: [] });
  const candidates = Array.isArray(state.candidates) ? state.candidates : [];
  candidates.unshift(candidate);
  await chrome.storage.local.set({ candidates: candidates.slice(0, 30) });

  if (tab && tab.windowId) {
    chrome.sidePanel.open({ windowId: tab.windowId }).catch(() => {});
  }
});
