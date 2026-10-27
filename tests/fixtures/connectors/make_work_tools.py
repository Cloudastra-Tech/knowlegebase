"""
Writes the recorded-HTTP fixtures of Asana, ClickUp, Coda, Microsoft Teams
messages, Gmail and Google Calendar (tests/fixtures/connectors/<vendor>/*.json).
Every shape is taken from the vendor's API reference, cited in each file's
"_source". Run from the repository root after changing a shape:

    python tests/fixtures/connectors/make_work_tools.py
"""

import json
import pathlib

OUT = pathlib.Path(__file__).parent


def ex(method, url, params=None, json_body=None, status=200, headers=None, match_json=None,
       repeat=False, text=None):
    req = {"method": method, "url": url}
    if params:
        req["params"] = params
    if match_json:
        req["json"] = match_json
    res = {"status": status}
    if json_body is not None:
        res["json"] = json_body
    if text is not None:
        res["text"] = text
    if headers:
        res["headers"] = headers
    e = {"request": req, "response": res}
    if repeat:
        e["repeat"] = True
    return e


def write(vendor, name, sources, exchanges):
    (OUT / vendor).mkdir(exist_ok=True)
    (OUT / vendor / f"{name}.json").write_text(
        json.dumps({"_source": sources, "exchanges": exchanges}, indent=1) + "\n")


# ---- Asana ---------------------------------------------------------------------------------
A = "https://app.asana.com/api/1.0"
ASANA_SRC = [
    "https://developers.asana.com/reference/getproject (gid, name, notes, permalink_url, "
    "archived, project_brief)",
    "https://developers.asana.com/reference/getmemberships (parent=<project>; data[].member "
    "{gid, resource_type user | team})",
    "https://developers.asana.com/reference/getuser (data.email)",
    "https://developers.asana.com/reference/gettasks (project, modified_since, opt_fields, "
    "limit, offset; next_page.offset)",
    "https://developers.asana.com/reference/getstoriesfortask (resource_subtype comment_added, "
    "text, created_at, created_by.name)",
    "https://developers.asana.com/reference/getprojectbrief (title, text, permalink_url)",
    "https://developers.asana.com/reference/getevents (412 with a new sync token; data[] "
    "{action, resource, parent}; has_more)"]
PROJECT = {"gid": "111", "name": "Handbook", "notes": "Where the team keeps its policies.",
           "permalink_url": "https://app.asana.com/0/111/list", "archived": False,
           "modified_at": "2026-10-10T10:00:00.000Z", "project_brief": {"gid": "b1"}}
A_MEMBERS = ex("GET", f"{A}/memberships", {"parent": "111"}, {"data": [
    {"gid": "m1", "member": {"gid": "u1", "resource_type": "user", "name": "Ann"}},
    {"gid": "m2", "member": {"gid": "u2", "resource_type": "user", "name": "Bob"}},
    {"gid": "m3", "member": {"gid": "t9", "resource_type": "team", "name": "Everyone"}}],
    "next_page": None})
A_BRIEF = ex("GET", f"{A}/project_briefs/b1", None, {"data": {
    "gid": "b1", "title": "Why this project", "text": "One place for every policy.",
    "permalink_url": "https://app.asana.com/0/111/brief"}})
T1 = {"gid": "t1", "name": "Rotate the API keys", "notes": "Rotate **every 90 days**.",
      "completed": False, "modified_at": "2026-10-10T09:00:00.000Z",
      "created_at": "2026-09-01T09:00:00.000Z", "due_on": "2026-11-01",
      "permalink_url": "https://app.asana.com/0/111/t1", "assignee": {"gid": "u1", "name": "Ann"},
      "memberships": [{"section": {"gid": "s1", "name": "Security"}}]}
T2 = {"gid": "t2", "name": "Write the leave policy", "notes": "", "completed": True,
      "modified_at": "2026-10-09T09:00:00.000Z", "created_at": "2026-09-02T09:00:00.000Z",
      "permalink_url": "https://app.asana.com/0/111/t2", "assignee": None, "memberships": []}


