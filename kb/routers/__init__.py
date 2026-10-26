"""
The API's endpoints, one module per area. kb/app.py includes them all.

MULTI-TENANT: every user belongs to a workspace (company). Documents, the search
index, integrations and index status are all per workspace (workspaces.py), so
one company never sees another company's data.

Endpoints (all under /api, all except sign-in need "Authorization: Bearer <token>"):
  auth.py
    GET    /auth/providers        which sign-in options the login page shows (email_sso:
                                  ask for an email first, as some workspace has its own provider)
    POST   /auth/login            {username, password} -> {token, user}
    POST   /auth/sso/exchange     {code} -> {token, user}   the one-time code from the callback
    POST   /auth/logout           -> {logout_url}: also sign out at the provider, if it's set to
    POST   /auth/logout-everywhere  end all of this person's sessions, on every device
    GET    /auth/me
  sso.py
    GET    /auth/sso/{provider}/start      send the browser to Google / Microsoft
    GET    /auth/sso/{provider}/callback   they send it back here (sso.py decides who gets in)
    POST   /auth/sso/discover     {email} -> {connections} the workspace's own sign-in providers
    GET    /auth/sso/c/{id}/start | /callback   the same, through one of those providers
  saml.py
    GET    /auth/saml/{id}/metadata | /start  POST /acs  GET /done   ... when it's SAML
  signup.py
    GET    /auth/signup/google/start  {name, code}: a new company creates its workspace
  chat.py
    POST   /chat                  {question, role} -> {answer, sources, steps}
    POST   /chat/stream           same, streamed live (steps + answer tokens) as SSE
    GET    /passages/{chunk_id}   a cited passage and the text around it (role-checked)
  docs.py
    GET    /docs                  list documents                       (HR / Admin)
    GET    /docs/{access}/{name}  text of a .txt, or the file itself    (HR / Admin)
    POST   /docs                  {name, access, content} new .txt      (HR / Admin)
    PUT    /docs/{access}/{name}  {content} save a .txt                 (HR / Admin)
    DELETE /docs/{access}/{name}                                        (HR / Admin)
    POST   /docs/upload           multipart: files[], access            (HR / Admin)
  index.py
    POST   /reindex               rebuild the search index              (HR / Admin)
    GET    /index/status          background re-index progress
  knowledge.py
    GET    /knowledge             everything the assistant can search (uploads + synced files)
  integrations.py
    GET    /integrations          what this workspace has connected
    POST   /integrations/{provider}/connect     -> {url} of the consent screen  (HR / Admin)
    GET    /integrations/{provider}/callback    the provider redirects back here
    GET    /integrations/google_drive/folders   browse folders to pick         (HR / Admin)
    PUT    /integrations/google_drive/folders   {folders} choose what to sync  (HR / Admin)
    PUT    /integrations/{provider}/access      {access: restricted|everyone}
                                  who may ask about its files, then re-index  (HR / Admin)
    POST   /integrations/{provider}/sync        sync now                      (HR / Admin)
    DELETE /integrations/{provider}             disconnect + delete copies    (HR / Admin)
  ({provider} = google_drive | notion)
  admin_sign_in.py
    GET    /admin/sign-in         this workspace's domains and sign-in providers  (admin)
    GET    /admin/sign-in/new-provider  an ID and our URLs for a provider about to be added
    PUT    /admin/sign-in/policy  {require_sso}: passwords only for admins           (admin)
    PUT    /admin/sign-in/group-admin-days {days}: how long admin from groups lasts (admin)
    POST   /admin/sign-in/domains {domain} -> the DNS record that proves it      (admin)
    POST   /admin/sign-in/domains/{domain}/verify   check the DNS record          (admin)
    DELETE /admin/sign-in/domains/{domain}                                         (admin)
    POST   /admin/sign-in/connections {label, issuer, client_id, client_secret}    (admin)
    PATCH  /admin/sign-in/connections/{id} change any of those, or {enabled}       (admin)
    POST   /admin/sign-in/saml-connections {label, metadata_url | metadata_xml |
                                  entity_id + sso_url + certificate, email_attribute} (admin)
    PATCH  /admin/sign-in/saml-connections/{id} change any of those, or {enabled}  (admin)
    PUT    /admin/sign-in/connections/{id}/roles {group_claim, rules: [{group, role}]}
                                  roles from the groups a provider sends            (admin)
    POST   /admin/sign-in/connections/{id}/test -> {ticket, url}: "Test sign-in"   (admin)
    GET    /admin/sign-in/tests/{ticket}   that test's result                       (admin)
    DELETE /admin/sign-in/connections/{id}                                         (admin)
  admin_members.py
    GET    /admin/members         everyone in this workspace, with where their role comes from
    PATCH  /admin/members/{username} {role: public|hr|admin|null, disabled}       (admin)
    DELETE /admin/members/{username}                                               (admin)
    POST   /admin/members/{username}/sign-out   end all their sessions                (admin)
  invites.py
    GET    /admin/members/invites invites not yet accepted or revoked              (admin)
    POST   /admin/members/invite  {email, role}: email someone a link to join      (admin)
    POST   /admin/members/invites/{id}/resend   a new link, emailed again          (admin)
    DELETE /admin/members/invites/{id}          withdraw it                        (admin)
    POST   /auth/invite/lookup    {token} -> whose invite, to which workspace, how they'll sign in
    POST   /auth/invite/accept    {token, name, password} -> {token, user} | {sso, email}
  audit.py
    GET    /admin/audit           ?actor&action&from&to&cursor: the audit log, newest first (admin)
    GET    /admin/audit/export    ?format=csv|jsonl (+ the filters), streamed           (admin)
    GET    /admin/audit/verify    check the hash chain                                  (admin)
  health.py
    GET    /health                the process is up (Docker's HEALTHCHECK)
    GET    /health/ready          can it serve: database, index, key, guards (503 if not)

Chat, uploads, re-index and sync now are rate-limited per person and per workspace
(kb/rate_limit.py); request bodies and uploaded files are size-capped
(middleware.BodyLimit, upload_docs); every response carries security headers
(middleware.SecurityHeaders). Every document change re-indexes automatically;
connectors re-sync every 30 minutes; SAML providers set up from a metadata URL
fetch it again daily (or as it says). FastAPI's own /docs, /redoc and
/openapi.json are only on when settings.api_docs_on().

tests/routes.txt is the full list (method, path, endpoint, guard);
tests/test_routes.py fails when it changes, so a change to it is deliberate.
"""
