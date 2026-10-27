"""
Real per-document permissions (platform.md PL-09, PL-10 step 2; access.py,
groups_store.py, kb/services/permissions.py): access groups, document ACLs on
chunks, and the source's own permissions under a connection's `source` policy.

THE LEAK SUITE FOR ACLs. A member outside a document's ACL never retrieves it
(dense, keyword, intros, the API's search and read), is never answered or cited
from it, and never sees it again through a share link, chat search, Copy, a Slack
channel, the MCP / API, or a verified answer. Covered for a group grant, a user
grant, a verified domain, everyone, and an unknown principal (which fails
closed); and a policy switch re-tags the index without embedding anything.

Real indexing, Chroma and agent; only the embedding model (test_indexing.env) and
the language models (test_citations: Allow, CitingModel) are fakes.
"""

import json
import os

import pytest

import access
from test_citations import Allow, CitingModel
from test_indexing import env  # noqa: F401  (a fixture)

FINANCE = "Finance only: the Q3 acquisition target is Zephyrcorp for 40 million."
ALICE_NOTE = "Alice only: her relocation stipend is 9000 euros."
ENG = "Engineering only: the deploy freeze starts on the 12th of December."
DOMAIN = "Domain wide: the office wifi password is mango-tree-42."
UNMAPPED = "Unmapped: the board meets in the Orion room on Fridays."
OPEN = "Everyone: the cafeteria opens at eight."
HANDBOOK = "The handbook says the refund window is 30 days."


@pytest.fixture
def org(env, monkeypatch):  # noqa: F811
    """A workspace with alice (in Finance, and in the IdP group eng@...), bob (in
    no group), an HR person, uploads shared with Finance / alice / everyone, and a
    Google Drive connection under the `source` policy whose files are shared, at
    Drive, with alice, the eng group, the whole (verified) domain, someone who
    isn't a member, and anyone."""
    import agent
    import auth
    import db
    import groups_store
    import integrations_store
    import members_store
    import rag
    import safety
    import sync_store
    import workspaces
    from kb.services import indexing

    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: CitingModel(script={}))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)

    w = env.workspace(("public", "handbook.txt", HANDBOOK))
    domain = f"{w}.example"
    password = auth.hash_password("correct-horse")
    for name, role in (("alice", "public"), ("bob", "public"), ("hr", "hr")):
        members_store.set_password(f"{name}@{domain}", name.title(), password, w, role)
    members_store.add_verified_domain(w, domain)
    conn = db.connect()
    with conn:              # alice's groups from her sign-in (the IdP's claim)
        conn.execute("UPDATE members SET groups = ?, group_source = 'oidc-test' "
                     "WHERE username = ?", (json.dumps([f"eng@{domain}"]), f"alice@{domain}"))
    conn.close()
    finance = groups_store.create(w, "Finance", "", "admin")
    groups_store.set_members(w, finance["id"], [f"alice@{domain}"], "admin")

    admin = env.login(w)

    def upload(name, text, acl):
        r = env.client.post("/api/docs", headers=admin,
                            json={"name": name, "access": "public", "content": text, "acl": acl})
        assert r.status_code == 200, r.text
        return f"{r.json()['access']}/{r.json()['name']}"

    keys = {"finance": upload("finance.txt", FINANCE, [access.group(finance["id"])]),
            "alice": upload("alice.txt", ALICE_NOTE, [access.user(f"alice@{domain}")]),
            "open": upload("open.txt", OPEN, ["everyone"])}

    # Drive: files synced, with their permissions at Drive as the connector captures them.
    integrations_store.connect(w, "google_drive", "Acme", {"access_token": "x"}, "admin")
    integrations_store.update(w, "google_drive", settings={"policy": "source"})
    folder = workspaces.connector_dir(w, "google_drive")
    os.makedirs(folder, exist_ok=True)
    drive = {
        "eng": (ENG, {"users": [], "groups": [f"google:eng@{domain}"], "domains": [],
                      "anyone": False}),
        "domain": (DOMAIN, {"users": [], "groups": [], "domains": [domain], "anyone": False}),
        "unmapped": (UNMAPPED, {"users": ["stranger@elsewhere.example"],
                                "groups": ["google:nobody@elsewhere.example"],
                                "domains": ["elsewhere.example"], "anyone": False}),
        "anyone": (OPEN + " (Drive)", {"users": [], "groups": [], "domains": [],
                                       "anyone": True}),
    }
    for sid, (text, acl) in drive.items():
        with open(os.path.join(folder, f"{sid}.txt"), "w", encoding="utf-8") as f:
            f.write(f"{sid}\n\n{text}")
        sync_store.save_item(w, "google_drive", sid, file_name=f"{sid}.txt", title=sid,
                             url=f"https://drive.example/{sid}", acl=acl)
        keys[sid] = f"connectors/google_drive/{sid}.txt"
    indexing.schedule_index(w)
    env.wait(w)

    class Org:
        ws, keys_ = w, keys
        group = finance

        def login(self, name):
            r = env.client.post("/api/auth/login", json={"username": f"{name}@{domain}",
                                                          "password": "correct-horse"})
            assert r.status_code == 200, r.text
            return {"Authorization": f"Bearer {r.json()['token']}",
                    "X-Role": "hr" if name == "hr" else "public"}

        def viewer(self, name, role="public"):
            return groups_store.principals_for(w, f"{name}@{domain}", role)

    o = Org()
    o.keys = keys
    o.domain = domain
    o.admin = admin
    return o


