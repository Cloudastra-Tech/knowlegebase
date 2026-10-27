"""
Personal data (platform.md PL-13, pii.py): the locale rule packs with their
checksums, the workspace's policy for answers (its own email domains allowed by
default), the question log and the ingest-time scan, the settings API, and the
Knowledge screen's flag on documents with dense personal data.
"""

import pytest

import pii
import question_log_store
from agent import build_agent
from eval import harness
from kb.services import assistant, question_log
from test_citations import citing, corpus_index  # noqa: F401  (fixtures)
from test_indexing import env  # noqa: F401  (a fixture)


# ---- The rule packs -------------------------------------------------------------------------
@pytest.mark.parametrize("text, kind", [
    ("mail priya@acme.example today", "EMAIL"),
    ("call 9876543210 now", "PHONE"),
    ("call +91 9876543210 now", "PHONE"),
    ("card 4111 1111 1111 1111 on file", "CARD"),
    ("card 4111-1111-1111-1111 on file", "CARD"),
    ("amex 3782 822463 10005 on file", "CARD"),
    ("card 5555555555554444 on file", "CARD"),
    ("pay GB82 WEST 1234 5698 7654 32 please", "IBAN"),
    ("pay DE89370400440532013000 please", "IBAN"),
    ("ssn 123-45-6789 on file", "US_SSN"),
    ("NI AB123456C on file", "UK_NI"),
    ("NI AB 12 34 56 C on file", "UK_NI"),
    ("PAN ABCPE1234F on file", "IN_PAN"),
    ("Aadhaar 1234 5678 9012 on file", "IN_AADHAAR"),
])
def test_each_pack_finds_its_type(text, kind):
    found = pii.find(text)
    assert [f[2] for f in found] == [kind], found
    masked = pii.redact(text)
    assert f"[REDACTED_{pii.MASKS[kind]}]" in masked and found[0][3] not in masked


@pytest.mark.parametrize("text", [
    "card 4111 1111 1111 1112 fails the Luhn check",
    "order 1234567890123456 is not a card",
    "pay GB82WEST12345698765433 fails mod 97",
    "ssn 666-12-3456 and 000-12-3456 and 123-00-4567 are never issued",
    "NI GB123456C and QQ123456C use prefixes never issued",
    "PAN ABCXE1234F has no holder type X",
    "version 1.2.3, room 101, 2026-10-27, 30 days",
])
def test_lookalikes_are_left_alone(text):
    assert pii.find(text) == [] and pii.redact(text) == text


def test_luhn():
    assert pii.luhn("4111111111111111") and pii.luhn("378282246310005")
    assert pii.luhn("4111 1111 1111 1111")
    assert not pii.luhn("4111111111111112") and not pii.luhn("411111")
    assert pii.iban_ok("GB82 WEST 1234 5698 7654 32") and not pii.iban_ok("GB00WEST12345698765432")


def test_only_the_types_asked_for_are_masked():
    text = "priya@acme.example, 9876543210"
    assert pii.redact(text, ["PHONE"]) == "priya@acme.example, [REDACTED_PHONE]"
    assert pii.count("a@x.example b@x.example a@x.example 9876543210") == {"EMAIL": 2,
                                                                           "PHONE": 1}
    assert pii.describe({"PHONE": 40, "EMAIL": 1}) == "40 phone numbers, 1 email address"


# ---- The policy -----------------------------------------------------------------------------
def test_the_workspaces_own_domains_stay_in_answers():
    policy = pii.Policy(None, own_domains=["acme.example"])
    text = "Ask priya@acme.example or hr@eu.acme.example, not mallory@evil.example."
    assert policy.answer(text) == ("Ask priya@acme.example or hr@eu.acme.example, not "
                                   "[REDACTED_EMAIL].")
    off = pii.Policy({"allow_own_domains": False}, own_domains=["acme.example"])
    assert off.answer(text).count("[REDACTED_EMAIL]") == 3
    extra = pii.Policy({"allow_own_domains": False, "allowed_domains": ["evil.example"]})
    assert "mallory@evil.example" in extra.answer(text)
    # Lookalike domains aren't the workspace's.
    assert "[REDACTED_EMAIL]" in policy.answer("x@notacme.example")
    # The log masks every email whatever the answers allow.
    assert policy.log(text).count("[REDACTED_EMAIL]") == 3


def test_a_policy_is_checked():
    for bad in ({"answers": ["NOPE"]}, {"answers": "EMAIL"}, {"colour": "red"},
                {"allowed_domains": ["not a domain"]}, {"dense_min": "many"},
                {"allowed_domains": [f"d{n}.example" for n in range(25)]}):
        with pytest.raises(pii.Invalid):
            pii.clean_policy(bad)
    clean = pii.clean_policy({"answers": ["PHONE", "EMAIL"], "allowed_domains": ["@Partner.Example"]})
    assert clean["answers"] == ["EMAIL", "PHONE"] and clean["allowed_domains"] == ["partner.example"]
    assert clean["logs"] == list(pii.ALL)                               # defaults for the rest
    assert pii.load("{broken") == pii.DEFAULT_POLICY


