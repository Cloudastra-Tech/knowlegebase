# Knowledge assistant browser extension

A Chrome / Edge (Manifest V3) side panel that asks your Knowledge assistant from any tab
and shows cited answers, the same checked answers as the web chat (platform.md PL-24).

- **Never reads the page.** No content scripts and no access to the tabs you visit. To ask
  about some text, select it, right-click and choose **Ask Knowledge about the selection**:
  Chrome hands the extension the selected text (and nothing else), the side panel opens and
  shows it, and it goes with your next question only if **Use the selected text** stays
  ticked. The server gives it to the model as untrusted data, never as instructions, and
  doesn't save it.
- **Asks as you.** It signs in through your Knowledge assistant's own sign-in (SSO or
  password) and gets a personal API key that can only ask, for 90 days. Your role is
  checked on every question, it shows under **API keys** and **Integrations → Browser
  extension**, and **Disconnect** (in the panel or the app) revokes it.

## Build

The extension has no `node_modules` of its own: it builds with the web app's toolchain and
uses its shadcn components.

```bash
cd frontend
npm ci                      # once
npm run test:extension      # its unit tests (src/api.test.ts)
npm run build:extension     # type-check, then build into extension/dist
```

CI runs both on every PR (`.github/workflows/ci.yml`, the frontend job).

## Load it unpacked

**Chrome** (116 or later)

1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose the `extension/dist` folder.
3. Note the extension's **ID** (32 letters) on its card.

**Microsoft Edge** (116 or later)

1. Open `edge://extensions` and turn on **Developer mode** (left).
2. Click **Load unpacked** and choose the `extension/dist` folder.
3. Note the extension's **ID** on its card.

The ID of an unpacked extension comes from the folder's path, so it stays the same while
you load it from the same place.

## Let the server accept it

The server hands keys only to extensions it knows, and answers CORS on `/api/v1` only for
them. Set the extension's origin in the server's environment (`.env`) and restart it:

```bash
EXTENSION_ORIGINS=chrome-extension://<the 32-letter ID>
# several (Chrome and Edge builds, a store build and a dev build): comma-separated
```

Without it, **Connect** says the extension isn't set up on this server, and the
Integrations card shows **Not set up**.

## Connect and ask

1. Click the extension's toolbar button (pin it first from the puzzle-piece menu): the side
   panel opens.
2. Enter your Knowledge assistant's address (`https://…`; `http://localhost:…` only on your
   own machine) and click **Connect**. Chrome asks to let the extension reach that one
   address: allow it.
3. A window opens your Knowledge assistant's **Connect the browser extension?** screen (sign
   in first if asked). Click **Connect**; the window closes and the panel is ready.
4. Ask. Answers stream in with numbered sources; a source without a link of its own opens
   in the app's passage view, where your role is checked again.

## How signing in works (and why this way)

```
side panel ── chrome.identity.launchWebAuthFlow ──> https://<server>/extension/connect
                                                     ?redirect=https://<id>.chromiumapp.org/
web app (signed in) ── POST /api/extension/connect {redirect_uri} ──> {code}   (60 s, once)
web app ── navigates to https://<id>.chromiumapp.org/?code=… (the flow ends there)
side panel ── POST /api/v1/extension/exchange {code} ──> {key}   (a personal key, ask only)
side panel ── POST /api/v1/ask (stream: true), Authorization: Bearer <key>
```

- **A personal API key, not a session token.** `/api/v1` takes API keys only, and a key is
  what an always-on client should hold: scoped (`ask` only), expiring (90 days), revocable
  by its owner from the app, re-checked against the person's membership and role on every
  call, and its questions are logged as surface `extension`. A web session (12 hours, full
  access to the app) would be both shorter-lived and more powerful than the panel needs.
- **The key travels through a one-time code**, the `sso.issue_code` pattern: the code is in
  the redirect URL, valid for 60 seconds and once only, and the key itself is made when the
  code is swapped, so it never appears in a URL, history or log, and a code nobody redeems
  leaves no key behind.
- **Only our extension gets one.** The redirect must be `https://<id>.chromiumapp.org/` for
  an ID in `EXTENSION_ORIGINS`, and the swap is refused if it comes from another
  extension's origin.

Chrome references:

- Side panel (`side_panel.default_path`, `sidePanel` permission, Chrome 114+;
  `sidePanel.open` needs a user action, Chrome 116+):
  https://developer.chrome.com/docs/extensions/reference/api/sidePanel
- The selection without page access (`contextMenus`, context `selection`,
  `OnClickData.selectionText`): https://developer.chrome.com/docs/extensions/reference/api/contextMenus.
  `activeTab` isn't used: it's granted by an action click, a menu item, a shortcut or the
  omnibox, and the docs don't say a side panel interaction grants it
  (https://developer.chrome.com/docs/extensions/develop/concepts/activeTab).
- Sign-in (`identity.launchWebAuthFlow`, redirect `https://<id>.chromiumapp.org/`):
  https://developer.chrome.com/docs/extensions/reference/api/identity
- Default MV3 page CSP `script-src 'self'; object-src 'self'` (no remote code; the panel
  loads no web fonts): https://developer.chrome.com/docs/extensions/reference/manifest/content-security-policy
- Calling the server from an extension page with host permission:
  https://developer.chrome.com/docs/extensions/develop/concepts/network-requests. The
  extension asks for access to your server's address only, at Connect
  (`optional_host_permissions`), not to every site.
- Edge supports the same side panel API: https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/sidebar

## Testing it in a browser

Automated runs can load it: Chromium and Chrome for Testing still take
`--load-extension=<path>` (branded Chrome dropped the flag in 137:
https://developer.chrome.com/blog/extension-news-june-2025), with the new headless mode
(`--headless=new`; old headless can't load extensions:
https://developer.chrome.com/docs/extensions/how-to/test/end-to-end-testing).

## Files

- `public/manifest.json`: the manifest (copied into `dist` as it is), with placeholder
  icons in `public/icons`
- `sidepanel.html`, `src/sidepanel.tsx`: the panel (React, the app's shadcn components)
- `src/background.ts`: the service worker (the toolbar button and the menu item)
- `src/api.ts`: the calls to the server; `src/api.test.ts` its tests
- `../frontend/vite.extension.config.ts`: the build and test config
