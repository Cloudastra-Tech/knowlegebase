// The few chrome.* APIs the extension uses, typed by hand (the full @types/chrome
// isn't a dependency of this repo). Shapes from developer.chrome.com/docs/extensions/reference/api.

declare namespace chrome {
  namespace runtime {
    const id: string
    const lastError: { message?: string } | undefined
    const onInstalled: { addListener(cb: () => void): void }
    function openOptionsPage(): Promise<void>
  }
  namespace contextMenus {
    interface OnClickData {
      menuItemId: string | number
      selectionText?: string
    }
    function create(props: { id: string; title: string; contexts: string[] }): void
    function removeAll(): Promise<void>
    const onClicked: {
      addListener(cb: (info: OnClickData, tab?: { id?: number; windowId?: number }) => void): void
    }
  }
  namespace sidePanel {
    function setPanelBehavior(behavior: { openPanelOnActionClick: boolean }): Promise<void>
    function open(options: { windowId?: number; tabId?: number }): Promise<void>
  }
  namespace storage {
    interface Area {
      get(keys: string | string[]): Promise<Record<string, unknown>>
      set(items: Record<string, unknown>): Promise<void>
      remove(keys: string | string[]): Promise<void>
    }
    const local: Area
    const session: Area
    const onChanged: {
      addListener(
        cb: (changes: Record<string, { oldValue?: unknown; newValue?: unknown }>, area: string) => void,
      ): void
      removeListener(
        cb: (changes: Record<string, { oldValue?: unknown; newValue?: unknown }>, area: string) => void,
      ): void
    }
  }
  namespace identity {
    function getRedirectURL(path?: string): string
    function launchWebAuthFlow(details: { url: string; interactive: boolean }): Promise<string>
  }
  namespace permissions {
    function request(permissions: { origins: string[] }): Promise<boolean>
    function contains(permissions: { origins: string[] }): Promise<boolean>
  }
}

// Stylesheets are imported for their side effect (Vite bundles them).
declare module "*.css"