SECRETS = {"finance": FINANCE, "alice": ALICE_NOTE, "eng": ENG, "domain": DOMAIN}


def _texts(docs):
    return " ".join(d.page_content for d in docs)


def _reach(ws, who, question):
    """Everything a search as `who` can reach for the question: both searches,
    no limit, and the document intros."""
    import rag
    from kb.services import indexing
    collection, version = indexing.current(ws)
    retriever = rag.load_chain(access.role_of(who), collection, version=str(version))[0]
    docs = retriever.invoke(question, k=50, principals=access.of(who))
    return docs + retriever.intros_for(docs, max_docs=50, principals=access.of(who))


# ---- The index ------------------------------------------------------------------------------
def test_each_chunk_carries_its_documents_acl(org, env):  # noqa: F811
    import ingest
    from kb.services import indexing
    got = ingest._store(indexing.current(org.ws)[0])._collection.get(include=["metadatas"])
    acls = {m["doc_key"]: (m["acl"], m["access"]) for m in got["metadatas"]}
    assert acls[org.keys["finance"]] == ([access.group(org.group["id"])], "restricted")
    assert acls[org.keys["alice"]] == ([access.user(f"alice@{org.domain}")], "restricted")
    assert acls[org.keys["open"]] == (["everyone"], "public")
    assert acls["public/handbook.txt"] == (["everyone"], "public")       # no ACL: its folder's
    assert acls[org.keys["domain"]] == ([access.domain(org.domain)], "restricted")
    assert acls[org.keys["anyone"]] == (["everyone"], "public")
    # Every grant unknown here: dropped, never widened. No one may read it.
    assert acls[org.keys["unmapped"]] == ([access.NOBODY], "restricted")
    [eng_group] = acls[org.keys["eng"]][0]
    assert eng_group.startswith("group:idp_")


def test_chroma_contains_filters_on_the_acl_list(org):
    """chromadb 1.5.9 keeps list metadata and filters it with $contains
    (docs.trychroma.com, metadata filtering): what access.chroma_filter relies on."""
    import ingest
    from kb.services import indexing
    col = ingest._store(indexing.current(org.ws)[0])._collection

    def keys(who):
        got = col.get(where=access.chroma_filter(who), include=["metadatas"])
        return {m["doc_key"] for m in got["metadatas"]}

    everyone = {"public/handbook.txt", org.keys["open"], org.keys["anyone"]}
    assert keys("public") == everyone
    assert keys(org.viewer("bob")) == everyone | {org.keys["domain"]}
    assert keys(org.viewer("alice")) == everyone | {org.keys[k] for k in
                                                   ("finance", "alice", "eng", "domain")}
    assert org.keys["unmapped"] not in keys(org.viewer("hr", "hr"))
    # One principal alone is a plain clause ($or needs two).
    assert access.chroma_filter(access.Viewer()) == {"acl": {"$contains": "everyone"}}


