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
npm run build          # type-check + production build into dist/
```

In Docker, the build in `dist/` is served by `api.py` itself, so one container
serves both the site and the API. CI runs lint and build on every PR.

## Where things are

| Path | What |
|---|---|
| `src/App.tsx` | The shell: session restore, sign-in return (`#sso?code=`), navbar, screens |
| `src/lib/api.ts` | The only place that calls the API; holds the session token |
| `src/components/` | One file per screen (`chat-view`, `docs-view`, `integrations-view`, …) |
| `src/components/ui/` | shadcn/ui components — add more with `npx shadcn add <name>` |

## Conventions

- Use the shadcn components in `src/components/ui/` for every control
  (`Input`, `Select`, `Textarea`, …), never raw `<input>` / `<select>`.
- The UI hides what a role can't use, but never enforces it: the API checks the
  account's role on every request. An HR account's **View as** menu only lowers
  what's shown.
- The site routes by hash (`#login`, `#integrations?connected=…`); read
  parameters with `hashParams()` from `src/lib/utils.ts`.
