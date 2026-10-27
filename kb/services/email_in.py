"""
Email-in (platform.md PL-25): members email a question to ask@<workspace>.<domain>
and get the same checked, cited answer as in the web chat, as a reply in the
thread, through answering.ask() (surface "email").

HOW IT ARRIVES. An inbound-mail provider receives the email (the domain's MX
record points at it) and posts it to a webhook here. Postmark first
(POST /api/email-in/postmark, kb/routers/email_in.py); providers are pluggable
(PROVIDERS): each turns its webhook's body into an InboundEmail with the
provider's own authentication verdicts. The webhook only checks, records and
queues (take()); a job (kind "email_in", run()) answers and replies.

    ask@<slug>.<EMAIL_IN_DOMAIN>   the address (Postmark takes wildcard inbound
                                   domains: *.<EMAIL_IN_DOMAIN>)
    ask+<slug>@<EMAIL_IN_DOMAIN>   the same, for a provider that can't take a
                                   wildcard (Postmark's MailboxHash)

Off unless the workspace's admin turns it on (email_in_store.py), and only when
the host has set EMAIL_IN_DOMAIN and the webhook's credentials.

WHO GETS AN ANSWER. Only the sender (From) who is an enabled member of THAT
workspace, answered with their role as it is now (asker()), AND only when the
provider says the email really is from them (verdicts, below). Everything else
is dropped WITHOUT a reply, recorded with why in email_in_messages: replying to
a forged From would send our answer to whoever was spoofed (backscatter), and
saying "you aren't a member" would tell anyone which workspaces exist.

AUTHENTICATION. A From header is trivially forged, so it counts only with the
provider's verdicts:
  Postmark  "Received-SPF: pass" AND DKIM aligned with the From domain (the
            SpamAssassin test DKIM_VALID_AU in X-Spam-Tests: a valid signature
            from the author's domain). Postmark documents no DMARC verdict
            (https://postmarkapp.com/developer/user-guide/inbound/parse-an-email),
            so SPF pass plus aligned DKIM stands in for it: the From domain signed
            the message. Every copy of those headers must say so, so a header the
            sender wrote themselves can't vouch for them; Authentication-Results
            is ignored, as Postmark doesn't add one (any there came with the email).
  SES       (not built yet) the SNS notification's receipt.spfVerdict,
            receipt.dkimVerdict and receipt.dmarcVerdict must each be PASS
            (https://docs.aws.amazon.com/ses/latest/dg/receiving-email-notifications-contents.html),
            and the SNS message's signature must be checked first (SignatureVersion 2,
            a SigningCertURL on sns.<region>.amazonaws.com, the expected TopicArn:
            https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message.html).

LOOPS AND AUTOMATION (automated()). Dropped: Auto-Submitted other than "no"
(RFC 3834 section 2), Precedence bulk / list / junk, mailing lists (List-Id, any
List-*), X-Auto-Response-Suppress, a null Return-Path (<>), mail from our own
sending address or any ask@ address, mailer-daemon and no-reply senders, and a
Message-ID already taken in (a provider retrying). Our replies say
"Auto-Submitted: auto-replied", so other responders leave them alone. At most
RATE_LIMIT_EMAIL_USER emails a minute per sender (kb/rate_limit.inbound_limit).

THREADS. The reply is "Re: <subject>" with In-Reply-To and References (RFC 5322
3.6.4) and Reply-To the ask@ address, so replying asks a follow-up: an email
whose In-Reply-To / References names one of our replies to the same person
continues that conversation (with its history).
"""

import hmac
import logging
import os
import re
import time
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr

from fastapi import HTTPException

import audit_store
import conversations_store
import email_in_store
import mailer
import members_store
import workspaces
from kb import jobs
from kb.rate_limit import inbound_limit, user_limit
from kb.services import answering, conversations

log = logging.getLogger("uvicorn.error")

JOB = "email_in"
LOCAL = "ask"                       # the address's local part
QUESTION_CHARS = 4000
SUBJECT_CHARS = 200
FAILED = ("Sorry, I couldn't answer that just now ({kind}). Please try again in a few "
          "minutes, or ask in the web app.")