def asana():
    write("asana", "full", ASANA_SRC, [
        ex("GET", f"{A}/events", {"resource": "111"},
           {"errors": [{"message": "Sync token invalid or too old."}], "sync": "ev-1"}, status=412),
        ex("GET", f"{A}/projects/111", None, {"data": PROJECT}),
        A_MEMBERS,
        ex("GET", f"{A}/users/u1", {"opt_fields": "email"},
           {"data": {"gid": "u1", "email": "Ann@acme.example"}}),
        ex("GET", f"{A}/users/u2", {"opt_fields": "email"},
           {"errors": [{"message": "Forbidden"}]}, status=403),
        ex("GET", f"{A}/tasks", {"project": "111", "limit": "100"},
           {"data": [T1], "next_page": {"offset": "off-2", "path": "/tasks?offset=off-2"}}),
        ex("GET", f"{A}/tasks/t1/stories", None, {"data": [
            {"gid": "st1", "resource_subtype": "assigned", "text": "Bob assigned to Ann",
             "created_at": "2026-09-01T10:00:00Z", "created_by": {"name": "Bob"}},
            {"gid": "st2", "resource_subtype": "comment_added", "text": "Prod next week.",
             "created_at": "2026-09-03T10:00:00Z", "created_by": {"name": "Bob"}},
            {"gid": "st3", "resource_subtype": "comment_added", "text": "Done for staging.",
             "created_at": "2026-09-04T10:00:00Z", "created_by": {"name": "Ann"}}],
            "next_page": None}),
        ex("GET", f"{A}/tasks", {"project": "111", "offset": "off-2"},
           {"data": [T2], "next_page": None}),
        ex("GET", f"{A}/tasks/t2/stories", None, {"data": [], "next_page": None}),
        A_BRIEF,
    ])
    write("asana", "incremental", ASANA_SRC, [
        ex("GET", f"{A}/events", {"resource": "111", "sync": "ev-1"}, {"data": [
            {"action": "deleted", "resource": {"gid": "t2", "resource_type": "task"}},
            {"action": "changed", "resource": {"gid": "t1", "resource_type": "task"}}],
            "sync": "ev-2", "has_more": False}),
        ex("GET", f"{A}/projects/111", None, {"data": PROJECT}),
        A_MEMBERS,
        ex("GET", f"{A}/users/u1", {"opt_fields": "email"},
           {"data": {"gid": "u1", "email": "ann@acme.example"}}),
        ex("GET", f"{A}/users/u2", {"opt_fields": "email"},
           {"errors": [{"message": "Forbidden"}]}, status=403),
        ex("GET", f"{A}/tasks", {"project": "111", "modified_since~": "T"},
           {"data": [dict(T1, notes="Rotate **every 30 days**.",
                          modified_at="2026-10-11T09:00:00.000Z")], "next_page": None}),
        ex("GET", f"{A}/tasks/t1/stories", None, {"data": [], "next_page": None}),
        A_BRIEF,
    ])


# ---- ClickUp -------------------------------------------------------------------------------
C = "https://api.clickup.com/api"
CLICKUP_SRC = [
    "https://developer.clickup.com/reference/getauthorizedteams (teams[] {id, name})",
    "https://developer.clickup.com/reference/getspaces (spaces[] {id, name, private, members[] "
    "{user {email}}})",
    "https://developer.clickup.com/reference/getfolders (folders[] {id, name, lists[]})",
    "https://developer.clickup.com/reference/getfolderlesslists (lists[] {id, name})",
    "https://developer.clickup.com/reference/getlistmembers (members[] {email})",
    "https://developer.clickup.com/reference/getfilteredteamtasks (page, list_ids[], "
    "date_updated_gt ms, include_closed, subtasks, include_markdown_description; tasks[])",
    "https://developer.clickup.com/reference/gettaskcomments (comments[] {id, comment_text, "
    "user.username, date ms})",
    "https://developer.clickup.com/reference/searchdocspublic (docs[] {id, name, parent {id, "
    "type 4 space | 5 folder | 6 list | 7 everything}, date_updated, deleted, archived}; "
    "next_cursor)",
    "https://developer.clickup.com/reference/getdocpagespublic (content_format=text/md; "
    "[{id, name, content, pages[]}])"]
