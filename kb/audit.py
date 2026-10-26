"""
audited("member.update"): the dependency every endpoint that changes something
takes, so the change lands in the workspace's audit log (audit_store.py).

    @router.patch("/api/admin/members/{username}")
    def edit_member(..., user=Depends(require_admin), audit=audited("member.update")):
        ...                                  # make the change
        audit.record("member", username, {"role": "hr"})

The endpoint calls record() once the change is made, so a refused or failed
request leaves no event. Who did it, from where and with what browser come from
the request; `workspace` and `actor` are given for requests without a signed-in
user (accepting an invite, a provider's callback). Each event is logged too.

tests/test_audit.py checks every endpoint that changes something takes it.
"""

import logging

from fastapi import Depends, Request

import audit_store
from kb.deps import ws_of

log = logging.getLogger("uvicorn.error")


def client_ip(request):
    return request.client.host if request and request.client else None


class Audit:
    def __init__(self, action, request):
        self.action, self.request = action, request

    def record(self, target_type=None, target_id=None, details=None, *, user=None,
               workspace=None, actor=None, action=None, actor_type="user"):
        """Write the event. `user` is who did it (else `actor` in `workspace`)."""
        if user is not None:
            workspace, actor = workspace or ws_of(user), actor or user["username"]
        event = audit_store.record(
            workspace, action or self.action, actor=actor, actor_type=actor_type,
            target_type=target_type, target_id=target_id, details=details,
            ip=client_ip(self.request),
            user_agent=self.request.headers.get("user-agent") if self.request else None)
        log.info("audit %s: %s %s %s:%s %s", workspace, actor, event["action"], target_type,
                 target_id, event["details"])
        return event


def audited(action):
    """Depends(...) handing the endpoint an Audit for `action`."""
    def audited(request: Request):          # named so tests/routes.txt shows "audited"
        return Audit(action, request)
    return Depends(audited)


def record(request, workspace, action, actor, target_type=None, target_id=None, details=None,
           actor_type="user"):
    """The same outside an endpoint's own request (e.g. a provider's callback)."""
    return Audit(action, request).record(target_type, target_id, details, workspace=workspace,
                                         actor=actor, actor_type=actor_type)
