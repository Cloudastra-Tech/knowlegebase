"""
Email-in (kb/routers/email_in.py, kb/services/email_in.py, email_in_store.py): the
provider's webhook needs its credentials; a workspace answers email only once an
admin turns it on; only a member of that workspace whose email the provider
authenticated (SPF pass, DKIM aligned) is answered, with their role as it is
now, and everyone else is dropped without a reply; automatic mail, lists and
our own mail are never answered; an email delivered twice is answered once; the
reply is in the thread (In-Reply-To, References, Auto-Submitted) and a reply to
it continues the conversation; senders are rate-limited; questions are logged
with surface "email".

Through the real API, job runner, search index and agent (fake models, as in
test_api_keys.py); Postmark's inbound webhook bodies are recorded fixtures, and
the mailer's Postmark API is faked, so nothing leaves the machine.
"""

import base64
import json
import pathlib
import uuid

import httpx
import pytest

import audit_store
import conversations_store
import db
import email_in_store
import members_store
from kb import jobs, state
from test_api_keys import kb  # noqa: F401  (a fixture)
from test_indexing import env  # noqa: F401  (a fixture kb needs)

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "email_in" /
                      "postmark_inbound.json").read_text())
DOMAIN = "kb.example.net"
USER, PASSWORD = "postmark", "a-long-webhook-password"
QUESTION = "When does the Brightlearn vendor contract end?"
SECRET_WORDS = "March 2027"            # only in the restricted contract
PUBLIC_WORDS = "guide.txt"             # the public guide (cited by name)


def basic(user=USER, password=PASSWORD):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def _fill(value, fields):
    if isinstance(value, str):
        for k, v in fields.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, fields) for k, v in value.items() if k != "_comment"}
    if isinstance(value, list):
        return [_fill(v, fields) for v in value]
    return value


def inbound(sender, to, subject=QUESTION, text="", message_id=None, headers=(), drop=()):
    """A Postmark inbound body: the recorded one with these values, extra headers
    (name, value), and the headers named in `drop` removed."""
    body = _fill(FIXTURE, {"sender": sender, "to": to, "subject": subject, "text": text,
                           "message_id": message_id or f"<{uuid.uuid4().hex}@mail.example>"})
    body["Headers"] = [h for h in body["Headers"] if h["Name"] not in drop]
    body["Headers"] += [{"Name": k, "Value": v} for k, v in headers]
    return body


@pytest.fixture
def mail(kb, monkeypatch):  # noqa: F811
    """A workspace with documents (kb) and email-in on, the host's settings in the
    environment, jobs run by hand, and the mailer's Postmark API faked."""
    w = kb.workspace_with_docs()
    monkeypatch.setenv("EMAIL_IN_DOMAIN", DOMAIN)
    monkeypatch.setenv("EMAIL_IN_WEBHOOK_USER", USER)
    monkeypatch.setenv("EMAIL_IN_WEBHOOK_PASSWORD", PASSWORD)
    monkeypatch.setenv("MAIL_BACKEND", "postmark")
    monkeypatch.setenv("POSTMARK_TOKEN", "pm-test-token")
    monkeypatch.setenv("MAIL_FROM", "Knowledge assistant <kb@kb.example.net>")
    monkeypatch.setenv("PUBLIC_URL", "https://kb.example.com")
    monkeypatch.setenv("JOBS_IN_PROCESS", "off")
    jobs.stop_in_process()
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        assert url == "https://api.postmarkapp.com/email", url
        sent.append(json)
        return httpx.Response(200, json={"MessageID": "x"})
    monkeypatch.setattr(httpx, "post", post)
    email_in_store.set_enabled(w, True, f"admin@{w}.example")
    kb.ws, kb.sent, kb.to = w, sent, f"ask@{w}.{DOMAIN}"

    def send(body, headers=None):
        r = kb.client.post("/api/email-in/postmark", json=body,
                           headers=basic() if headers is None else headers)
        return r

    def run_jobs():
        runner = jobs.Runner(handlers={"email_in": jobs.HANDLERS["email_in"]}, threads=0)
        while runner.run_once():
            pass
        conn = db.connect()
        try:
            rows = conn.execute("SELECT status, result, error FROM jobs WHERE kind = 'email_in' "
                                "ORDER BY id").fetchall()
        finally:
            conn.close()
        return [{"status": r["status"], **json.loads(r["result"] or "{}"), "error": r["error"]}
                for r in rows]

    def ask(sender, **kw):
        body = inbound(sender, kw.pop("to", kb.to), **kw)
        r = send(body)
        assert r.status_code == 200, r.text
        return body

    kb.send, kb.run_jobs, kb.ask = send, run_jobs, ask
    yield kb
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM jobs WHERE kind = 'email_in'")
    conn.close()