@pytest.mark.parametrize("secret", sorted(SECRETS))
def test_someone_outside_the_acl_never_retrieves_it(org, secret):
    text = SECRETS[secret]
    outsiders = [org.viewer("bob"), org.viewer("hr", "hr"), "public", "hr"]
    if secret == "domain":                    # everyone at the verified domain may
        outsiders = ["public", "hr", access.Viewer([access.user("guest@other.example")])]
    for who in outsiders:
        for question in (text, text.split(":", 1)[1]):
            assert text not in _texts(_reach(org.ws, who, question)), (who, question)
    # ...and the people it is shared with do.
    insider = org.viewer("alice")
    assert text in _texts(_reach(org.ws, insider, text))


def test_an_unknown_principal_fails_closed_for_everyone(org):
    for who in (org.viewer("alice"), org.viewer("bob"), org.viewer("hr", "hr")):
        assert UNMAPPED not in _texts(_reach(org.ws, who, UNMAPPED))


def test_the_keyword_index_is_built_once_and_filtered_per_asker(org):
    import rag
    from kb.services import indexing
    collection, version = indexing.current(org.ws)
    public = rag.load_chain("public", collection, version=str(version))[0]
    hr = rag.load_chain("hr", collection, version=str(version))[0]
    assert public.bm25.bm25 is hr.bm25.bm25                   # one BM25 per index version
    assert FINANCE not in _texts(public.bm25.docs)            # what "public" can reach
    alice = public.bm25.search(FINANCE, 10, org.viewer("alice"))
    assert FINANCE in _texts(alice)
    assert FINANCE not in _texts(public.bm25.search(FINANCE, 10, org.viewer("bob")))


# ---- Answers and everything built on them ----------------------------------------------------
def _ask(org, name, question, role="public"):
    """A saved answer for `name`, as the chat stream saves one."""
    import conversations_store
    from kb.services import answering, conversations
    viewer = org.viewer(name, role)
    user = {"username": f"{name}@{org.domain}", "workspace": org.ws}
    conv = conversations_store.create(org.ws, user["username"], role)
    turn = conversations.start_turn(org.ws, user["username"], viewer, conv["id"], question)
    done = answering.ask(org.ws, user, viewer, question, surface="web", turn=turn)
    conversations_store.finish_message(turn["message_id"], done.answer, done.state,
                                       sources=done.sources, max_access=done.max_access,
                                       acl=done.acl)
    return conv["id"], turn["message_id"], done


def test_an_answer_cites_only_what_its_asker_may_see(org):
    _, _, bob = _ask(org, "bob", "What is the Q3 acquisition target of Finance?")
    assert "Zephyrcorp" not in bob.answer + json.dumps(bob.sources)
    _, _, alice = _ask(org, "alice", "What is the Q3 acquisition target of Finance?")
    assert "Zephyrcorp" in alice.answer
    # Its audience: every ACL it read (here more than Finance's: a small corpus).
    assert [access.group(org.group["id"])] in alice.acl and alice.max_access == "restricted"


def test_a_slack_channel_answer_is_everyones_content_only(org):
    from kb.services import answering, slack
    user = {"username": f"alice@{org.domain}", "workspace": org.ws}
    done = answering.ask(org.ws, user, "public", "What is the Q3 acquisition target?",
                         surface="slack")
    assert "Zephyrcorp" not in done.answer + json.dumps(done.sources)
    # ...and alice is told privately that she could have been told more.
    assert slack.restricted_found(org.ws, org.viewer("alice"), "Q3 acquisition target")
    assert not slack.restricted_found(org.ws, "public", "Q3 acquisition target")


def test_leaving_the_group_hides_the_answer_everywhere(org, env):  # noqa: F811
    import groups_store
    cid, mid, _ = _ask(org, "alice", "What is the Q3 acquisition target of Finance?")
    alice, bob = org.login("alice"), org.login("bob")
    get = env.client.get
    shown = get(f"/api/conversations/{cid}", headers=alice).json()["messages"][-1]
    assert not shown["withheld"] and "Zephyrcorp" in shown["content"]
    assert get(f"/api/messages/{mid}/markdown", headers=alice).status_code == 200
    found = get("/api/conversations/search", params={"q": "Zephyrcorp"}, headers=alice).json()
    assert found["results"]

    # Shared with the workspace: bob, outside Finance, gets the question, not the answer.
    r = env.client.post("/api/shares", json={"conversation_id": cid, "message_id": mid},
                        headers=alice)
    assert r.status_code == 200 and r.json()["restricted"]
    opened = get(f"/api/shared/{r.json()['token']}", headers=bob).json()
    assert opened["withheld"] == 1 and "Zephyrcorp" not in json.dumps(opened)

    groups_store.set_members(org.ws, org.group["id"], [], "admin")      # alice leaves
    shown = get(f"/api/conversations/{cid}", headers=alice).json()["messages"][-1]
    assert shown["withheld"] and "Zephyrcorp" not in json.dumps(shown)
    assert get(f"/api/messages/{mid}/markdown", headers=alice).status_code == 403
    found = get("/api/conversations/search", params={"q": "Zephyrcorp"}, headers=alice).json()
    assert found["results"] == []


