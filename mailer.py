"""
mailer.py  —  the app's outgoing email: invites, and notices to members.

Which way it goes is set in the environment (.env):

    MAIL_BACKEND=console    the default: nothing is sent, the email is written to
                            the server log (for a developer's machine: copy the
                            link from there)
    MAIL_BACKEND=smtp       SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD;
                            STARTTLS unless SMTP_SECURITY=ssl (port 465) or none
                            (a local relay only: the password would go in clear)
    MAIL_BACKEND=resend     RESEND_API_KEY      (https://resend.com)
    MAIL_BACKEND=postmark   POSTMARK_TOKEN      (https://postmarkapp.com, a server token)
    MAIL_FROM               the sender, e.g. "Knowledge assistant <kb@example.com>"

send(to, template, **values) renders one of TEMPLATES (a subject, a plain-text
body and a simple HTML one) and sends it; it raises MailError if it couldn't.
Values are escaped in the HTML.

A reply (email-in's answers, kb/services/email_in.py) also passes `reply`: a
Reply with the subject, the threading headers (In-Reply-To, References), the
Reply-To and, optionally, its own From. Every reply is marked
"Auto-Submitted: auto-replied" (RFC 3834), so other automatic responders don't
answer it, and gets a Message-ID of ours, which send() returns.
"""

import html
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

import httpx

log = logging.getLogger("uvicorn.error")

BACKENDS = ("console", "smtp", "resend", "postmark")
TIMEOUT = 15                         # seconds, for SMTP and the HTTP APIs
DEFAULT_FROM = "Knowledge assistant <no-reply@localhost>"


class MailError(Exception):
    """The email wasn't sent. The message says why, without any secret."""


# ---- Templates -------------------------------------------------------------------
# Each: (subject, text, html), formatted with the values send() is given. The HTML
# is a single plain column; every value in it is escaped first.
TEMPLATES = {
    "invite": (
        "{inviter} invited you to {workspace_name}",
        "{inviter} invited you to the {workspace_name} workspace of the Knowledge "
        "assistant, as {role_label}.\n\n"
        "Accept the invite:\n{link}\n\n"
        "The link works once and expires on {expires}. If you weren't expecting it, "
        "ignore this email.\n",
        "<p>{inviter} invited you to the <strong>{workspace_name}</strong> workspace of the "
        "Knowledge assistant, as {role_label}.</p>"
        '<p><a href="{link}" style="display:inline-block;padding:10px 16px;border-radius:10px;'
        'background:#18181b;color:#fafafa;text-decoration:none">Accept the invite</a></p>'
        '<p style="color:#71717a;font-size:13px">Or open this link: {link}<br>'
        "It works once and expires on {expires}. If you weren't expecting it, ignore this "
        "email.</p>",
    ),
    "member_removed": (
        "You were removed from {workspace_name}",
        "An admin removed you from the {workspace_name} workspace of the Knowledge "
        "assistant. You've been signed out.\n\nIf you think this is a mistake, ask your "
        "company's admin.\n",
        "<p>An admin removed you from the <strong>{workspace_name}</strong> workspace of the "
        "Knowledge assistant. You've been signed out.</p>"
        '<p style="color:#71717a;font-size:13px">If you think this is a mistake, ask your '
        "company's admin.</p>",
    ),
    "verified_review": (
        "Verified answer to review: {title}",
        "The verified answer \"{title}\" in the {workspace_name} workspace {state}.\n\n"
        "You own it. Check it still matches the documents, then mark it reviewed (or edit "
        "it):\n{link}\n",
        "<p>The verified answer <strong>{title}</strong> in the {workspace_name} workspace "
        "{state}.</p>"
        "<p>You own it. Check it still matches the documents, then mark it reviewed (or "
        "edit it).</p>"
        '<p><a href="{link}" style="display:inline-block;padding:10px 16px;border-radius:10px;'
        'background:#18181b;color:#fafafa;text-decoration:none">Review it</a></p>',
    ),
    "notice": (
        "{title}",
        "{body}\n",
        "<p><strong>{title}</strong></p><p>{body}</p>",
    ),
    # Email-in's answer (kb/services/email_in.py); its subject is the Reply's.
    "email_answer": (
        "{subject}",
        "{answer}\n{sources}\n-- \n{footer}\n",
        '<div style="white-space:pre-wrap">{answer}</div>'
        '<div style="white-space:pre-wrap;color:#52525b;font-size:13px;margin-top:12px">'
        "{sources}</div>"
        '<p style="color:#71717a;font-size:13px;border-top:1px solid #e4e4e7;padding-top:8px">'
        "{footer}</p>",
    ),
}


