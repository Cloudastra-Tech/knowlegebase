"""mailer.py: the templates, and each way of sending (SMTP and the HTTP APIs are
faked; nothing leaves the machine)."""

import logging

import httpx
import pytest

import mailer

INVITE = {"inviter": "Ada <script>", "workspace_name": "Acme & Co", "role_label": "an admin",
          "link": "https://kb.example/invite/abc", "expires": "3 November 2026 (UTC)"}


def test_templates_have_text_and_escaped_html():
    subject, text, page = mailer.render("invite", **INVITE)
    assert subject == "Ada <script> invited you to Acme & Co"
    assert "https://kb.example/invite/abc" in text and "Acme & Co" in text
    assert "Ada &lt;script&gt;" in page and "Acme &amp; Co" in page and "<script>" not in page
    assert 'href="https://kb.example/invite/abc"' in page
    assert mailer.render("member_removed", workspace_name="Acme")[0] == "You were removed from Acme"
    assert mailer.render("notice", title="Heads up", body="Hi")[1] == "Hi\n"


def test_the_console_backend_logs_and_sends_nothing(monkeypatch, caplog):
    monkeypatch.delenv("MAIL_BACKEND", raising=False)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        mailer.send("pat@acme.example", "invite", **INVITE)
    assert "pat@acme.example" in caplog.text and "https://kb.example/invite/abc" in caplog.text


def test_an_unknown_backend_is_an_error(monkeypatch):
    monkeypatch.setenv("MAIL_BACKEND", "pigeon")
    with pytest.raises(mailer.MailError, match="MAIL_BACKEND"):
        mailer.send("pat@acme.example", "notice", title="t", body="b")


class FakeSMTP:
    """smtplib.SMTP / SMTP_SSL, recording what was done."""
    made = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = [("connect", host, port, "ssl" if context else "plain")]
        FakeSMTP.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["From"], msg["To"], msg["Subject"]))
        self.message = msg


@pytest.fixture
def smtp(monkeypatch):
    import smtplib
    FakeSMTP.made = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    for name, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "mail.example",
                        "SMTP_USERNAME": "kb", "SMTP_PASSWORD": "pw",
                        "MAIL_FROM": "KB <kb@acme.example>"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_SECURITY", raising=False)
    return monkeypatch


def test_smtp_uses_starttls_then_logs_in(smtp):
    mailer.send("pat@acme.example", "invite", **INVITE)
    (server,) = FakeSMTP.made
    assert server.calls == [("connect", "mail.example", 587, "plain"), ("starttls",),
                            ("login", "kb", "pw"),
                            ("send", "KB <kb@acme.example>", "pat@acme.example",
                             "Ada <script> invited you to Acme & Co"), ("quit",)]
    parts = [p.get_content_type() for p in server.message.iter_parts()]
    assert parts == ["text/plain", "text/html"]


def test_smtp_over_ssl(smtp):
    smtp.setenv("SMTP_SECURITY", "ssl")
    mailer.send("pat@acme.example", "notice", title="t", body="b")
    assert FakeSMTP.made[0].calls[0] == ("connect", "mail.example", 465, "ssl")
    assert ("starttls",) not in FakeSMTP.made[0].calls