AUTOMATED_PRECEDENCE = {"bulk", "list", "junk"}
ROBOT_LOCALS = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounces?)"
                          r"([+._-].*)?$")
REPLY_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|sv)\s*:\s*)+", re.IGNORECASE)


# ---- The host's settings -------------------------------------------------------------------
def domain():
    """EMAIL_IN_DOMAIN: the domain under which every workspace has its address."""
    return os.getenv("EMAIL_IN_DOMAIN", "").strip().lower().strip(".")


def webhook_credentials():
    """(user, password) the provider's webhook must send (HTTP Basic), or None."""
    user = os.getenv("EMAIL_IN_WEBHOOK_USER", "").strip()
    password = os.getenv("EMAIL_IN_WEBHOOK_PASSWORD", "").strip()
    return (user, password) if user and password else None


def configured():
    return bool(domain() and webhook_credentials())


def credentials_ok(user, password):
    """Constant-time check of the webhook's Basic credentials."""
    expected = webhook_credentials()
    if not expected:
        return False
    same_user = hmac.compare_digest((user or "").encode(), expected[0].encode())
    same_password = hmac.compare_digest((password or "").encode(), expected[1].encode())
    return same_user and same_password


def address(ws):
    return f"{LOCAL}@{ws}.{domain()}" if domain() else None


def reply_from():
    """The From of our replies: EMAIL_IN_FROM, else the mailer's MAIL_FROM."""
    return os.getenv("EMAIL_IN_FROM", "").strip() or None


def workspace_for(recipients):
    """The workspace an email was sent to, from its recipients, or None."""
    base = domain()
    if not base:
        return None
    for rcpt in recipients:
        local, _, host = (rcpt or "").strip().lower().rpartition("@")
        if local == LOCAL and host.endswith("." + base):
            slug = host[:-len(base) - 1]
        elif local.startswith(LOCAL + "+") and host == base:
            slug = local[len(LOCAL) + 1:]
        else:
            continue
        if workspaces.valid(slug):
            return slug
    return None


# ---- What came in ----------------------------------------------------------------------------
class Headers:
    """An email's headers: case-insensitive, each name possibly more than once."""

    def __init__(self, pairs=()):
        self._pairs = [(str(k), str(v)) for k, v in pairs]

    def all(self, name):
        name = name.lower()
        return [v for k, v in self._pairs if k.lower() == name]

    def get(self, name, default=None):
        found = self.all(name)
        return found[0] if found else default

    def names(self):
        return {k.lower() for k, _ in self._pairs}


@dataclass
class InboundEmail:
    provider: str
    message_id: str                 # the Message-ID header, or the provider's own id
    sender: str                     # the From address, lower case
    sender_name: str
    recipients: list                # every address it was sent to
    subject: str
    text: str                       # the new text, quoted replies removed where known
    headers: Headers
    in_reply_to: str | None = None
    references: list = field(default_factory=list)
    spf_pass: bool = False          # the provider's verdicts
    dkim_aligned: bool = False


def _message_ids(value):
    return re.findall(r"<[^<>\s]+>", value or "")


def _strip_quoted(text):
    """A reply's own words: from the first quoted line, "On ... wrote:" or
    "-----Original Message-----" on, it's the thread below; then the signature."""
    out = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith(">") or re.match(r"^-{2,}\s*original message\s*-{2,}$",
                                                stripped, re.IGNORECASE):
            break
        if re.match(r"^on .{4,200} wrote:$", stripped, re.IGNORECASE):
            break
        if line == "-- ":
            break
        out.append(line)
    return "\n".join(out).strip()