def test_the_api_and_mcp_search_as_the_keys_person(org, env):  # noqa: F811
    import api_keys_store
    from kb.services import retrieval
    api_keys_store.set_settings(org.ws, "admin", api_enabled=True, mcp_enabled=True)
    found = {}
    for name in ("alice", "bob"):
        _, key = api_keys_store.create(org.ws, f"{name}@{org.domain}", "k", ["search",
                                                                             "docs:read"],
                                       "public")
        headers = {"Authorization": f"Bearer {key}"}
        r = env.client.post("/api/v1/search", json={"query": FINANCE, "k": 10}, headers=headers)
        assert r.status_code == 200, r.text
        found[name] = json.dumps(r.json())
        docs = env.client.get("/api/v1/documents", headers=headers).json()["documents"]
        found[name] += json.dumps(docs)
        r = env.client.get("/api/v1/documents/public/finance.txt", headers=headers)
        found[name] += str(r.status_code)
    assert "Zephyrcorp" in found["alice"] and "finance.txt" in found["alice"]
    assert "Zephyrcorp" not in found["bob"] and "finance.txt" not in found["bob"]
    assert found["bob"].endswith("404")
    # The MCP server's read_document: the same check.
    assert retrieval.read_document(org.ws, org.viewer("bob"), org.keys["finance"]) is None
    assert retrieval.read_document(org.ws, org.viewer("alice"), org.keys["finance"])


def test_a_verified_answer_is_served_only_to_who_may_see_its_sources(org):
    import verified_store
    from kb.services import verified
    verified_store.create(org.ws, {"title": "Acquisition target", "variants": ["Who do we buy?"],
                                   "answer_md": "Zephyrcorp.", "sources": [org.keys["finance"]],
                                   "access": "public", "owner": f"hr@{org.domain}",
                                   "review_days": 90}, f"hr@{org.domain}")
    matcher = verified.Matcher(org.ws, "public")
    assert matcher.match("Who do we buy?", principals=org.viewer("bob")) is None
    assert matcher.match("Who do we buy?", principals="public") is None       # a Slack channel
    hit = matcher.match("Who do we buy?", principals=org.viewer("alice"))
    assert hit and hit["acls"][1] == [access.group(org.group["id"])]


def test_knowledge_shows_who_can_see_each_item(org, env):  # noqa: F811
    items = {i["doc_key"]: i for i in
             env.client.get("/api/knowledge", headers=org.admin).json()["items"]}
    assert [w["label"] for w in items[org.keys["finance"]]["who"]] == ["Finance"]
    assert [w["label"] for w in items[org.keys["domain"]]["who"]] == [f"Everyone at {org.domain}"]
    assert items[org.keys["unmapped"]]["dropped"] == 3
    assert [w["kind"] for w in items[org.keys["unmapped"]]["who"]] == ["nobody"]
    bob = {i["doc_key"] for i in
           env.client.get("/api/knowledge", headers=org.login("bob")).json()["items"]}
    assert org.keys["finance"] not in bob and org.keys["open"] in bob
    assert "who" not in env.client.get("/api/knowledge",
                                       headers=org.login("bob")).json()["items"][0]


# ---- Jira, Zendesk and GitHub under the source policy --------------------------------------------
def _sourced(org, env, provider, items):  # noqa: F811
    """A `provider` connection under the source policy with these synced items
    ({source id: (text, acl)}), indexed."""
    import integrations_store
    import sync_store
    import workspaces
    from kb.services import indexing
    integrations_store.connect(org.ws, provider, "Acme", {"access_token": "x"}, "admin")
    integrations_store.update(org.ws, provider, settings={"policy": "source"})
    folder = workspaces.connector_dir(org.ws, provider)
    os.makedirs(folder, exist_ok=True)
    for sid, (text, acl) in items.items():
        with open(os.path.join(folder, f"{sid}.txt"), "w", encoding="utf-8") as f:
            f.write(f"{sid}\n\n{text}")
        sync_store.save_item(org.ws, provider, sid, file_name=f"{sid}.txt", title=sid, acl=acl)
    indexing.schedule_index(org.ws)
    env.wait(org.ws)


