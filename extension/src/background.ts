// The extension's service worker: the toolbar button opens the side panel, and the
// "Ask Knowledge about the selection" menu item hands the selected text to it.
//
// The page itself is never read: no content scripts, no host access to the tab.
// Chrome gives the menu click the selected text (contextMenus OnClickData.selectionText,
// which needs no host permission), and it goes to the panel through
// chrome.storage.session, which only the extension's own pages can read.

import { MAX_CONTEXT } from "./api"

const MENU = "kb-ask-selection"

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU,
    title: "Ask Knowledge about the selection",
    contexts: ["selection"],
  })
})

void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU || !info.selectionText) return
  // Opened first, while it's still the click's doing (sidePanel.open needs a user action).
  if (tab?.windowId !== undefined) void chrome.sidePanel.open({ windowId: tab.windowId })
  void chrome.storage.session.set({
    selection: { text: info.selectionText.slice(0, MAX_CONTEXT), at: Date.now() },
  })
})
