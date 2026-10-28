"""
Joining a workspace by signing in (join_store.py has the rules and the tables).

admit() is the ONE gate every sign-in that can add a member goes through (sso.py:
the server-wide Google, Microsoft and OpenID Connect providers, Microsoft 365, and
a workspace's own OIDC and SAML providers). Sign-up never comes here: it makes a
new workspace, with its founder as admin.

    already a member        in (signed in before, SCIM-provisioned, invited and
                            accepted, approved, or given a role by hand)
    a pending invite        in: the sign-in accepts it, with the invited role
    policy auto             in: the sign-in adds them
    policy invite_only      NotAllowed("invite_only")
    policy request          a join request (or the one they have, updated), the
                            admins emailed about a new one, NotAllowed("join_requested"):
                            no member, no session; the browser goes to the "request
                            sent" page (services/sign_in.py). Within JOIN_DENY_DAYS
                            of a denial: NotAllowed("join_denied")

Admins' emails are limited to JOIN_NOTICES_PER_HOUR per workspace (default 5); the
rest wait in Access > Members. Everything here is in the workspace's audit log.

Claiming a workspace nobody can administer (join_store.claim) with Google
Workspace also finishes here: finish_claim().
"""

import logging
import os
import time

from fastapi.responses import RedirectResponse

import audit_store
import join_store
import kb.audit
import mailer
import members_store
import sso
import sso_errors
from kb.services.sign_in import ProviderFailed, code_for, login_error, provider_answer

log = logging.getLogger("uvicorn.error")

CLAIM_KEY = "claim"                # the claim code, in the sign-in cookie, while at Google
CLAIM_SECONDS = 15 * 60


def notices_per_hour():
    try:
        return max(0, int(os.getenv("JOIN_NOTICES_PER_HOUR", "5")))
    except ValueError:
        return 5


def _site(path):
    return (os.getenv("PUBLIC_URL") or "").rstrip("/") + path


def admit(workspace, email, name, method):
    """May `email` (just signed in with `method`, a provider's name) be added to
    `workspace` now? Returns how they get in ("member", "invite" or "auto");
    raises sso.NotAllowed. See the top of this file."""
    member = members_store.get(email)
    if member:
        return "member"            # (another workspace's: record_sso_login refuses them)
    invite = join_store.take_invite(workspace, email, name)
    if invite:
        kb.audit.record(None, workspace, "invite.accept", email, "invite", invite["id"],
                        {"email": email, "role": invite["role"], "via": method})
        from kb.services import webhooks
        webhooks.member_added(workspace, email, invite["role"], "invite")
        return "invite"
    policy = join_store.policy_for(workspace, email.rpartition("@")[2])
    if policy == "auto":
        return "auto"
    if policy == "invite_only":
        raise sso.NotAllowed("invite_only")
    try:
        request, new = join_store.request(workspace, email, name, method)
    except join_store.Denied:
        raise sso.NotAllowed("join_denied") from None
    if new:
        kb.audit.record(None, workspace, "join_request.create", email, "join_request",
                        request["id"], {"email": email, "provider": method})
        _tell_admins(workspace, request)
    raise sso.NotAllowed("join_requested")


def _tell_admins(workspace, request):
    """Email the workspace's admins about a new request, unless they've had
    notices_per_hour() already this hour. Best effort."""
    if join_store.notices_since(workspace, time.time() - 3600) >= notices_per_hour():
        return
    sent = False
    for admin in members_store.enabled_admins(workspace):
        if "@" not in admin:
            continue
        try:
            mailer.send(admin, "join_request", requester=request["name"] or request["email"],
                        email=request["email"], provider=request["provider"] or "single sign-on",
                        workspace_name=members_store.workspace_name(workspace),
                        link=_site("/admin/members"), days=join_store.request_days())
            sent = True
        except mailer.MailError as e:
            log.warning("Couldn't email %s about a join request: %s", admin, e)
    if sent:
        join_store.mark_notified(request["id"])


def expire(workspace):
    """Mark the workspace's overdue requests expired, each in its audit log."""
    for r in join_store.expire_due(workspace):
        kb.audit.record(None, workspace, "join_request.expire", None, "join_request", r["id"],
                        {"email": r["email"]}, actor_type="system")


def approved(workspace, request):
    """After an admin approved a request: the webhook, and an email to the person."""
    from kb.services import webhooks
    webhooks.member_added(workspace, request["email"], request["role"], "join_request")
    try:
        mailer.send(request["email"], "join_approved",
                    workspace_name=members_store.workspace_name(workspace),
                    link=_site("/login"))
    except mailer.MailError as e:
        log.warning("Couldn't email %s that their request was approved: %s", request["email"], e)


# ---- Claiming a workspace with Google Workspace ---------------------------------------
async def finish_claim(c, request, code):
    """Google sent the claimant back: claim the workspace and sign them in as its
    admin, or back to the claim page with why not."""
    try:
        claims, _ = await provider_answer("google", c, request)
        email, name, org = sso.identity("google", claims)
    except ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("Claim: Google sign-in failed (%s), reference %s", e.code, ref)
        return login_error(e.error, ref, tab="claim")
    except sso.NotAllowed as e:    # unverified, or a personal account (no hd)
        return login_error(e.reason, tab="claim")
    try:
        found = join_store.claim(code, email, name, org)
    except join_store.ClaimRefused as e:
        log.warning("Claim: %s refused (%s)", email, e.reason)
        return login_error(e.reason, tab="claim")
    except Exception:
        ref = sso_errors.new_ref()
        log.exception("Claim: couldn't claim the workspace, reference %s", ref)
        return login_error("sso_failed", ref, tab="claim")
    workspace, domain = found["workspace"], found["domain"]
    details = {"domain": domain, "claim_code": found["id"], "issued_by": found["created_by"]}
    kb.audit.record(request, workspace, "workspace.claim", email, "workspace", workspace, details)
    audit_store.record(audit_store.PLATFORM, "workspace.claim", actor=email, actor_type="user",
                       target_type="workspace", target_id=workspace, details=details,
                       ip=kb.audit.client_ip(request))
    log.warning("Claim: %s claimed the workspace %s with %s", email, workspace, domain)
    members_store.log_event(workspace, "google", email, "signed_in")
    user = {"username": email, "name": name, "provider": "google", "org": org,
            "workspace": workspace, "role": "admin"}
    return RedirectResponse("/#sso?welcome=1&code=" + code_for(request, user))