def _who_reaches(org, text):
    people = {"alice": org.viewer("alice"), "bob": org.viewer("bob"),
              "hr": org.viewer("hr", "hr"), "channel": "public"}
    return {name for name, who in people.items() if text in _texts(_reach(org.ws, who, text))}


def test_jira_browse_grants_and_security_levels(org, env):  # noqa: F811
    eng = {"public": False, "users": ["atlassian:557058:abc"], "dynamic": ["reporter"],
           "groups": [f"atlassian:group:eng@{org.domain}"], "roles": ["atlassian:role:1"]}
    opened = {**eng, "public": True}
    texts = {"ops-1": "Jira OPS-1: the failover drill is on Tuesday at noon.",
             "ops-2": "Jira OPS-2: the breach report names the Kestrel vendor.",
             "pub-1": "Jira PUB-1: the public roadmap lists dark mode."}
    _sourced(org, env, "jira", {
        "ops-1": (texts["ops-1"], {"project": "OPS", "project_read": eng,
                                   "security_level": None, "restricted": False}),
        "ops-2": (texts["ops-2"], {"project": "OPS", "project_read": opened, "restricted": True,
                                   "security_level": {"id": "1", "name": "Security team"}}),
        "pub-1": (texts["pub-1"], {"project": "PUB", "project_read": opened,
                                   "security_level": None, "restricted": False})})
    assert _who_reaches(org, texts["ops-1"]) == {"alice"}           # the eng group's browse grant
    assert _who_reaches(org, texts["ops-2"]) == set()               # a security level: no one
    assert _who_reaches(org, texts["pub-1"]) == {"alice", "bob", "hr", "channel"}


def test_zendesk_segments_and_tickets(org, env):  # noqa: F811
    import integrations_store
    texts = {"a1": "Zendesk article: resetting a password takes two minutes.",
             "a2": "Zendesk article for agents: the refund override code is ORCA-7.",
             "t1": "Zendesk ticket: the customer at 12 Elm Street wants a refund."}
    _sourced(org, env, "zendesk", {
        "a1": (texts["a1"], {"mode": "workspace", "everyone": True, "segments": []}),
        "a2": (texts["a2"], {"mode": "staff", "everyone": False,
                             "segments": [{"id": "9", "user_type": "staff"}]}),
        "t1": (texts["t1"], {"mode": "staff", "reason": "ticket"})})
    assert _who_reaches(org, texts["a1"]) == {"alice", "bob", "hr", "channel"}
    assert _who_reaches(org, texts["a2"]) == {"hr"}                 # staff: the hr group
    assert _who_reaches(org, texts["t1"]) == {"hr"}
    # Tickets on: the connection is locked to HR and admins, whatever its policy says.
    integrations_store.update(org.ws, "zendesk", settings={"policy": "workspace", "tickets": True,
                                                           "access_lock": "restricted"})
    from kb.services import indexing
    indexing.schedule_retag(org.ws, "zendesk")
    env.wait(org.ws)
    assert _who_reaches(org, texts["a1"]) == {"hr"}


def test_github_visibility_collaborators_and_teams(org, env):  # noqa: F811
    texts = {"site": "GitHub acme/site: the README says to run make serve.",
             "api": "GitHub acme/api: the signing key rotates every 90 days.",
             "ops": "GitHub acme/ops: the pager rota lives in rota.yaml."}
    private = {"repo": "acme/api", "visibility": "private", "public": False,
               "users": ["github:octocat"], "teams": [f"github:team:acme/eng@{org.domain}"]}
    _sourced(org, env, "github", {
        "site": (texts["site"], {"repo": "acme/site", "visibility": "public", "public": True}),
        "api": (texts["api"], private),
        "ops": (texts["ops"], {**private, "repo": "acme/ops", "teams": None, "partial": True})})
    assert _who_reaches(org, texts["site"]) == {"alice", "bob", "hr", "channel"}
    assert _who_reaches(org, texts["api"]) == {"alice"}             # the eng team
    assert _who_reaches(org, texts["ops"]) == set()                 # a login only: no one


