"""
The product tour's progress (kb/services/onboarding.py, "PRODUCT TOUR";
PUT /api/onboarding/tours/{tour}): kept per member on the server, so a tour seen in
one browser doesn't start again in another; the admin tour is for admins alone; a
read-only workspace still takes it; a data request lists it and erasure deletes it.
"""

import uuid

import pytest
from fastapi import HTTPException

import auth
import members_store

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def workspace():
    """A workspace with an admin and a member."""
    tag = uuid.uuid4().hex[:8]
    domain = f"tour-{tag}.example"
    ws = members_store.create_workspace(f"Tour {tag}", domain, f"admin@{domain}", "Ada")
    members_store.set_password(f"admin@{domain}", "Ada", auth.hash_password(PASSWORD), ws, "admin")
    members_store.set_password(f"pat@{domain}", "Pat", auth.hash_password(PASSWORD), ws, "public")
    members_store.set_password(f"sam@{domain}", "Sam", auth.hash_password(PASSWORD), ws, "public")
    return ws, domain


def login(client, email):
    r = client.post("/api/auth/login", json={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def tours(client, h):
    return client.get("/api/onboarding", headers=h).json()["tours"]


def test_a_tour_seen_in_one_browser_is_seen_in_every_other(client):
    _, domain = workspace()
    first = login(client, f"pat@{domain}")
    assert tours(client, first) == {"members": {"seen": False, "finished_at": None,
                                                "skipped_at": None}}
    r = client.put("/api/onboarding/tours/members", headers=first, json={"outcome": "finished"})
    assert r.status_code == 200 and r.json()["members"]["seen"] is True
    other = login(client, f"pat@{domain}")                 # another sign-in, another browser
    seen = tours(client, other)["members"]
    assert seen["seen"] is True and seen["finished_at"] and seen["skipped_at"] is None
    # Each member's own: a colleague hasn't seen it.
    assert tours(client, login(client, f"sam@{domain}"))["members"]["seen"] is False


def test_skipping_counts_as_seen_and_the_first_time_is_kept(client):
    _, domain = workspace()
    h = login(client, f"sam@{domain}")
    skipped = client.put("/api/onboarding/tours/members", headers=h,
                         json={"outcome": "skipped"}).json()["members"]
    assert skipped["seen"] is True and skipped["finished_at"] is None
    # Taken again from the account menu and finished: both are kept, the skip unchanged.
    again = client.put("/api/onboarding/tours/members", headers=h,
                       json={"outcome": "finished"}).json()["members"]
    assert again["skipped_at"] == skipped["skipped_at"] and again["finished_at"]


def test_the_admin_tour_is_for_admins(client):
    _, domain = workspace()
    member = login(client, f"pat@{domain}")
    assert "admin" not in tours(client, member)
    r = client.put("/api/onboarding/tours/admin", headers=member, json={"outcome": "finished"})
    assert r.status_code == 403
    admin = login(client, f"admin@{domain}")
    assert tours(client, admin)["admin"]["seen"] is False
    r = client.put("/api/onboarding/tours/admin", headers=admin, json={"outcome": "finished"})
    assert r.status_code == 200 and r.json()["admin"]["seen"] is True
    assert r.json()["members"]["seen"] is False             # one tour doesn't mark the other


def test_only_known_tours_and_outcomes(client):
    _, domain = workspace()
    h = login(client, f"pat@{domain}")
    assert client.put("/api/onboarding/tours/other", headers=h,
                      json={"outcome": "finished"}).status_code == 422
    assert client.put("/api/onboarding/tours/members", headers=h,
                      json={"outcome": "maybe"}).status_code == 422
    assert client.put("/api/onboarding/tours/members", json={"outcome": "finished"}).status_code \
        == 401


def test_a_read_only_workspace_still_takes_it(client, monkeypatch):
    from kb.services import billing
    ws, domain = workspace()
    h = login(client, f"pat@{domain}")
    monkeypatch.setenv("BILLING", "on")
    monkeypatch.setattr(billing, "state",
                        lambda w: {"read_only": True, "expired": True} if w == ws else None)
    billing.check_writable(ws, "PUT", "/api/onboarding/tours/members")       # no 402
    with pytest.raises(HTTPException):
        billing.check_writable(ws, "PUT", "/api/onboarding/dismissed")
    r = client.put("/api/onboarding/tours/members", headers=h, json={"outcome": "skipped"})
    assert r.status_code == 200 and r.json()["members"]["seen"] is True


def test_a_data_request_lists_the_tours_and_erasure_deletes_them():
    from kb.services import dsar, onboarding
    ws, domain = workspace()
    pat, sam = f"pat@{domain}", f"sam@{domain}"
    onboarding.mark_tour(ws, pat, "members", "finished")
    onboarding.mark_tour(ws, sam, "members", "skipped")
    assert dsar.export_member(ws, pat)["tours"]["members"]["seen"] is True
    _, counts = dsar.erase_member(ws, pat)
    assert counts["tours"] == 1
    assert onboarding.tours(ws, pat)["members"]["seen"] is False
    assert onboarding.tours(ws, sam)["members"]["seen"] is True           # theirs alone


def test_removing_a_member_forgets_their_tours(client):
    from kb.services import onboarding
    ws, domain = workspace()
    pat, sam = f"pat@{domain}", f"sam@{domain}"
    onboarding.mark_tour(ws, pat, "members", "finished")
    onboarding.mark_tour(ws, sam, "members", "finished")
    admin = login(client, f"admin@{domain}")
    assert client.delete(f"/api/admin/members/{pat}", headers=admin).status_code == 200
    assert onboarding.tours(ws, pat)["members"]["seen"] is False
    assert onboarding.tours(ws, sam)["members"]["seen"] is True           # theirs alone


def test_the_admin_checklist_doesnt_read_anyones_tour():
    import onboarding_store
    from kb.services import onboarding
    ws, domain = workspace()
    onboarding.mark_tour(ws, f"pat@{domain}", "members", "finished")
    onboarding.dismiss(ws)
    done = onboarding_store.done_steps(ws)
    assert "dismissed" in done and not any(s.startswith("tour:") for s in done)


def test_saving_a_tour_reads_them_once(client, monkeypatch):
    from kb.services import onboarding
    _, domain = workspace()
    h = login(client, f"pat@{domain}")
    calls = []
    real = onboarding.tours
    monkeypatch.setattr(onboarding, "tours", lambda *a: calls.append(a) or real(*a))
    r = client.put("/api/onboarding/tours/members", headers=h, json={"outcome": "finished"})
    assert r.status_code == 200 and set(r.json()) == {"members"}       # no admin tour for them
    assert len(calls) == 1