def test_flagging_follows_the_policy():
    p = pii.Policy({"ingest": ["PHONE"], "dense_min": 10})
    assert p.flagged({"PHONE": 12, "EMAIL": 50}) == {"PHONE": 12}
    assert p.flagged({"PHONE": 9, "EMAIL": 50}) == {}


# ---- Where it applies -----------------------------------------------------------------------
def test_answers_are_masked_with_the_workspaces_policy(citing):  # noqa: F811
    citing["reply"] = ("Ask priya@acme.example or call 9876543210; the PAN is ABCPE1234F; "
                       "outside: x@gmail.example.")
    policy = pii.Policy({"answers": ["EMAIL", "PHONE"]}, own_domains=["acme.example"])
    answer, _ = build_agent("public", harness.COLLECTION, outline=False, pii=policy)(
        "Who handles refunds?")
    assert "priya@acme.example" in answer and "[REDACTED_PHONE]" in answer
    assert "ABCPE1234F" in answer                                       # not in this policy
    assert "x@gmail.example" not in answer
    # Without a policy: every type, no domain kept (as before).
    answer, _ = build_agent("public", harness.COLLECTION, outline=False)("Who handles refunds?")
    assert "priya@acme.example" not in answer and "ABCPE1234F" not in answer


def test_the_question_log_masks_what_the_policy_says(env):  # noqa: F811
    import assistant_store
    w = env.workspace()
    question_log_store.set_settings(w, "redacted", 90)
    assistant_store.save_settings(w, {"pii_policy": {"logs": ["PHONE"]}}, "admin")
    question_log.record(w, {"username": "a"}, "public", "web",
                        "Call 9876543210 or mail priya@acme.example", "answered",
                        answer="ok", language="en", unsupported=0)
    row = question_log_store.rows(w)[-1]
    assert row["question"] == "Call [REDACTED_PHONE] or mail priya@acme.example"
    assert row["language"] == "en" and row["unsupported"] == 0


def test_the_settings_api_saves_and_checks_the_policy(env):  # noqa: F811
    import members_store
    w = env.workspace()
    members_store.add_verified_domain(w, f"{w}.example")
    admin = env.login(w)
    got = env.client.get("/api/admin/assistant", headers=admin).json()
    assert got["pii_policy"] == pii.DEFAULT_POLICY and got["own_domains"] == [f"{w}.example"]
    assert {e["id"] for e in got["choices"]["pii_entities"]} == set(pii.ALL)
    assert got["choices"]["pii_packs"]["in"] == ["IN_PAN", "IN_AADHAAR"]
    r = env.client.put("/api/admin/assistant", headers=admin,
                       json={"pii_policy": {"answers": ["CARD"], "allow_own_domains": False}})
    assert r.status_code == 200 and r.json()["pii_policy"]["answers"] == ["CARD"]
    assert assistant.pii_policy(w).answer(f"x@{w}.example 9876543210") == \
        f"x@{w}.example 9876543210"                                     # only cards now
    env.client.put("/api/admin/assistant", headers=admin,
                   json={"pii_policy": {"answers": ["EMAIL"], "allow_own_domains": False}})
    assert assistant.pii_policy(w).answer(f"x@{w}.example") == "[REDACTED_EMAIL]"
    assert env.client.put("/api/admin/assistant", headers=admin,
                          json={"pii_policy": {"answers": ["NOPE"]}}).status_code == 400
    # null: back to the defaults.
    r = env.client.put("/api/admin/assistant", headers=admin, json={"pii_policy": None})
    assert r.json()["pii_policy"] == pii.DEFAULT_POLICY
    member = env.login(w, "staff")
    assert env.client.put("/api/admin/assistant", headers=member,
                          json={"pii_policy": None}).status_code == 403


PHONES = "\n".join(f"Branch {n}: call 98765432{n:02d} for help." for n in range(12))


def test_dense_personal_data_is_flagged_on_the_knowledge_screen(env):  # noqa: F811
    import assistant_store
    from kb.services import indexing
    w = env.workspace(("public", "branches.txt", PHONES),
                      ("public", "handbook.txt", "Refunds take 30 days. Mail help@x.example."))
    admin, member = env.login(w), env.login(w, "staff")
    indexing.schedule_index(w)
    env.wait(w)
    items = {i["doc_key"]: i for i in
             env.client.get("/api/knowledge", headers=admin).json()["items"]}
    assert items["public/branches.txt"]["pii"] == {"counts": {"PHONE": 12},
                                                   "text": "contains 12 phone numbers"}
    assert items["public/handbook.txt"]["pii"] is None                # 1 value: not dense
    # Members never see the flag.
    assert all("pii" not in i for i in
               env.client.get("/api/knowledge", headers=member).json()["items"])
    # The policy decides what's flagged, without scanning again.
    assistant_store.save_settings(w, {"pii_policy": {"ingest": ["EMAIL"]}}, "admin")
    items = {i["doc_key"]: i for i in
             env.client.get("/api/knowledge", headers=admin).json()["items"]}
    assert items["public/branches.txt"]["pii"] is None
    # A deleted document's counts go at the next index run.
    from kb.services import pii_scan
    assert "public/branches.txt" in pii_scan.stored(w)
    env.client.delete("/api/docs/public/branches.txt", headers=admin)
    env.wait(w)
    assert "public/branches.txt" not in pii_scan.stored(w)
