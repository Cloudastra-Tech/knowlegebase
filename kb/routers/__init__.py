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
  conversations.py
    GET    /conversations         your saved chats and folders
    GET    /conversations/search  ?q: your chats by title, question or answer (role-checked)
    POST   /conversations         a new chat
    GET    /conversations/{id}    its messages as shown, each with its versions
    PATCH  /conversations/{id}    {title, pinned, folder_id, active_leaf_id}
    DELETE /conversations/{id}
    POST   /conversations/{id}/fork  {message_id}: "Branch in new chat"
    GET/POST /conversation-folders, PATCH/DELETE /conversation-folders/{id}
    GET    /messages/{id}/markdown  an answer with its citations as footnotes (Copy)
    PUT/DELETE /messages/{id}/feedback   thumbs up / down
    GET    /admin/feedback        the workspace's feedback                 (HR / Admin)
    GET    /chat-settings         how long chats are kept, sharing on or off
    PUT    /admin/chat-settings   {retention_days: 30|90|365|null, sharing_enabled} (admin)
  shares.py
    POST   /shares                {conversation_id, message_id?, expires_in_days} -> the link
    GET    /shares                ?conversation_id: your links to it that still work
    DELETE /shares/{id}           revoke a link
    GET    /shared/{token}        open a link (same workspace; answers role-checked)
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
  insights.py
    GET    /question-log/settings  how questions are logged here: {mode, days, logged}
    PUT    /admin/question-log/settings {mode: full|redacted|metadata, days}     (admin)
    GET    /admin/insights        ?from&to (YYYY-MM-DD): usage, outcomes, latency, cost,
                                  sources, content gaps, connector health      (HR / Admin)
    GET    /admin/insights/export ?table&from&to: one of its tables as CSV    (HR / Admin)
    GET    /admin/eval-cases      answers promoted to the test set            (HR / Admin)
    POST   /admin/eval-cases      {message_id, expected_answer}: promote a rated answer (admin)
    DELETE /admin/eval-cases/{id}                                              (admin)
  scim.py      (SCIM 2.0: "Authorization: Bearer <the workspace's SCIM token>", not a session)
    /scim/v2/ServiceProviderConfig | Schemas | ResourceTypes | Users | Groups
                                  an identity provider provisions members and groups
    GET    /admin/sign-in/scim    base URL, tokens, last sync, rules, SCIM events     (admin)
    POST   /admin/sign-in/scim/tokens       a new token, shown once                  (admin)
    DELETE /admin/sign-in/scim/tokens/{id}  revoke it                               (admin)
    PUT    /admin/sign-in/scim/roles {rules, make_role_source}: roles from SCIM's groups (admin)
  health.py
    GET    /health                the process is up (Docker's HEALTHCHECK)
    GET    /health/ready          can it serve: database, index, key, guards (503 if not)
  api_keys.py
    GET    /me/api-keys           my API keys, the scopes I may give them, the API / MCP URLs
    POST   /me/api-keys           {name, scopes, days} -> the key, shown once
    DELETE /me/api-keys/{id}      revoke one of mine
    GET    /admin/api             API and MCP settings, every key in the workspace     (admin)
    PUT    /admin/api             {api_enabled, mcp_enabled, mcp_tools}               (admin)
    POST   /admin/api/service-keys  a key acting as a public user, shown once         (admin)
    DELETE /admin/api/keys/{id}   revoke any key in the workspace                    (admin)
  api_v1.py    (the public API: "Authorization: Bearer kb_live_...", an API key, not a session)
    POST   /v1/ask | /v1/search   GET /v1/sources | /v1/audit | /v1/openapi.json
    GET | POST | DELETE /v1/documents ...   see the module; the API we keep stable
And /mcp (kb/mcp_server.py, outside /api): the MCP server, with personal API keys.

Chat, uploads, re-index and sync now are rate-limited per person and per workspace
(kb/rate_limit.py); request bodies and uploaded files are size-capped
(middleware.BodyLimit, upload_docs); every response carries security headers
(middleware.SecurityHeaders). Every document change re-indexes automatically;
connectors re-sync every 30 minutes; SAML providers set up from a metadata URL
fetch it again daily (or as it says). FastAPI's own /docs, /redoc and
/openapi.json are only on when settings.api_docs_on(); /api/v1/openapi.json
(the public API's alone) is always there.

tests/routes.txt is the full list (method, path, endpoint, guard);
tests/test_routes.py fails when it changes, so a change to it is deliberate.
"""