# ---- Changing who may see -----------------------------------------------------------------------
def test_a_policy_switch_retags_without_embedding(org, env):  # noqa: F811
    embedded = len(env.embedded)
    r = env.client.put("/api/integrations/google_drive/policy", json={"policy": "workspace"},
                       headers=org.admin)
    assert r.status_code == 200, r.text
    env.wait(org.ws)
    assert len(env.embedded) == embedded                       # nothing embedded again
    assert ENG in _texts(_reach(org.ws, org.viewer("bob"), ENG))
    r = env.client.put("/api/integrations/google_drive/policy", json={"policy": "restricted"},
                       headers=org.admin)
    env.wait(org.ws)
    assert ENG not in _texts(_reach(org.ws, org.viewer("alice"), ENG))
    assert ENG in _texts(_reach(org.ws, org.viewer("hr", "hr"), ENG))
    assert len(env.embedded) == embedded


def test_an_uploads_acl_can_change_by_retag(org, env):  # noqa: F811
    embedded = len(env.embedded)
    r = env.client.put(f"/api/docs/{org.keys['finance']}/acl",
                       json={"acl": [access.user(f"bob@{org.domain}")]}, headers=org.admin)
    assert r.status_code == 200, r.text
    env.wait(org.ws)
    assert len(env.embedded) == embedded
    assert FINANCE in _texts(_reach(org.ws, org.viewer("bob"), FINANCE))
    assert FINANCE not in _texts(_reach(org.ws, org.viewer("alice"), FINANCE))
    # The Documents screen lists it for HR / Admin, but its text is only for bob now.
    docs = {f"{d['access']}/{d['name']}": d for d in
            env.client.get("/api/docs", headers=org.admin).json()["docs"]}
    assert docs[org.keys["finance"]]["readable"] is False
    assert docs[org.keys["open"]]["readable"] is True
    assert env.client.get(f"/api/docs/{org.keys['finance']}", headers=org.admin).status_code == 403
    bad = env.client.put(f"/api/docs/{org.keys['finance']}/acl",
                         json={"acl": ["user:stranger@elsewhere.example"]}, headers=org.admin)
    assert bad.status_code == 400


def test_a_new_group_member_counts_at_the_next_permission_sync(org, env):  # noqa: F811
    """A Drive group that matches no group here fails closed until one appears:
    then a permission sync (a re-tag) maps it."""
    import db
    import sync_store
    from kb.services import indexing
    sync_store.save_item(org.ws, "google_drive", "eng", acl={
        "users": [], "groups": [f"google:design@{org.domain}"], "domains": [], "anyone": False})
    indexing.schedule_retag(org.ws, "google_drive")
    env.wait(org.ws)
    assert ENG not in _texts(_reach(org.ws, org.viewer("alice"), ENG))
    conn = db.connect()
    with conn:
        conn.execute("UPDATE members SET groups = ?, group_source = 'oidc-test' WHERE username = ?",
                     (json.dumps([f"design@{org.domain}"]), f"bob@{org.domain}"))
    conn.close()
    indexing.schedule_retag(org.ws, "google_drive")
    env.wait(org.ws)
    assert ENG in _texts(_reach(org.ws, org.viewer("bob"), ENG))


def test_sync_permissions_now_and_the_policy_rules(org, env):  # noqa: F811
    import integrations_store
    r = env.client.post("/api/integrations/google_drive/permissions/sync", headers=org.admin)
    assert r.status_code == 200, r.text
    card = next(i for i in env.client.get("/api/integrations", headers=org.admin)
                .json()["integrations"] if i["id"] == "google_drive")
    assert card["policy"] == "source" and "source" in card["policies"]
    # Notion reads no permissions: it can't follow the source's.
    integrations_store.connect(org.ws, "notion", "Acme", {"access_token": "x"}, "admin")
    r = env.client.put("/api/integrations/notion/policy", json={"policy": "source"},
                       headers=org.admin)
    assert r.status_code == 400
    assert integrations_store.policy_of(integrations_store.get(org.ws, "notion")) == "restricted"


