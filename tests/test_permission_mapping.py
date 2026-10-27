"""
The `source` policy's mapping (kb/services/permissions.Resolver): each
connector's captured permissions (source_items.acl, as connectors/*.py write
them) onto this workspace's principals. What doesn't match is dropped, never
widened; with nothing left, no one may see the item.
"""

import pytest

import access
from kb.services.permissions import Resolver

ALICE = "alice@acme.example"
ENG = access.group("idp_eng")
SUPPORT = access.group("scim_support")


@pytest.fixture
def resolver():
    """A workspace with alice, the IdP group eng@acme.example (also known by its
    Entra object id), the SCIM group "support" (a GitHub team's slug and an
    Atlassian group's name), and acme.example verified."""
    r = Resolver.__new__(Resolver)
    r.ws, r.members, r.domains = "w", {ALICE, "bob@acme.example"}, {"acme.example"}
    r.groups = {"eng@acme.example": {ENG}, "0f6c-entra-id": {ENG},
                "support": {SUPPORT}, "acme/support": {SUPPORT}}
    return r


def acl(r, source, items=None):
    return r.resolve(source, items)


# ---- Google Drive / SharePoint (the generic shape) ----------------------------------------
def test_drive_users_groups_domains_and_anyone(resolver):
    assert acl(resolver, {"users": [ALICE.upper()], "groups": [], "domains": []}) == (
        [access.user(ALICE)], 0)
    assert acl(resolver, {"users": [], "groups": ["google:eng@acme.example"]}) == ([ENG], 0)
    assert acl(resolver, {"domains": ["acme.example"]}) == ([access.domain("acme.example")], 0)
    assert acl(resolver, {"users": ["x@y.example"], "anyone": True}) == (["everyone"], 0)


def test_unknown_grants_fail_closed(resolver):
    # A non-member, an unknown group, an unverified domain: all dropped.
    got, dropped = acl(resolver, {"users": ["stranger@else.example"],
                                  "groups": ["google:else@else.example"],
                                  "domains": ["else.example"]})
    assert got == [access.NOBODY] and dropped == 3
    # One of them unknown: only that one goes; the others stay, nobody is added.
    got, dropped = acl(resolver, {"users": [ALICE, "stranger@else.example"]})
    assert got == [access.user(ALICE)] and dropped == 1
    for nothing in (None, {}, "users"):
        assert acl(resolver, nothing)[0] == [access.NOBODY]


def test_sharepoint_ids_and_site_groups(resolver):
    got, dropped = acl(resolver, {"users": ["entra:user:123"],
                                  "groups": ["entra:0f6c-entra-id", "sharepoint:site:3"],
                                  "anyone": False, "organization": False})
    assert got == [ENG] and dropped == 2         # an Entra user id and a SharePoint site group
    got, _ = acl(resolver, {"users": [], "groups": [], "anyone": False, "organization": True})
    assert got == [access.domain("acme.example")]
    resolver.domains = set()
    assert acl(resolver, {"organization": True})[0] == [access.NOBODY]


# ---- Jira ----------------------------------------------------------------------------------------
def test_jira_project_browse_grants(resolver):
    project = {"public": False, "users": ["atlassian:557058:abc"],
               "groups": ["atlassian:group:support"], "roles": ["atlassian:role:10002"],
               "dynamic": ["reporter"]}
    got, dropped = acl(resolver, {"project": "OPS", "project_read": project,
                                  "security_level": None, "restricted": False})
    assert got == [SUPPORT] and dropped == 3     # an account id, a project role, "reporter"
    anyone = {**project, "public": True}
    assert acl(resolver, {"project": "OPS", "project_read": anyone, "security_level": None,
                          "restricted": False})[0] == ["everyone"]


def test_jira_security_levels_and_unreadable_schemes_fail_closed(resolver):
    project = {"public": True, "users": [], "groups": [], "roles": [], "dynamic": []}
    level = {"id": "10000", "name": "Staff only"}
    assert acl(resolver, {"project": "OPS", "project_read": project,
                          "security_level": level, "restricted": True})[0] == [access.NOBODY]
    assert acl(resolver, {"project": "OPS", "project_read": None, "security_level": None,
                          "restricted": False, "partial": True})[0] == [access.NOBODY]