HIERARCHY = [
    ex("GET", f"{C}/v2/team", None, {"teams": [{"id": "900", "name": "Acme"}]}, repeat=True),
    ex("GET", f"{C}/v2/team/900/space", None, {"spaces": [
        {"id": "s1", "name": "Engineering", "private": False, "members": []},
        {"id": "s2", "name": "People", "private": True, "members": [
            {"user": {"email": "HR@acme.example"}}, {"user": {"email": "ann@acme.example"}}]}]},
       repeat=True),
    ex("GET", f"{C}/v2/space/s1/folder", None, {"folders": [
        {"id": "f1", "name": "Runbooks", "lists": [{"id": "l1", "name": "On-call"}]}]},
       repeat=True),
    ex("GET", f"{C}/v2/space/s1/list", None, {"lists": [{"id": "l2", "name": "Backlog"}]},
       repeat=True),
    ex("GET", f"{C}/v2/space/s2/folder", None, {"folders": []}, repeat=True),
    ex("GET", f"{C}/v2/space/s2/list", None, {"lists": [{"id": "l3", "name": "Policies"}]},
       repeat=True),
]
TASK1 = {"id": "86a1", "name": "Restart the queue", "markdown_description": "Run `make restart`.",
         "status": {"status": "open"}, "list": {"id": "l1", "name": "On-call"},
         "folder": {"id": "f1", "name": "Runbooks"}, "assignees": [{"username": "Ann"}],
         "date_updated": "1760000000000", "date_created": "1759000000000",
         "url": "https://app.clickup.com/t/86a1"}
TASK2 = {"id": "86a2", "name": "Upgrade Postgres", "markdown_description": "",
         "status": {"status": "closed"}, "list": {"id": "l1", "name": "On-call"},
         "assignees": [], "date_updated": "1760000100000", "date_created": "1759000100000",
         "url": "https://app.clickup.com/t/86a2"}


def clickup():
    write("clickup", "full", CLICKUP_SRC, HIERARCHY + [
        ex("GET", f"{C}/v2/team/900/task", {"page": "0", "list_ids[]": "l1"},
           {"tasks": [TASK1, TASK2]}),
        ex("GET", f"{C}/v2/list/l1/member", None, {"members": [
            {"id": 1, "email": "Ann@acme.example"}, {"id": 2, "email": "bob@acme.example"}]}),
        ex("GET", f"{C}/v2/task/86a1/comment", None, {"comments": [
            {"id": "c2", "comment_text": "Done twice this week.", "user": {"username": "Bob"},
             "date": "1759500000000"},
            {"id": "c1", "comment_text": "Needs sudo.", "user": {"username": "Ann"},
             "date": "1759400000000"}]}),
        ex("GET", f"{C}/v2/task/86a2/comment", None, {"comments": []}),
        ex("GET", f"{C}/v3/workspaces/900/docs", None, {"docs": [
            {"id": "d1", "name": "On-call guide", "parent": {"id": "l1", "type": 6},
             "date_updated": 1760000000000, "deleted": False, "archived": False},
            {"id": "d2", "name": "Roadmap", "parent": {"id": "s9", "type": 4},
             "date_updated": 1760000000000, "deleted": False, "archived": False}],
            "next_cursor": "dc-2"}),
        ex("GET", f"{C}/v3/workspaces/900/docs", {"cursor": "dc-2"}, {"docs": [
            {"id": "d3", "name": "Engineering wiki", "parent": {"id": "f1", "type": 5},
             "date_updated": 1760000000000, "deleted": False, "archived": False}],
            "next_cursor": None}),
        ex("GET", f"{C}/v3/workspaces/900/docs/d1/pages", {"content_format": "text/md"}, [
            {"id": "p1", "name": "Paging", "content": "Page the **secondary** after 10 minutes.",
             "pages": [{"id": "p2", "name": "Escalation", "content": "Then the manager.",
                        "pages": []}]}]),
        ex("GET", f"{C}/v3/workspaces/900/docs/d3/pages", {"content_format": "text/md"}, [
            {"id": "p3", "name": "Setup", "content": "Install the CLI.", "pages": []}]),
    ])
    write("clickup", "incremental", CLICKUP_SRC, HIERARCHY + [
        ex("GET", f"{C}/v2/team/900/task",
           {"page": "0", "list_ids[]": "l1", "date_updated_gt~": "1"},
           {"tasks": [dict(TASK1, markdown_description="Run `make restart-all`.",
                           date_updated="1760000500000")]}),
        ex("GET", f"{C}/v2/list/l1/member", None, {"members": [{"id": 1,
                                                               "email": "ann@acme.example"}]}),
        ex("GET", f"{C}/v2/task/86a1/comment", None, {"comments": []}),
        ex("GET", f"{C}/v3/workspaces/900/docs", None, {"docs": [
            {"id": "d1", "name": "On-call guide", "parent": {"id": "l1", "type": 6},
             "date_updated": 1760000000000, "deleted": False, "archived": False},
            {"id": "d3", "name": "Engineering wiki", "parent": {"id": "f1", "type": 5},
             "date_updated": 1760000000000, "deleted": True, "archived": False}],
            "next_cursor": None}),
    ])