def test_zendesk_tickets_keep_their_connection_locked_to_hr(org, env):  # noqa: F811
    import integrations_store
    integrations_store.connect(org.ws, "zendesk", "Acme", {"access_token": "x"}, "admin")
    integrations_store.update(org.ws, "zendesk", settings={"policy": "source", "tickets": True,
                                                           "access_lock": "restricted"})
    assert integrations_store.policy_of(integrations_store.get(org.ws, "zendesk")) == "restricted"
    for policy in ("source", "workspace"):
        r = env.client.put("/api/integrations/zendesk/policy", json={"policy": policy},
                           headers=org.admin)
        assert r.status_code == 400, policy


# ---- Old data ----------------------------------------------------------------------------------
def test_chunks_and_answers_from_before_acls_keep_their_meaning(tmp_path, monkeypatch):
    import ingest
    import rag
    from eval.harness import HashEmbeddings
    from chromadb.api.client import SharedSystemClient
    from kb.services import conversations
    for module in (ingest, rag):
        monkeypatch.setattr(module, "OpenAIEmbeddings", lambda **kwargs: HashEmbeddings())
        monkeypatch.setattr(module, "DB_FOLDER", str(tmp_path / "chroma"))
    try:
        store = ingest._store("legacy")
        store._collection.add(ids=["p", "r"], embeddings=HashEmbeddings().embed_documents(
                                  ["public text", "restricted text"]),
                              documents=["public handbook text", "restricted salary text"],
                              metadatas=[{"access": "public", "doc_key": "public/a.txt"},
                                         {"access": "restricted", "doc_key": "restricted/b.txt"}])
        public = rag.load_chain("public", "legacy")[0]        # backfills the ACLs in place
        got = store._collection.get(include=["metadatas"])
        assert {m["doc_key"]: m["acl"] for m in got["metadatas"]} == {
            "public/a.txt": ["everyone"], "restricted/b.txt": ["group:hr"]}
        assert "salary" not in _texts(public.invoke("salary text"))
        assert "salary" in _texts(rag.load_chain("hr", "legacy")[0].invoke("salary text"))
    finally:
        SharedSystemClient.clear_system_cache()
    # An answer saved before ACLs: its max_access says.
    old = {"author": "assistant", "max_access": "restricted", "acl": None, "sources": []}
    assert not conversations.visible_to("public", old)
    assert conversations.visible_to("hr", old)
    assert conversations.visible_to("public", {**old, "max_access": "public"})


def test_the_migration_fills_in_answers_audiences(tmp_path):
    import sqlite3
    import db
    path = str(tmp_path / "app.db")
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO workspaces (slug, name, created_at) VALUES ('w', 'W', 0)")
    conn.execute("INSERT INTO conversations (id, workspace, owner, role_used, created_at, "
                 "updated_at) VALUES ('c', 'w', 'o', 'public', 0, 0)")
    for mid, level in (("m1", "public"), ("m2", "restricted")):
        conn.execute("INSERT INTO messages (id, conversation_id, author, content, state, "
                     "max_access, created_at) VALUES (?, 'c', 'assistant', 'x', 'answered', ?, 0)",
                     (mid, level))
    conn.execute("UPDATE messages SET acl = NULL")
    conn.execute("UPDATE alembic_version SET version_num = '0015'")
    conn.commit()
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    assert dict(conn.execute("SELECT id, acl FROM messages")) == {
        "m1": "[]", "m2": '[["group:hr"]]'}
    # A workspace's purge deletes every table with a `workspace` column: these have one.
    for table in ("access_groups", "group_members", "doc_acl"):
        assert "workspace" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()


def test_erasing_a_member_takes_them_out_of_groups_and_acls(org, env):  # noqa: F811
    import db
    import groups_store
    import ingest
    from kb.services import indexing
    alice = f"alice@{org.domain}"
    r = env.client.post(f"/api/admin/members/{alice}/erase", headers=org.admin,
                        json={"confirm": alice})
    assert r.status_code == 200, r.text
    pseudonym = r.json()["pseudonym"]
    env.wait(org.ws)
    assert groups_store.members(org.ws, org.group["id"]) == []
    # The document shared with her by name: no one may see it now (not her folder's default).
    assert groups_store.doc_acl(org.ws, org.keys["alice"]) == [access.user(pseudonym)]
    for who in (org.viewer("bob"), org.viewer("hr", "hr")):
        assert ALICE_NOTE not in _texts(_reach(org.ws, who, ALICE_NOTE))
    # Her address is nowhere in the index's ACLs, nor in the access tables.
    got = ingest._store(indexing.current(org.ws)[0])._collection.get(include=["metadatas"])
    assert alice not in json.dumps([m["acl"] for m in got["metadatas"]])
    conn = db.connect()
    for table in ("group_members", "doc_acl"):
        assert alice not in json.dumps([list(r) for r in conn.execute(f"SELECT * FROM {table}")])
    conn.close()