# ---- Zendesk -------------------------------------------------------------------------------------
def test_zendesk_segments(resolver):
    assert acl(resolver, {"mode": "workspace", "everyone": True, "segments": []})[0] == [
        "everyone"]
    signed_in = {"mode": "workspace", "everyone": False,
                 "segments": [{"id": "1", "user_type": "signed_in_users"}]}
    assert acl(resolver, signed_in)[0] == ["everyone"]
    narrower = {"mode": "staff", "everyone": False,
                "segments": [{"id": "2", "user_type": "staff", "group_ids": [7]}]}
    assert acl(resolver, narrower)[0] == [access.HR]
    assert acl(resolver, {"mode": "staff", "reason": "ticket"})[0] == [access.HR]


# ---- GitHub --------------------------------------------------------------------------------------
def test_github_visibility_collaborators_and_teams(resolver):
    assert acl(resolver, {"repo": "acme/site", "visibility": "public", "public": True})[0] == [
        "everyone"]
    private = {"repo": "acme/api", "visibility": "private", "public": False,
               "users": ["github:octocat"], "teams": ["github:team:acme/support"]}
    got, dropped = acl(resolver, private)
    assert got == [SUPPORT] and dropped == 1     # a GitHub login is no member here
    # Teams unreadable (no Administration permission): only what was read counts.
    partial = {**private, "teams": None, "partial": True}
    assert acl(resolver, partial)[0] == [access.NOBODY]


# ---- Confluence ----------------------------------------------------------------------------------
def test_confluence_space_page_and_ancestor_restrictions(resolver):
    space = {"users": ["atlassian:557058:abc"], "groups": ["atlassian:group:support"],
             "roles": [], "space_id": "1"}
    page = {"space": "OPS", "space_read": space, "restricted": False, "users": [],
            "groups": [], "inherits_from": []}
    assert acl(resolver, page)[0] == [SUPPORT]
    locked = {**page, "restricted": True, "users": [ALICE], "groups": ["atlassian:group:support"]}
    assert sorted(acl(resolver, locked)[0]) == sorted([access.user(ALICE), SUPPORT])
    # A child of a restricted page: its own restrictions AND the parent's must allow.
    parent = {**locked, "groups": []}
    child = {**locked, "inherits_from": ["page-1"]}
    assert acl(resolver, child, {"page-1": parent})[0] == [access.user(ALICE)]
    assert acl(resolver, {**page, "inherits_from": ["page-9"]}, {})[0] == [access.NOBODY]
    assert acl(resolver, {**page, "partial": True})[0] == [access.NOBODY]
    assert acl(resolver, {**page, "space_read": None})[0] == [access.NOBODY]


def test_outline_notion_and_websites(resolver):
    assert acl(resolver, {"collection": "c", "collection_permission": "read"})[0] == ["everyone"]
    assert acl(resolver, {"collection": "c", "collection_permission": None})[0] == [
        access.NOBODY]
    assert acl(resolver, {"source": "shared_with_integration"})[0] == [access.NOBODY]
    assert acl(resolver, {"public_web": True})[0] == ["everyone"]


def test_linear_team_members(resolver):
    got, dropped = acl(resolver, {"via": "linear", "team": "t1", "teams": ["t1"],
                                  "users": [ALICE, "stranger@else.example"]})
    assert got == [access.user(ALICE)] and dropped == 1
    assert acl(resolver, {"via": "linear", "team": None, "users": [ALICE]})[0] == [access.NOBODY]
    assert acl(resolver, {"via": "linear", "team": "t1", "users": []})[0] == [access.NOBODY]


def test_dropbox_members_external_groups_and_invitees(resolver):
    got, dropped = acl(resolver, {"via": "dropbox", "users": [ALICE],
                                  "groups": ["dropbox:ext:eng@acme.example", "dropbox:group:g:2",
                                             "dropbox:ext:unknown"],
                                  "invitees": ["bob@acme.example"]})
    # An IdP-synced group maps; a Dropbox-only group, an unknown one and an invitee don't.
    assert got == sorted([access.user(ALICE), ENG]) and dropped == 3
    # A group id that happens to equal an IdP group's key still isn't one: only ext: maps.
    assert acl(resolver, {"via": "dropbox", "groups": ["dropbox:group:support"]})[0] == [
        access.NOBODY]
    assert acl(resolver, {"via": "dropbox", "users": [ALICE], "owner_only": True})[0] == [
        access.user(ALICE)]