# ---- Coda ----------------------------------------------------------------------------------
D = "https://docs.superhuman.com/apis/v1"
CODA_SRC = [
    "https://coda.io/apis/v1/openapi.json getDoc (id, name, owner, updatedAt, browserLink); "
    "listPages (items[] {id, name, contentType canvas | embed | syncPage, updatedAt, "
    "browserLink}; nextPageToken)",
    "getPermissions (items[] {principal {type email | domain | anyone | group | workspace, "
    "email, domain}, access readonly | write | comment | none})",
    "beginPageContentExport (POST {outputFormat: markdown} -> 202 {id, status, href}); "
    "getPageContentExportStatus ({status inProgress | failed | complete, downloadLink})"]
DOC = {"id": "AbC1", "type": "doc", "name": "Team handbook", "owner": "Owner@acme.example",
       "updatedAt": "2026-10-10T10:00:00.000Z", "browserLink": "https://coda.io/d/_dAbC1"}


def export(page, text, polls=0):
    base = f"{D}/docs/AbC1/pages/{page}/export"
    out = [ex("POST", base, None, {"id": f"req-{page}", "status": "inProgress", "href": base},
              status=202, match_json={"outputFormat": "markdown"})]
    out += [ex("GET", f"{base}/req-{page}", None, {"id": f"req-{page}", "status": "inProgress"})
            for _ in range(polls)]
    link = f"https://docs.superhuman.com/blobs/DOC_EXPORT_RENDERING/req-{page}"
    out.append(ex("GET", f"{base}/req-{page}", None,
                  {"id": f"req-{page}", "status": "complete", "downloadLink": link}))
    out.append(ex("GET", link, None, text=text))
    return out


def coda():
    write("coda", "full", CODA_SRC, [
        ex("GET", f"{D}/docs/AbC1", None, DOC),
        ex("GET", f"{D}/docs/AbC1/acl/permissions", None, {"items": [
            {"id": "p1", "access": "readonly",
             "principal": {"type": "email", "email": "Ann@acme.example"}},
            {"id": "p2", "access": "write", "principal": {"type": "domain",
                                                          "domain": "acme.example"}},
            {"id": "p3", "access": "readonly",
             "principal": {"type": "group", "groupId": "grp-1", "groupName": "Sales"}},
            {"id": "p4", "access": "none",
             "principal": {"type": "email", "email": "gone@acme.example"}}]}),
        ex("GET", f"{D}/docs/AbC1/pages", None, {"items": [
            {"id": "canvas-1", "name": "Leave", "contentType": "canvas",
             "updatedAt": "2026-10-10T09:00:00.000Z",
             "browserLink": "https://coda.io/d/_dAbC1/Leave_su1"},
            {"id": "canvas-2", "name": "Travel", "contentType": "canvas",
             "updatedAt": "2026-10-09T09:00:00.000Z",
             "browserLink": "https://coda.io/d/_dAbC1/Travel_su2"}], "nextPageToken": "pg-2"}),
        *export("canvas-1", "# Leave\n\nEveryone gets **25 days**.", polls=1),
        *export("canvas-2", "# Travel\n\nBook through the portal."),
        ex("GET", f"{D}/docs/AbC1/pages", {"pageToken": "pg-2"}, {"items": [
            {"id": "canvas-3", "name": "Embedded map", "contentType": "embed",
             "updatedAt": "2026-10-09T09:00:00.000Z",
             "browserLink": "https://coda.io/d/_dAbC1/Map_su3"}]}),
    ])
    write("coda", "incremental", CODA_SRC, [
        ex("GET", f"{D}/docs/AbC1", None, dict(DOC, updatedAt="2026-10-11T10:00:00.000Z")),
        ex("GET", f"{D}/docs/AbC1/acl/permissions", None,
           {"statusCode": 403, "statusMessage": "Forbidden"}, status=403),
        ex("GET", f"{D}/docs/AbC1/pages", None, {"items": [
            {"id": "canvas-1", "name": "Leave", "contentType": "canvas",
             "updatedAt": "2026-10-11T09:00:00.000Z",
             "browserLink": "https://coda.io/d/_dAbC1/Leave_su1"}]}),
        *export("canvas-1", "# Leave\n\nEveryone gets **30 days**."),
    ])