# ---- The Groups screen's API --------------------------------------------------------------------
def test_admins_manage_manual_groups_and_see_the_identity_providers(org, env):  # noqa: F811
    groups = env.client.get("/api/admin/groups", headers=org.admin).json()["groups"]
    by_name = {g["name"]: g for g in groups}
    assert groups[0]["source"] == "builtin" and not groups[0]["editable"]
    assert {m["username"] for m in by_name["HR & Admin"]["members"]} >= {f"hr@{org.domain}"}
    idp = by_name[f"eng@{org.domain}"]
    assert idp["source"] == "idp" and not idp["editable"]
    assert [m["username"] for m in idp["members"]] == [f"alice@{org.domain}"]
    r = env.client.put(f"/api/admin/groups/{idp['id']}/members", json={"usernames": []},
                       headers=org.admin)
    assert r.status_code == 400                         # read-only: change it at the provider

    made = env.client.post("/api/admin/groups", json={"name": "Legal"}, headers=org.admin).json()
    assert env.client.post("/api/admin/groups", json={"name": "legal"},
                           headers=org.admin).status_code == 400          # names are unique
    r = env.client.put(f"/api/admin/groups/{made['id']}/members",
                       json={"usernames": [f"bob@{org.domain}", "stranger@else.example"]},
                       headers=org.admin)
    assert r.status_code == 400                         # members of this workspace only
    r = env.client.put(f"/api/admin/groups/{made['id']}/members",
                       json={"usernames": [f"bob@{org.domain}"]}, headers=org.admin)
    assert [m["username"] for m in r.json()["members"]] == [f"bob@{org.domain}"]
    assert access.group(made["id"]) in org.viewer("bob")          # counts at once
    picker = env.client.get("/api/access/principals", headers=org.admin).json()["principals"]
    assert {"everyone", access.group(made["id"]), access.user(f"bob@{org.domain}")} <= {
        p["principal"] for p in picker}
    assert env.client.delete(f"/api/admin/groups/{made['id']}",
                             headers=org.admin).status_code == 200
    assert access.group(made["id"]) not in org.viewer("bob")

    # Groups gate content, roles gate screens: HR and members can't manage groups.
    for who in ("hr", "bob"):
        assert env.client.get("/api/admin/groups", headers=org.login(who)).status_code == 403
    assert env.client.get("/api/access/principals", headers=org.login("bob")).status_code == 403


# ---- Route walk -------------------------------------------------------------------------------
# Routes that know the asker's role but not their principals, and why that's
# enough. Any other route that reads content must take current_viewer.
ROLE_ONLY = {
    "list_conversations": "titles only: a title is the asker's own first question",
    "create_conversation": "records the role a chat is started under",
    "edit_conversation": "title and folder only",
    "fork_conversation": "copies rows; reading the copy is checked like any chat",
    "create_share": "makes a link; open_share checks every answer",
    "open_share": "works out the viewer's and the sharer's principals itself",
    "list_integrations": "the role decides the live Outline tool; no documents",
    "get_insights": "HR / Admin counts by access level (follow-up: by ACL)",
    "export_insights": "as get_insights",
    "list_eval_cases": "HR / Admin; eval cases carry no ACL (follow-up)",
    "list_prompts": "saved prompts hold no documents; the role says who keeps the workspace's",
    "create_prompt": "as list_prompts",
    "update_prompt": "as list_prompts",
    "delete_prompt": "as list_prompts",
}


def test_every_content_route_checks_the_askers_principals():
    import api
    from fastapi.routing import APIRoute, iter_route_contexts
    role_only = set()
    for context in iter_route_contexts(api.app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        deps = {d.call.__name__ for d in route.dependant.dependencies}
        if "current_role" in deps and "current_viewer" not in deps:
            role_only.add(route.endpoint.__name__)
    assert role_only == set(ROLE_ONLY), ("A route reads the role but not the principals: "
                                         "check it against groups (access.py), then list it "
                                         "here with why, or take current_viewer.")