@dataclass
class Reply:
    """What makes an email a reply in its thread (RFC 5322 3.6.4): its subject, the
    message it answers (In-Reply-To) and that thread's References; where a reply
    to it goes (Reply-To), and its From when it isn't MAIL_FROM."""
    subject: str
    in_reply_to: str | None = None
    references: list = field(default_factory=list)
    reply_to: str | None = None
    from_address: str | None = None

    def headers(self, message_id):
        out = {"Message-ID": message_id, "Auto-Submitted": "auto-replied"}
        if self.in_reply_to:
            out["In-Reply-To"] = self.in_reply_to
        refs = [r for r in self.references if r]
        if self.in_reply_to and self.in_reply_to not in refs:
            refs.append(self.in_reply_to)
        if refs:
            out["References"] = " ".join(refs[-20:])
        return out


def render(template, **values):
    """(subject, text, html) of a template with these values."""
    subject, text, page = TEMPLATES[template]
    escaped = {k: html.escape(str(v), quote=True) for k, v in values.items()}
    body = ('<div style="font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;'
            f'color:#18181b;max-width:520px">{page.format(**escaped)}</div>')
    return subject.format(**values), text.format(**values), body


# ---- Sending ---------------------------------------------------------------------
def backend():
    name = os.getenv("MAIL_BACKEND", "console").strip().lower() or "console"
    if name not in BACKENDS:
        raise MailError(f"MAIL_BACKEND must be one of: {', '.join(BACKENDS)}.")
    return name


def sender():
    return os.getenv("MAIL_FROM", "").strip() or DEFAULT_FROM


def send(to, template, reply=None, **values):
    """Send one email (`reply`: a Reply, see above). Returns its Message-ID when it
    is a reply, else None. Raises MailError."""
    if reply is not None:
        values.setdefault("subject", reply.subject)
    subject, text, page = render(template, **values)
    name = backend()
    extra, message_id = {}, None
    if reply is not None:
        domain = parseaddr(reply.from_address or sender())[1].rpartition("@")[2] or "localhost"
        message_id = make_msgid(domain=domain)
        extra = {"headers": reply.headers(message_id), "reply_to": reply.reply_to,
                 "from_address": reply.from_address}
    if name == "console":
        log.warning("Email (MAIL_BACKEND=console, not sent) to %s: %s%s\n%s", to, subject,
                    f" {extra['headers']}" if extra else "", text)
        return message_id
    try:
        {"smtp": _smtp, "resend": _resend, "postmark": _postmark}[name](to, subject, text, page,
                                                                       **extra)
    except MailError:
        raise
    except (OSError, smtplib.SMTPException, httpx.HTTPError) as e:
        log.warning("Couldn't send an email through %s: %s", name, type(e).__name__)
        raise MailError(f"The email couldn't be sent ({name}: {type(e).__name__}).") from None
    return message_id


def _smtp(to, subject, text, page, headers=None, reply_to=None, from_address=None):
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise MailError("SMTP_HOST isn't set.")
    security = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
    port = int(os.getenv("SMTP_PORT") or (465 if security == "ssl" else 587))
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = from_address or sender(), to, subject
    if reply_to:
        msg["Reply-To"] = reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(text)
    msg.add_alternative(page, subtype="html")
    context = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    with server:
        if security == "starttls":
            server.starttls(context=context)
        user = os.getenv("SMTP_USERNAME", "").strip()
        if user:
            server.login(user, os.getenv("SMTP_PASSWORD", ""))
        server.send_message(msg)


def _api_key(name):
    key = os.getenv(name, "").strip()
    if not key:
        raise MailError(f"{name} isn't set.")
    return key


def _check(response, name):
    if response.status_code >= 300:
        raise MailError(f"{name} refused the email (HTTP {response.status_code}).")


def _resend(to, subject, text, page, headers=None, reply_to=None, from_address=None):
    body = {"from": from_address or sender(), "to": [to], "subject": subject, "text": text,
            "html": page}
    if headers:
        body["headers"] = dict(headers)
    if reply_to:
        body["reply_to"] = reply_to
    r = httpx.post("https://api.resend.com/emails", timeout=TIMEOUT,
                   headers={"Authorization": f"Bearer {_api_key('RESEND_API_KEY')}"},
                   json=body)
    _check(r, "Resend")


def _postmark(to, subject, text, page, headers=None, reply_to=None, from_address=None):
    name, address = parseaddr(from_address or sender())
    body = {"From": formataddr((name, address)), "To": to, "Subject": subject,
            "TextBody": text, "HtmlBody": page, "MessageStream": "outbound"}
    if headers:
        body["Headers"] = [{"Name": k, "Value": v} for k, v in headers.items()]
    if reply_to:
        body["ReplyTo"] = reply_to
    r = httpx.post("https://api.postmarkapp.com/email", timeout=TIMEOUT,
                   headers={"X-Postmark-Server-Token": _api_key("POSTMARK_TOKEN"),
                            "Accept": "application/json"},
                   json=body)
    _check(r, "Postmark")