# ---- Microsoft Teams messages ---------------------------------------------------------------
G = "https://graph.microsoft.com/v1.0"
TEAM = "11111111-2222-3333-4444-555555555555"
GENERAL = "19:general@thread.tacv2"
SECRET = "19:secret@thread.tacv2"
Q_GENERAL = GENERAL.replace(":", "%3A").replace("@", "%40")
Q_SECRET = SECRET.replace(":", "%3A").replace("@", "%40")
TEAMS_SRC = [
    "https://learn.microsoft.com/en-us/graph/api/channel-list (value[] {id, displayName, "
    "membershipType standard | private | shared})",
    "https://learn.microsoft.com/en-us/graph/api/chatmessage-delta (GET /teams/{t}/channels/{c}"
    "/messages/delta; @odata.nextLink, @odata.deltaLink; deletedDateTime)",
    "https://learn.microsoft.com/en-us/graph/api/chatmessage-list-replies",
    "https://learn.microsoft.com/en-us/graph/api/resources/chatmessage (messageType, body "
    "{contentType html | text, content}, from.user.displayName, webUrl, subject)",
    "https://learn.microsoft.com/en-us/graph/api/team-list-members, "
    "https://learn.microsoft.com/en-us/graph/api/channel-list-allmembers "
    "(aadUserConversationMember: email, userId)"]


def msg(mid, text, who, created, modified=None, **extra):
    return {"id": mid, "messageType": "message", "createdDateTime": created,
            "lastModifiedDateTime": modified or created, "deletedDateTime": None,
            "subject": extra.pop("subject", None), "webUrl": f"https://teams.microsoft.com/l/message/{mid}",
            "from": {"user": {"id": f"id-{who}", "displayName": who}},
            "body": {"contentType": "html", "content": text}, **extra}


CHANNELS = ex("GET", f"{G}/teams/{TEAM}/channels", None, {"value": [
    {"id": GENERAL, "displayName": "General", "membershipType": "standard"},
    {"id": SECRET, "displayName": "Leadership", "membershipType": "private"}]}, repeat=True)


def teams():
    write("teams_messages", "full", TEAMS_SRC, [
        CHANNELS,
        ex("GET", f"{G}/teams/{TEAM}/members", None, {"value": [
            {"id": "m1", "displayName": "Ann", "email": "Ann@acme.example", "userId": "u1"},
            {"id": "m2", "displayName": "Bob", "email": "bob@acme.example", "userId": "u2"}]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta", {"$top": "50"}, {
            "value": [msg("1001", "<p>Where is the <b>VPN</b> guide?</p>", "Ann",
                          "2026-10-01T09:00:00Z", subject="VPN"),
                      {"id": "1002", "messageType": "systemEventMessage",
                       "createdDateTime": "2026-10-01T09:30:00Z", "body": {"content": ""}}],
            "@odata.nextLink": f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta"
                               "?$skiptoken=sk-2"}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/1001/replies", None, {
            "value": [msg("1001-r2", "Thanks!", "Ann", "2026-10-01T10:00:00Z"),
                      msg("1001-r1", "It's on the <i>wiki</i>, under IT.", "Bob",
                          "2026-10-01T09:10:00Z", "2026-10-01T09:20:00Z"),
                      dict(msg("1001-r3", "", "Bob", "2026-10-01T10:05:00Z"),
                           deletedDateTime="2026-10-01T10:06:00Z")]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta",
           {"$skiptoken": "sk-2"}, {
               "value": [msg("1003", "Lunch is at noon.", "Bob", "2026-10-02T09:00:00Z")],
               "@odata.deltaLink": f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta"
                                   "?$deltatoken=dt-general-1"}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/1003/replies", None,
           {"value": []}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{SECRET}/allMembers", None, {"value": [
            {"id": "m1", "displayName": "Ann", "email": "ann@acme.example", "userId": "u1"}]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_SECRET}/messages/delta", {"$top": "50"}, {
            "value": [msg("2001", "The reorg is announced on Friday.", "Ann",
                          "2026-10-03T09:00:00Z")],
            "@odata.deltaLink": f"{G}/teams/{TEAM}/channels/{Q_SECRET}/messages/delta"
                                "?$deltatoken=dt-secret-1"}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_SECRET}/messages/2001/replies", None,
           {"value": []}),
    ])
    write("teams_messages", "incremental", TEAMS_SRC, [
        CHANNELS,
        ex("GET", f"{G}/teams/{TEAM}/members", None, {"value": [
            {"id": "m1", "displayName": "Ann", "email": "ann@acme.example", "userId": "u1"}]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta",
           {"$deltatoken": "dt-general-1"}, {
               "value": [msg("1001", "<p>Where is the <b>VPN</b> guide? (Mac)</p>", "Ann",
                             "2026-10-01T09:00:00Z", "2026-10-05T09:00:00Z", subject="VPN"),
                         dict(msg("1003", "", "Bob", "2026-10-02T09:00:00Z"),
                              deletedDateTime="2026-10-05T10:00:00Z")],
               "@odata.deltaLink": f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta"
                                   "?$deltatoken=dt-general-2"}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/1001/replies", None, {
            "value": [msg("1001-r1", "It's on the <i>wiki</i>, under IT.", "Bob",
                          "2026-10-01T09:10:00Z", "2026-10-01T09:20:00Z")]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{SECRET}/allMembers", None, {"value": [
            {"id": "m1", "displayName": "Ann", "email": "ann@acme.example", "userId": "u1"}]}),
        ex("GET", f"{G}/teams/{TEAM}/channels/{Q_SECRET}/messages/delta",
           {"$deltatoken": "dt-secret-1"}, {
               "value": [],
               "@odata.deltaLink": f"{G}/teams/{TEAM}/channels/{Q_SECRET}/messages/delta"
                                   "?$deltatoken=dt-secret-2"}),
    ])