def test_an_smtp_failure_is_a_mail_error_without_the_password(smtp):
    import smtplib

    def refuse(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials pw")
    smtp.setattr(FakeSMTP, "login", refuse)
    with pytest.raises(mailer.MailError) as e:
        mailer.send("pat@acme.example", "notice", title="t", body="b")
    assert "SMTPAuthenticationError" in str(e.value) and "pw" not in str(e.value)
    smtp.delenv("SMTP_HOST")
    with pytest.raises(mailer.MailError, match="SMTP_HOST"):
        mailer.send("pat@acme.example", "notice", title="t", body="b")


@pytest.mark.parametrize("backend,key,url", [
    ("resend", "RESEND_API_KEY", "https://api.resend.com/emails"),
    ("postmark", "POSTMARK_TOKEN", "https://api.postmarkapp.com/email"),
])
def test_the_http_apis(monkeypatch, backend, key, url):
    sent = []

    def post(u, json=None, headers=None, timeout=None):
        sent.append((u, json, headers))
        return httpx.Response(200 if len(sent) == 1 else 422)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("MAIL_BACKEND", backend)
    monkeypatch.setenv("MAIL_FROM", "KB <kb@acme.example>")
    monkeypatch.delenv(key, raising=False)
    with pytest.raises(mailer.MailError, match=key):
        mailer.send("pat@acme.example", "invite", **INVITE)
    monkeypatch.setenv(key, "k-123")
    mailer.send("pat@acme.example", "invite", **INVITE)
    (u, body, headers), = sent
    assert u == url and "k-123" in str(headers)
    assert "https://kb.example/invite/abc" in str(body)
    with pytest.raises(mailer.MailError, match="HTTP 422"):
        mailer.send("pat@acme.example", "invite", **INVITE)


# ---- Replies in a thread (email-in) -----------------------------------------------------------
REPLY = mailer.Reply(subject="Re: Refunds?", in_reply_to="<q2@mail.example>",
                     references=["<q1@mail.example>", "<a1@kb.example>"],
                     reply_to="ask@acme.kb.example")
ANSWER = {"answer": "Within thirty days [1]", "sources": "Sources:\n[1] Guide\n",
          "footer": "Answered from what Pat can see."}


def test_a_reply_has_threading_headers_and_says_it_is_automatic(smtp):
    message_id = mailer.send("pat@acme.example", "email_answer", reply=REPLY, **ANSWER)
    msg = FakeSMTP.made[0].message
    assert msg["Subject"] == "Re: Refunds?" and msg["Message-ID"] == message_id
    assert message_id.startswith("<") and message_id.endswith("@acme.example>")
    assert msg["In-Reply-To"] == "<q2@mail.example>"
    assert msg["References"] == "<q1@mail.example> <a1@kb.example> <q2@mail.example>"
    assert msg["Auto-Submitted"] == "auto-replied" and msg["Reply-To"] == "ask@acme.kb.example"
    assert "Within thirty days [1]" in msg.get_body(("plain",)).get_content()


def test_a_reply_can_come_from_its_own_address(smtp):
    reply = mailer.Reply(subject="Re: x", from_address="Ask <ask@acme.kb.example>")
    message_id = mailer.send("pat@acme.example", "email_answer", reply=reply, **ANSWER)
    msg = FakeSMTP.made[0].message
    assert msg["From"] == "Ask <ask@acme.kb.example>" and "@acme.kb.example>" in message_id
    assert msg["In-Reply-To"] is None and msg["References"] is None


@pytest.mark.parametrize("backend,key", [("resend", "RESEND_API_KEY"),
                                         ("postmark", "POSTMARK_TOKEN")])
def test_the_http_apis_send_a_replys_headers(monkeypatch, backend, key):
    sent = []

    def post(u, json=None, headers=None, timeout=None):
        sent.append(json)
        return httpx.Response(200)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("MAIL_BACKEND", backend)
    monkeypatch.setenv(key, "k-123")
    monkeypatch.setenv("MAIL_FROM", "KB <kb@acme.example>")
    message_id = mailer.send("pat@acme.example", "email_answer", reply=REPLY, **ANSWER)
    (body,) = sent
    if backend == "postmark":
        headers = {h["Name"]: h["Value"] for h in body["Headers"]}
        assert body["ReplyTo"] == "ask@acme.kb.example"
    else:
        headers = body["headers"]
        assert body["reply_to"] == "ask@acme.kb.example"
    assert headers["Message-ID"] == message_id and headers["Auto-Submitted"] == "auto-replied"
    assert headers["In-Reply-To"] == "<q2@mail.example>"
    # Without a reply, nothing changes.
    sent.clear()
    assert mailer.send("pat@acme.example", "notice", title="t", body="b") is None
    assert "Headers" not in sent[0] and "headers" not in sent[0]


def test_the_console_backend_logs_a_replys_headers(monkeypatch, caplog):
    monkeypatch.delenv("MAIL_BACKEND", raising=False)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        message_id = mailer.send("pat@acme.example", "email_answer", reply=REPLY, **ANSWER)
    assert message_id and "In-Reply-To" in caplog.text