def test_box_collaborators_groups_and_shared_links(resolver):
    got, dropped = acl(resolver, {"via": "box", "users": [ALICE, "eve@else.example"],
                                  "groups": ["box:group:77"], "shared_link": None})
    assert got == [access.user(ALICE)] and dropped == 2
    assert acl(resolver, {"via": "box", "users": [], "shared_link": "open"})[0] == ["everyone"]
    assert acl(resolver, {"via": "box", "users": [], "shared_link": "company"})[0] == [
        access.domain("acme.example")]
    assert acl(resolver, {"via": "box", "users": [], "shared_link": "collaborators"})[0] == [
        access.NOBODY]


def test_s3_and_unknown_connectors_fail_closed(resolver):
    assert acl(resolver, {"via": "s3"}) == ([access.NOBODY], 1)
    assert acl(resolver, {"via": "someday", "users": [ALICE], "anyone": True}) == (
        [access.NOBODY], 1)


def test_freshdesk_folder_visibility(resolver):
    for everyone in (1, 2):                            # all users, logged-in users
        assert acl(resolver, {"via": "freshdesk", "visibility": everyone}) == (["everyone"], 0)
    for staff in (3, 4, 5, 6, 7):                      # agents, companies, bots, segments
        assert acl(resolver, {"via": "freshdesk", "visibility": staff,
                              "company_ids": [5]})[0] == [access.HR]
    assert acl(resolver, {"via": "freshdesk", "ticket": True})[0] == [access.HR]
    for unknown in (None, 8, "1"):
        assert acl(resolver, {"via": "freshdesk", "visibility": unknown}) == ([access.NOBODY], 1)


def test_hubspot_access_types(resolver):
    assert acl(resolver, {"via": "hubspot", "access_type": "PUBLIC"})[0] == ["everyone"]
    for narrower in ("SSO_LOGIN", "ACCESS_GROUP_MEMBERSHIP"):
        assert acl(resolver, {"via": "hubspot", "access_type": narrower,
                              "access_group_ids": ["7788"]})[0] == [access.HR]
    assert acl(resolver, {"via": "hubspot", "access_type": None}) == ([access.NOBODY], 1)


def test_intercom_never_follows_an_audience_it_cant_read(resolver):
    published = {"via": "intercom", "kind": "article", "state": "published",
                 "help_center_live": True, "audience": "not_exposed"}
    assert acl(resolver, published)[0] == [access.HR]
    assert acl(resolver, {"via": "intercom", "kind": "conversation"})[0] == [access.HR]
    assert acl(resolver, {"via": "intercom"}) == ([access.NOBODY], 1)


def test_salesforce_channels_and_data_categories(resolver):
    public = {"via": "salesforce", "channels": {"app": True, "pkb": True}, "data_categories": []}
    assert acl(resolver, public)[0] == ["everyone"]
    # Data categories gate guest users too: not provably public.
    assert acl(resolver, {**public, "data_categories": ["Region:Spain"]})[0] == [access.HR]
    assert acl(resolver, {**public, "channels": {"app": True, "pkb": False}})[0] == [access.HR]
    assert acl(resolver, {**public, "channels": {"pkb": "true"}})[0] == [access.HR]
    assert acl(resolver, {"via": "salesforce", "channels": {}}) == ([access.NOBODY], 1)


# ---- The principals algebra ---------------------------------------------------------------------
def test_an_answers_audience_is_every_acl_it_read():
    eng, alice = access.group("eng"), access.user(ALICE)
    found = access.clauses([["everyone"], [eng, alice], [eng, alice], [alice]])
    assert found == [[alice]]                          # [alice] implies [eng, alice]
    assert access.sees(access.Viewer([alice]), found)
    assert not access.sees(access.Viewer([eng]), found)
    two = access.clauses([[eng], [alice]])
    assert access.sees(access.Viewer([eng, alice]), two)
    assert not access.sees((access.Viewer([eng, alice]), access.Viewer([eng])), two)
    assert access.message_clauses({"max_access": "public", "acl": None}) == []
    assert access.message_clauses({"max_access": "restricted"}) == [[access.HR]]
    assert access.level_of([]) == "public" and access.level_of(two) == "restricted"


def test_the_salary_guard_follows_the_hr_group():
    import safety
    assert safety.may_see_restricted("hr") and not safety.may_see_restricted("public")
    assert safety.may_see_restricted(access.Viewer([access.HR], "public"))
    assert not safety.may_see_restricted(access.Viewer([access.group("finance")], "hr"))