def message_id_of(body):
    return next(h["Value"] for h in body["Headers"] if h["Name"] == "Message-ID")


def status_of(body):
    return email_in_store.get(message_id_of(body))


def header(sent, name):
    return next((h["Value"] for h in sent.get("Headers") or [] if h["Name"] == name), None)


# ---- The webhook is the provider's -----------------------------------------------------------
@pytest.mark.parametrize("headers", [{}, basic(password="wrong"), basic(user="someone"),
                                     {"Authorization": "Bearer " + PASSWORD},
                                     {"Authorization": "Basic not-base64!"}])
def test_the_webhook_needs_its_credentials(mail, headers):
    r = mail.send(inbound(f"hr@{mail.ws}.example", mail.to), headers=headers)
    assert r.status_code == 403
    assert mail.run_jobs() == [] and mail.sent == []


def test_the_webhook_is_closed_when_the_server_has_no_email_in(mail, monkeypatch):
    monkeypatch.delenv("EMAIL_IN_WEBHOOK_PASSWORD")
    r = mail.send(inbound(f"hr@{mail.ws}.example", mail.to))
    assert r.status_code == 403


def test_a_workspace_answers_email_only_once_turned_on(mail):
    w = mail.ws
    email_in_store.set_enabled(w, False, "x")
    body = mail.ask(f"hr@{w}.example")
    assert status_of(body)["status"] == "dropped" and status_of(body)["reason"] == "off"
    assert mail.run_jobs() == [] and mail.sent == []
    other = mail.workspace()                               # never turned on
    assert email_in_store.settings(other)["enabled"] is False


# ---- Answered: a member, authenticated, with their role ----------------------------------------
def test_an_hr_member_is_answered_in_the_thread_with_what_they_can_see(mail):
    w = mail.ws
    body = mail.ask(f"hr@{w}.example", text="Thanks!")
    assert mail.run_jobs()[0]["status"] == "done"
    (reply,) = mail.sent
    assert reply["To"] == f"hr@{w}.example"
    assert reply["Subject"] == "Re: " + QUESTION
    assert reply["ReplyTo"] == mail.to
    assert SECRET_WORDS in reply["TextBody"] and "Sources:" in reply["TextBody"]
    assert "[S" not in reply["TextBody"]                   # markers are numbers now
    assert "can see in" in reply["TextBody"]
    assert header(reply, "In-Reply-To") == message_id_of(body)
    assert message_id_of(body) in header(reply, "References")
    assert header(reply, "Auto-Submitted") == "auto-replied"
    row = status_of(body)
    assert row["status"] == "answered" and row["reply_message_id"] == header(reply, "Message-ID")


def test_a_public_member_gets_public_documents_only(mail):
    mail.ask(f"staff@{mail.ws}.example", subject="Refunds and the Brightlearn contract",
             text="When does the Brightlearn vendor contract end, and how are refunds paid?")
    mail.run_jobs()
    (reply,) = mail.sent
    assert SECRET_WORDS not in json.dumps(reply)
    assert PUBLIC_WORDS in reply["TextBody"]


def test_questions_are_logged_with_surface_email_and_audited(mail):
    w = mail.ws
    mail.ask(f"hr@{w}.example")
    mail.run_jobs()
    conn = db.connect()
    try:
        logged = conn.execute("SELECT surface, user, role FROM question_log WHERE workspace = ?",
                              (w,)).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in logged] == [("email", f"hr@{w}.example", "hr")]
    events = audit_store.list_events(w, limit=50)[0]
    answered = [e for e in events if e["action"] == "email_in.answer"]
    assert answered and answered[0]["details"]["surface"] == "email"
    assert QUESTION not in json.dumps(events)              # never the question itself


def test_the_plus_address_reaches_the_workspace_too(mail):
    body = inbound(f"hr@{mail.ws}.example", f"ask+{mail.ws}@{DOMAIN}")
    body["MailboxHash"] = mail.ws
    assert mail.send(body).status_code == 200
    mail.run_jobs()
    assert len(mail.sent) == 1