# ---- Gmail ---------------------------------------------------------------------------------
GM = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SRC = [
    "https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile "
    "(emailAddress, historyId)",
    "https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/list "
    "(labelIds, q, maxResults, pageToken; threads[] {id, snippet, historyId}, nextPageToken)",
    "https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/get "
    "(format full | minimal; messages[] {id, threadId, labelIds, payload {headers, parts, "
    "body.data base64url}})",
    "https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list "
    "(startHistoryId; history[] {messagesAdded, messagesDeleted, labelsAdded, labelsRemoved}; "
    "historyId; 404 when too old)"]


def b64(text):
    import base64
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def mail(mid, tid, labels, subject, sender, text, html=None):
    parts = [{"mimeType": "text/plain", "filename": "", "body": {"data": b64(text)}}] if text \
        else []
    if html:
        parts.append({"mimeType": "text/html", "filename": "", "body": {"data": b64(html)}})
    parts.append({"mimeType": "application/pdf", "filename": "invoice.pdf",
                  "body": {"attachmentId": "att-1", "size": 1000}})
    return {"id": mid, "threadId": tid, "labelIds": labels, "payload": {
        "mimeType": "multipart/mixed", "headers": [
            {"name": "Subject", "value": subject}, {"name": "From", "value": sender},
            {"name": "To", "value": "ann@acme.example"},
            {"name": "Date", "value": "Mon, 5 Oct 2026 09:00:00 +0000"}],
        "parts": parts}}