class Postmark:
    """Postmark's inbound webhook (https://postmarkapp.com/developer/webhooks/inbound-webhook):
    a JSON body with From/FromFull, To/ToFull, Cc/CcFull, Bcc/BccFull,
    OriginalRecipient, Subject, MessageID, MailboxHash, TextBody, HtmlBody,
    StrippedTextReply and Headers [{Name, Value}]."""
    name = "postmark"

    def parse(self, body):
        if not isinstance(body, dict):
            raise ValueError("not an object")
        headers = Headers((h.get("Name", ""), h.get("Value", ""))
                          for h in body.get("Headers") or [] if isinstance(h, dict))
        full = body.get("FromFull") or {}
        sender_name, sender = parseaddr(body.get("From") or "")
        sender = (full.get("Email") or sender or "").strip().lower()
        recipients = [body.get("OriginalRecipient") or ""]
        for key in ("ToFull", "CcFull", "BccFull"):
            recipients += [r.get("Email") or "" for r in body.get(key) or []
                           if isinstance(r, dict)]
        for key in ("To", "Cc", "Bcc"):
            recipients += [a for _, a in getaddresses([body.get(key) or ""])]
        mailbox_hash = (body.get("MailboxHash") or "").strip().lower()
        if mailbox_hash and domain():
            recipients.append(f"{LOCAL}+{mailbox_hash}@{domain()}")
        message_id = (_message_ids(headers.get("Message-ID", "")) or [None])[0] \
            or f"<postmark-{body.get('MessageID') or ''}>"
        text = (body.get("StrippedTextReply") or "").strip() or \
            _strip_quoted(body.get("TextBody") or "")
        refs = _message_ids(" ".join(headers.all("References")))
        sender_domain = sender.rpartition("@")[2]
        return InboundEmail(
            provider=self.name, message_id=message_id[:500], sender=sender,
            sender_name=(full.get("Name") or sender_name or "").strip(),
            recipients=[r for r in recipients if r], subject=(body.get("Subject") or "").strip(),
            text=text, headers=headers,
            in_reply_to=(_message_ids(headers.get("In-Reply-To", "")) or [None])[0],
            references=refs,
            spf_pass=self.spf_pass(headers), dkim_aligned=self.dkim_aligned(headers, sender_domain))

    @staticmethod
    def spf_pass(headers):
        """Every Received-SPF says pass (Postmark adds one; one the sender wrote can't
        vouch for them)."""
        found = headers.all("Received-SPF")
        return bool(found) and all(v.strip().lower().startswith("pass") for v in found)

    @staticmethod
    def dkim_aligned(headers, sender_domain):
        """Every X-Spam-Tests lists DKIM_VALID_AU: a valid DKIM signature from the
        From domain (SpamAssassin's test, which Postmark adds)."""
        found = headers.all("X-Spam-Tests")
        return bool(sender_domain) and bool(found) and all(
            "DKIM_VALID_AU" in re.split(r"[\s,]+", v.strip()) for v in found)


PROVIDERS = {"postmark": Postmark()}


# ---- Checks ----------------------------------------------------------------------------------
def access_role(member):
    """The access role a member asks as (rag.ROLE_ACCESS), as a session's is."""
    return "hr" if member["role"] in members_store.STAFF_ROLES else "public"


def asker(ws, email):
    """(member, access role) of the person an email is from in workspace `ws`, as
    they are now; (None, why) when they mayn't ask here."""
    member = members_store.check_group_admin(members_store.get(email)) if email else None
    if not member or member["workspace"] != ws:
        return None, "not_member"
    if member["disabled"]:
        return None, "disabled"
    return member, access_role(member)


def _own_addresses():
    found = {parseaddr(mailer.sender())[1].lower()}
    if reply_from():
        found.add(parseaddr(reply_from())[1].lower())
    return found


def automated(email):
    """Why an email is automatic, a list's, or ours (so it mustn't be answered), or None."""
    h = email.headers
    auto = (h.get("Auto-Submitted") or "").strip().lower()
    if auto and not auto.startswith("no"):
        return "auto_submitted"
    if (h.get("Precedence") or "").strip().lower() in AUTOMATED_PRECEDENCE:
        return "precedence"
    if any(name.startswith("list-") for name in h.names()):
        return "mailing_list"
    suppress = {v.strip().lower() for v in
                re.split(r",", ",".join(h.all("X-Auto-Response-Suppress")))}
    if suppress & {"all", "autoreply", "oof"}:
        return "auto_response_suppress"
    return_path = h.get("Return-Path")
    if return_path is not None and return_path.strip() in ("<>", ""):
        return "null_return_path"
    local, _, host = email.sender.rpartition("@")
    if not local or not host:
        return "no_sender"
    if email.sender in _own_addresses():
        return "own_address"
    base = domain()
    if base and (host == base or host.endswith("." + base)):
        return "own_address"
    if ROBOT_LOCALS.match(local):
        return "robot_sender"
    return None