def test_an_email_to_no_workspace_here_is_taken_and_ignored(mail):
    for to in (f"ask@nope.{DOMAIN}", f"help@{mail.ws}.{DOMAIN}", "ask@elsewhere.example"):
        assert mail.send(inbound(f"hr@{mail.ws}.example", to)).status_code == 200
    assert mail.run_jobs() == [] and mail.sent == []


# ---- Dropped, never answered -------------------------------------------------------------------
@pytest.mark.parametrize("case,reason,status", [
    ("stranger", "not_member", "refused"),
    ("other_workspace", "not_member", "refused"),
    ("disabled", "disabled", "refused"),
    ("spf_fail", "spf", "dropped"),
    ("no_spf", "spf", "dropped"),
    ("dkim_not_aligned", "dkim", "dropped"),
    ("forged_spf_header", "spf", "dropped"),
])
def test_only_authenticated_members_are_answered(mail, case, reason, status):
    w = mail.ws
    sender, kw = f"hr@{w}.example", {}
    if case == "stranger":
        sender = "someone@elsewhere.example"
    elif case == "other_workspace":
        other = mail.workspace()
        sender = f"staff@{other}.example"
    elif case == "disabled":
        members_store.set_disabled(f"staff@{w}.example", True, w)
        sender = f"staff@{w}.example"
    elif case == "spf_fail":
        kw = {"drop": ("Received-SPF",), "headers": [("Received-SPF", "Fail (not authorized)")]}
    elif case == "no_spf":
        kw = {"drop": ("Received-SPF",)}
    elif case == "dkim_not_aligned":
        kw = {"drop": ("X-Spam-Tests",), "headers": [("X-Spam-Tests", "DKIM_SIGNED,DKIM_VALID")]}
    elif case == "forged_spf_header":
        # The sender wrote a "pass" of their own; Postmark's own one says fail.
        kw = {"drop": ("Received-SPF",), "headers": [("Received-SPF", "Pass (forged)"),
                                                     ("Received-SPF", "SoftFail")]}
    body = mail.ask(sender, **kw)
    row = status_of(body)
    assert (row["status"], row["reason"]) == (status, reason)
    assert mail.run_jobs() == [] and mail.sent == []


def test_a_member_turned_off_after_sending_is_not_answered(mail):
    w = mail.ws
    mail.ask(f"staff@{w}.example")
    members_store.set_disabled(f"staff@{w}.example", True, w)
    assert mail.run_jobs()[0]["refused"] == "disabled"
    assert mail.sent == []


@pytest.mark.parametrize("headers,reason", [
    ([("Auto-Submitted", "auto-replied")], "auto_submitted"),
    ([("Auto-Submitted", "auto-generated")], "auto_submitted"),
    ([("Precedence", "bulk")], "precedence"),
    ([("Precedence", "list")], "precedence"),
    ([("List-Id", "<team.lists.example>")], "mailing_list"),
    ([("List-Unsubscribe", "<mailto:x@example>")], "mailing_list"),
    ([("X-Auto-Response-Suppress", "All")], "auto_response_suppress"),
    ([("Return-Path", "<>")], "null_return_path"),
])
def test_automatic_and_list_mail_is_dropped(mail, headers, reason):
    body = mail.ask(f"hr@{mail.ws}.example", headers=headers)
    assert status_of(body)["reason"] == reason
    assert mail.run_jobs() == [] and mail.sent == []


def test_auto_submitted_no_is_a_person(mail):
    mail.ask(f"hr@{mail.ws}.example", headers=[("Auto-Submitted", "no")])
    mail.run_jobs()
    assert len(mail.sent) == 1


@pytest.mark.parametrize("sender,reason", [
    ("kb@kb.example.net", "own_address"),
    ("ask@other.kb.example.net", "own_address"),
    ("mailer-daemon@{w}.example", "robot_sender"),
    ("no-reply@{w}.example", "robot_sender"),
])
def test_our_own_mail_and_robots_are_dropped(mail, sender, reason):
    body = mail.ask(sender.format(w=mail.ws))
    assert status_of(body)["reason"] == reason
    assert mail.run_jobs() == [] and mail.sent == []


def test_an_email_delivered_twice_is_answered_once(mail):
    body = inbound(f"hr@{mail.ws}.example", mail.to, message_id="<same-one@mail.example>")
    for _ in range(3):
        assert mail.send(body).status_code == 200
    mail.run_jobs()
    assert len(mail.sent) == 1


