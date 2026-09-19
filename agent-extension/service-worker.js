const MENU_ID = "turbot-add-selection";

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
    url: info.linkUrl || info.pageUrl || (tab && tab.url) || "",
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