def question_of(email):
    """The question: the subject (unless it only says Re:/Fwd:) and the new text."""
    subject = email.subject[:SUBJECT_CHARS]
    body = email.text.strip()
    parts = [] if REPLY_PREFIX.match(subject) or not subject else [subject]
    if body and body != subject:
        parts.append(body)
    if not parts and subject:
        parts.append(REPLY_PREFIX.sub("", subject))
    return "\n\n".join(parts).strip()[:QUESTION_CHARS]


# ---- Taking an email in (the webhook) ----------------------------------------------------------
def take(provider, body, audit=None):
    """Check, record and queue one email. Returns what was done (for logs and
    tests): "queued", "duplicate", "not_for_us", or "dropped:<why>" /
    "refused:<why>"."""
    email = PROVIDERS[provider].parse(body)
    ws = workspace_for(email.recipients)
    if not ws:
        return "not_for_us"
    if not email_in_store.first_time(email.message_id, ws, email.sender):
        return "duplicate"

    def drop(reason, status="dropped"):
        email_in_store.set_status(email.message_id, status, reason)
        log.info("Email-in: %s an email to workspace %s (%s)", status, ws, reason)
        return f"{status}:{reason}"

    if not email_in_store.enabled(ws):
        return drop("off")
    why = automated(email)
    if why:
        return drop(why)
    if not email.spf_pass:
        return drop("spf")
    if not email.dkim_aligned:
        return drop("dkim")
    member, why = asker(ws, email.sender)
    if not member:
        # Authenticated, so not spoofed: recorded, but still not answered (see above).
        if audit is not None:
            audit.record("email", email.message_id[:200],
                         {"reason": why, "sender_domain": email.sender.rpartition("@")[2]},
                         workspace=ws, actor="email-in", actor_type="system",
                         action="email_in.refused")
        return drop(why, "refused")
    if not inbound_limit("email", ws, email.sender):
        return drop("rate_limited")
    question = question_of(email)
    if not question:
        return drop("empty")
    jobs.enqueue(JOB, workspace=ws, lane=f"{JOB}:{ws}:{email.message_id}", max_attempts=1,
                 payload={"message_id": email.message_id, "sender": email.sender,
                          "subject": email.subject[:SUBJECT_CHARS], "question": question,
                          "in_reply_to": email.in_reply_to, "references": email.references[-20:]})
    return "queued"


# ---- Answering (the job) -----------------------------------------------------------------------
_MARKER = re.compile(r"[ \t]*\[S(\d{1,3})\]")


def _base_url():
    return (os.getenv("PUBLIC_URL") or "").rstrip("/") or None


def source_link(source, base_url):
    """Where a source points: the source itself, or the app's passage view (which
    checks the reader's sign-in and role)."""
    if source.get("url"):
        return source["url"]
    if source.get("chunk_id") and base_url:
        return f"{base_url}/passages/{source['chunk_id']}"
    return None


def render(done, base_url=None):
    """(answer text, sources text): each [S#] as [n] when that source is cited,
    else dropped, and the numbered sources with their links."""
    numbered = {s["n"]: s for s in done.sources if isinstance(s.get("n"), int)}

    def cite(m):
        return f" [{m.group(1)}]" if int(m.group(1)) in numbered else ""
    text = _MARKER.sub(cite, done.answer or "").strip() or "(no answer)"
    lines = []
    for n in sorted(numbered):
        s = numbered[n]
        title = " ".join(str(s.get("title") or "Untitled").split())[:150]
        link = source_link(s, base_url)
        lines.append(f"[{n}] {title}" + (f" - {link}" if link else ""))
    return text, ("Sources:\n" + "\n".join(lines) + "\n") if lines else ""