def test_a_sender_is_rate_limited(mail, monkeypatch):
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setenv("RATE_LIMIT_EMAIL_USER", "2")
    state.hits.clear()
    bodies = [mail.ask(f"hr@{mail.ws}.example") for _ in range(3)]
    assert [status_of(b)["status"] for b in bodies] == ["queued", "queued", "dropped"]
    assert status_of(bodies[2])["reason"] == "rate_limited"
    mail.ask(f"admin@{mail.ws}.example")                   # someone else still gets through
    monkeypatch.setenv("RATE_LIMITS", "off")
    mail.run_jobs()
    assert len(mail.sent) == 3
    state.hits.clear()


# ---- Threads -----------------------------------------------------------------------------------
def test_a_reply_to_our_answer_continues_the_conversation(mail):
    w = mail.ws
    first = mail.ask(f"hr@{w}.example")
    mail.run_jobs()
    ours = header(mail.sent[0], "Message-ID")
    conversation = status_of(first)["conversation_id"]
    follow = mail.ask(f"hr@{w}.example", subject="Re: " + QUESTION,
                      text="And does it renew automatically?",
                      headers=[("In-Reply-To", ours),
                               ("References", f"{message_id_of(first)} {ours}")])
    mail.run_jobs()
    assert status_of(follow)["conversation_id"] == conversation
    conv = conversations_store.get(w, f"hr@{w}.example", conversation)
    assert conv is not None
    second = mail.sent[1]
    assert second["Subject"] == "Re: " + QUESTION                # not "Re: Re: ..."
    assert header(second, "In-Reply-To") == message_id_of(follow)
    assert ours in header(second, "References")


def test_someone_else_replying_to_our_answer_starts_their_own(mail):
    w = mail.ws
    first = mail.ask(f"hr@{w}.example")
    mail.run_jobs()
    ours = header(mail.sent[0], "Message-ID")
    other = mail.ask(f"admin@{w}.example", headers=[("In-Reply-To", ours)])
    mail.run_jobs()
    assert status_of(other)["conversation_id"] != status_of(first)["conversation_id"]


def test_a_failed_answer_gets_a_short_reply(mail, monkeypatch):
    from kb.services import answering

    def fail(*args, **kwargs):
        raise answering.Failed("RateLimitError")
    monkeypatch.setattr(answering, "ask", fail)
    body = mail.ask(f"hr@{mail.ws}.example")
    mail.run_jobs()
    (reply,) = mail.sent
    assert "couldn't answer that just now (RateLimitError)" in reply["TextBody"]
    assert status_of(body)["status"] == "failed"


# ---- Quoted text, and the settings -----------------------------------------------------------
def test_the_question_leaves_out_the_quoted_thread():
    from kb.services import email_in
    text = email_in._strip_quoted("New question here\n\nOn Mon, 1 Jan, X <x@y> wrote:\n> old\n")
    assert text == "New question here"
    assert email_in._strip_quoted("Hi\n-- \nSig") == "Hi"


def test_admins_turn_it_on_and_hr_can_look(mail, monkeypatch):
    w = mail.ws
    email_in_store.set_enabled(w, False, "x")
    admin, hr, staff = mail.session(w), mail.session(w, "hr"), mail.session(w, "staff")
    r = mail.client.get("/api/admin/email-in", headers=hr)
    assert r.status_code == 200
    view = r.json()
    assert view == {**view, "enabled": False, "configured": True, "address": mail.to,
                    "provider": "postmark", "can_manage": False}
    assert view["webhook_url"] == "https://kb.example.com/api/email-in/postmark"
    assert mail.client.put("/api/admin/email-in", headers=hr,
                           json={"enabled": True}).status_code == 403
    assert mail.client.get("/api/admin/email-in", headers=staff).status_code == 403
    r = mail.client.put("/api/admin/email-in", headers=admin, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    events = [e for e in audit_store.list_events(w, limit=50)[0]
              if e["action"] == "email_in.settings"]
    assert events and events[0]["details"]["enabled"] == [False, True]
    monkeypatch.delenv("EMAIL_IN_DOMAIN")
    r = mail.client.put("/api/admin/email-in", headers=admin, json={"enabled": True})
    assert r.status_code == 400 and "EMAIL_IN_DOMAIN" in r.json()["detail"]