def gmail():
    write("gmail", "full", GMAIL_SRC, [
        ex("GET", f"{GM}/profile", None, {"emailAddress": "ann@acme.example",
                                          "historyId": "5000"}),
        ex("GET", f"{GM}/threads", {"labelIds": "Label_7", "q": "newer_than:180d"}, {
            "threads": [{"id": "th1", "snippet": "Offer letter for Bob", "historyId": "4001"}],
            "nextPageToken": "gp-2"}),
        ex("GET", f"{GM}/threads/th1", {"format": "full"}, {"id": "th1", "historyId": "4001",
                                                            "messages": [
            mail("m1", "th1", ["Label_7"], "Offer letter for Bob", "hr@acme.example",
                 "Bob's salary will be 90,000."),
            mail("m2", "th1", ["Label_7", "SENT"], "Re: Offer letter for Bob",
                 "ann@acme.example", "", html="<p>Approved &amp; signed.</p>")]}),
        ex("GET", f"{GM}/threads", {"labelIds": "Label_7", "pageToken": "gp-2"}, {
            "threads": [{"id": "th2", "snippet": "Board dinner", "historyId": "4002"}]}),
        ex("GET", f"{GM}/threads/th2", {"format": "full"}, {"id": "th2", "historyId": "4002",
                                                            "messages": [
            mail("m3", "th2", ["Label_7"], "Board dinner", "ceo@acme.example",
                 "Dinner on the 12th.")]}),
    ])
    write("gmail", "incremental", GMAIL_SRC, [
        ex("GET", f"{GM}/history", {"startHistoryId": "5000"}, {"history": [
            {"id": "5001", "messagesAdded": [{"message": {"id": "m4", "threadId": "th1"}}]},
            {"id": "5002", "labelsRemoved": [{"message": {"id": "m3", "threadId": "th2"},
                                              "labelIds": ["Label_7"]}]}],
            "historyId": "5002"}),
        ex("GET", f"{GM}/threads/th1", {"format": "minimal"}, {"id": "th1", "historyId": "5001",
                                                               "messages": [
            {"id": "m1", "labelIds": ["Label_7"]}, {"id": "m2", "labelIds": ["Label_7"]},
            {"id": "m4", "labelIds": ["Label_7"]}]}),
        ex("GET", f"{GM}/threads/th1", {"format": "full"}, {"id": "th1", "historyId": "5001",
                                                            "messages": [
            mail("m1", "th1", ["Label_7"], "Offer letter for Bob", "hr@acme.example",
                 "Bob's salary will be 90,000."),
            mail("m4", "th1", ["Label_7"], "Re: Offer letter for Bob", "hr@acme.example",
                 "Start date moved to November.")]}),
        ex("GET", f"{GM}/threads/th2", {"format": "minimal"}, {"id": "th2", "historyId": "5002",
                                                               "messages": [
            {"id": "m3", "labelIds": ["INBOX"]}]}),
    ])


# ---- Google Calendar -----------------------------------------------------------------------
GC = "https://www.googleapis.com/calendar/v3"
CAL_SRC = [
    "https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list "
    "(items[] {id, summary, accessRole, primary})",
    "https://developers.google.com/workspace/calendar/api/v3/reference/events/list (syncToken, "
    "pageToken, nextPageToken, nextSyncToken, showDeleted; 410 when the token is gone)",
    "https://developers.google.com/workspace/calendar/api/v3/reference/events (id, status, "
    "summary, description, location, start, end, organizer, attendees[] {email, displayName, "
    "responseStatus}, updated, htmlLink, recurrence)"]
PRIMARY = "ann%40acme.example"


def event(eid, summary, start, end, updated, **extra):
    return {"id": eid, "status": "confirmed", "summary": summary, "updated": updated,
            "htmlLink": f"https://www.google.com/calendar/event?eid={eid}",
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "organizer": {"email": "ann@acme.example", "displayName": "Ann"}, **extra}


def calendar():
    write("google_calendar", "full", CAL_SRC, [
        ex("GET", f"{GC}/calendars/{PRIMARY}/events", {"showDeleted": "false"}, {
            "items": [event("ev1", "Salary review with Bob", "2026-11-03T10:00:00Z",
                            "2026-11-03T10:30:00Z", "2026-10-20T08:00:00.000Z",
                            description="Discuss the <b>raise</b>.", location="Room 4",
                            attendees=[{"email": "bob@acme.example", "displayName": "Bob",
                                        "responseStatus": "accepted"},
                                       {"email": "ann@acme.example",
                                        "responseStatus": "accepted"}])],
            "nextPageToken": "cp-2"}),
        ex("GET", f"{GC}/calendars/{PRIMARY}/events", {"pageToken": "cp-2"}, {
            "items": [event("ev2", "Weekly 1:1", "2026-01-05T09:00:00Z",
                            "2026-01-05T09:30:00Z", "2026-01-01T08:00:00.000Z",
                            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"]),
                      event("ev3", "Offsite 2019", "2019-05-01T09:00:00Z",
                            "2019-05-01T17:00:00Z", "2019-04-01T08:00:00.000Z")],
            "nextSyncToken": "cs-1"}),
    ])
    write("google_calendar", "incremental", CAL_SRC, [
        ex("GET", f"{GC}/calendars/{PRIMARY}/events", {"syncToken": "cs-1",
                                                       "showDeleted": "true"}, {
            "items": [event("ev1", "Salary review with Bob (moved)", "2026-11-04T10:00:00Z",
                            "2026-11-04T10:30:00Z", "2026-10-21T08:00:00.000Z"),
                      {"id": "ev2", "status": "cancelled"}],
            "nextSyncToken": "cs-2"}),
    ])


if __name__ == "__main__":
    asana()
    clickup()
    coda()
    teams()
    gmail()
    calendar()