def _reply(p, ws):
    subject = p.get("subject") or "Your question"
    if not REPLY_PREFIX.match(subject):
        subject = "Re: " + subject
    return mailer.Reply(subject=subject[:SUBJECT_CHARS + 4], in_reply_to=p["message_id"],
                        references=list(p.get("references") or []), reply_to=address(ws),
                        from_address=reply_from())


def _conversation(ws, member, role, p):
    """The conversation a reply to one of ours continues, or a new one."""
    cid = email_in_store.conversation_replied_to(
        ws, p["sender"], [p.get("in_reply_to")] + list(p.get("references") or []))
    conv = conversations_store.get(ws, member["username"], cid) if cid else None
    return conv or conversations_store.create(ws, member["username"], role)


def teardown(ws):
    """Email-in off and forgotten for a workspace (purging it, kb/services/lifecycle.py):
    its setting and its record of emails go, so a queued answer is skipped (run()
    checks the setting first) and nothing more is taken in for it. Nothing is held
    at the provider: the inbound domain is the server's, not the workspace's.
    Returns how many emails were forgotten."""
    return email_in_store.remove(ws)


@jobs.handler(JOB, max_attempts=1)
def run(job):
    p, ws = job.payload, job.workspace
    mid = p["message_id"]
    if not email_in_store.enabled(ws):
        email_in_store.set_status(mid, "dropped", "off")
        return {"skipped": "off"}
    member, why = asker(ws, p["sender"])          # again: they may have changed since
    if not member:
        email_in_store.set_status(mid, "refused", why)
        return {"refused": why}
    user = {"username": member["username"], "workspace": ws}
    try:
        user_limit(user, "chat")
    except HTTPException:
        email_in_store.set_status(mid, "dropped", "rate_limited")
        return {"limited": True}

    role = access_role(member)
    conv = _conversation(ws, member, role, p)
    turn = conversations.start_turn(ws, member["username"], role, conv["id"], p["question"])
    cid = turn["conversation"]["id"]
    started = time.monotonic()
    reply = _reply(p, ws)
    try:
        done = answering.ask(ws, user, role, p["question"], surface="email", turn=turn)
    except answering.Failed as e:
        conversations_store.finish_message(turn["message_id"], f"Something went wrong: {e.kind}",
                                           "error", max_access="restricted")
        reply_id = _send(p, reply, FAILED.format(kind=e.kind), "", member, ws)
        email_in_store.set_status(mid, "failed", "answering", cid, reply_id)
        return {"failed": e.kind}
    conversations_store.finish_message(
        turn["message_id"], done.answer, done.state, sources=done.sources,
        steps=[{"tool": t["tool"]} for t in done.trace], max_access=done.max_access,
        standalone_question=done.standalone,
        latency_ms=int((time.monotonic() - started) * 1000), verified=done.verified)
    text, sources = render(done, _base_url())
    reply_id = _send(p, reply, text, sources, member, ws)
    if not reply_id:
        email_in_store.set_status(mid, "failed", "mail", cid)
        return {"failed": "mail"}
    email_in_store.set_status(mid, "answered", None, cid, reply_id)
    audit_store.record(ws, "email_in.answer", actor=member["username"], actor_type="user",
                       target_type="question", target_id=turn["message_id"],
                       details={"surface": "email", "audience": role, "state": done.state,
                                "chars": len(p["question"]), "conversation": cid})
    return {"answered": done.state, "conversation": cid}


def _send(p, reply, text, sources, member, ws):
    """Send the reply; its Message-ID, or None if it couldn't be sent."""
    footer = (f"Answered from what {member.get('name') or member['username']} can see in "
              f"{members_store.workspace_name(ws)}. Reply to this email to ask a follow-up.")
    try:
        return mailer.send(p["sender"], "email_answer", reply=reply, answer=text,
                           sources=sources, footer=footer)
    except mailer.MailError as e:
        log.warning("Email-in: couldn't send the answer in workspace %s (%s)", ws, e)
        return None
