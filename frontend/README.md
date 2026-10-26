# Knowledge assistant — website

The React site for the knowledge assistant: landing page, sign-in, chat, and the
HR / Admin screens (Documents, Integrations). It talks only to the FastAPI backend
(`../api.py`); all data and every permission check live there.

Built with React 19, TypeScript, Vite and [shadcn/ui](https://ui.shadcn.com)
(Tailwind v4). Needs Node.js 20+.

## Run it

```bash
# terminal 1, in the project root: the API on :8010
uvicorn api:app --reload --port 8010

# terminal 2, here
npm install
npm run dev            # http://localhost:5173
```

Vite proxies `/api` to `127.0.0.1:8010` and keeps the `Host` header, so OAuth
redirects (Google / Microsoft sign-in, Google Drive) come back to the dev site.
Use the same host everywhere (`localhost`, not `127.0.0.1`), or sign-in cookies
won't match.

## Build and check

```bash
npm run lint           # oxlint
npm test               # Vitest + Testing Library (jsdom, a faked API)
npm run build          # type-check + production build into dist/
```

In Docker, the build in `dist/` is served by `api.py` itself, so one container
serves both the site and the API; every screen's URL gets `index.html`, so deep
links and reloads work. CI runs lint, tests and build on every PR.

## Where things are

| Path | What |
|---|---|
| `src/routes.tsx` | Every URL (React Router) and what it shows (`src/screens.tsx`) |
| `src/App.tsx` | The signed-in shell: navbar, the chat (kept mounted), the screen below |
| `src/components/auth-root.tsx` | Who is signed in: session restore, 401 / role changes |
| `src/lib/api/` | The only place that calls the API, one file per domain; holds the session token |
| `src/lib/queries.ts` | Server data the screens share, cached by TanStack Query |
| `src/components/` | One file per screen (`chat-view`, `docs-view`, `integrations-view`, …) |
| `src/components/ui/` | shadcn/ui components — add more with `npx shadcn add <name>` |

## Conventions

- Use the shadcn components in `src/components/ui/` for every control
  (`Input`, `Select`, `Textarea`, …), never raw `<input>` / `<select>`.
- The UI hides what a role can't use, but never enforces it: the API checks the
  account's role on every request. An HR account's **View as** menu only lowers
  what's shown.
- Every screen has a URL (`/chat`, `/documents`, `/admin/members`, …; list in
  `src/routes.tsx`). The server still sends people back to the old hash
  addresses (`/#login?error=…`, `/#sso?code=…`, `/#integrations?connected=…`);
  `src/lib/legacy-hash.ts` redirects them.
- Read server data with `useQuery(queries.…())`; after a change, invalidate its
  key instead of keeping a copy in state.
