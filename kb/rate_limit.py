"""
Rate limits. The counts live in kb/state.py (`hits`), in this process only.

On the sign-in endpoints anyone can call: per client address and minute, plus a
ceiling for all addresses together (an address can be faked in X-Forwarded-For
if the proxy settings trust anyone). RATE_LIMITS=off turns them off (the tests do).
"""

import os
import time

from fastapi import HTTPException, Request

from kb import state
from kb.deps import ws_of
from kb.settings import env_int

LIMITS = {"login": 30, "discover": 30, "sso_start": 60, "saml_acs": 60, "dns_check": 20,
          "signup": 10, "invite": 30}
ALL_ADDRESSES = 20                       # x the per-address limit, per minute
# Limits on what a signed-in person can make the server do (it costs OpenAI calls
# or indexing time), per minute: (per person, per workspace). Each can be set in
# the environment, e.g. RATE_LIMIT_CHAT_USER=40, RATE_LIMIT_CHAT_WORKSPACE=600.
USER_LIMITS = {"chat": (20, 300), "upload": (30, 120), "reindex": (5, 20), "sync": (5, 20),
               "invite": (10, 30)}
# Calls with an API key (the public API and the MCP server, kb/api_auth.py), per
# minute: (per key, all of a workspace's keys together). RATE_LIMIT_API_KEY,
# RATE_LIMIT_API_WORKSPACE. A question also counts as one of its person's "chat".
KEY_LIMITS = (60, 600)


def rate_limits_on():
    return os.getenv("RATE_LIMITS", "on").strip().lower() not in ("off", "false", "0", "no")


def _take(checks):
    """Count one hit against each (key, limit, message) for the last minute, or
    none if any is used up: then a 429 with that message and Retry-After."""
    now = time.time()
    with state.hits_lock:
        hits = state.hits
        if len(hits) > 10_000:                        # forget the quiet ones
            for k in [k for k, v in hits.items() if not v or v[-1] < now - 60]:
                del hits[k]
            if len(hits) > 10_000:                    # a flood of fake addresses
                hits.clear()
        recents = []
        for key, limit, message in checks:
            recent = [t for t in hits.get(key, []) if t > now - 60]
            if len(recent) >= limit:
                wait = max(1, int(recent[-limit] + 60 - now + 0.999))
                raise HTTPException(429, message.format(wait=wait, s="" if wait == 1 else "s"),
                                    headers={"Retry-After": str(wait)})
            recents.append((key, recent))
        for key, recent in recents:
            hits[key] = recent + [now]


def rate_limit(request: Request, bucket):
    if not rate_limits_on():
        return
    address = request.client.host if request.client else "?"
    message = "Too many attempts. Wait {wait} second{s} and try again."
    _take([((bucket, address), LIMITS[bucket], message),
           ((bucket, "*"), LIMITS[bucket] * ALL_ADDRESSES, message)])


def user_limit(user, bucket):
    """Per person and per workspace limits (USER_LIMITS) on something costly."""
    if not rate_limits_on():
        return
    per_user, per_workspace = (env_int(f"RATE_LIMIT_{bucket.upper()}_{who}", default)
                               for who, default in zip(("USER", "WORKSPACE"),
                                                       USER_LIMITS[bucket]))
    verb, noun = {"chat": ("asked", "question"), "upload": ("uploaded files", "time"),
                  "reindex": ("started", "re-index"), "sync": ("started", "sync"),
                  "invite": ("sent", "invite")}[bucket]

    def did(n):
        return f"{verb} {n} {noun}{'' if n == 1 else 'es' if noun.endswith('x') else 's'}"
    _take([((bucket, "user", user["username"]), per_user,
            f"You've {did(per_user)} in the last minute, the most one person can. "
            "Try again in {wait} second{s}."),
           ((bucket, "workspace", ws_of(user)), per_workspace,
            f"Your workspace has {did(per_workspace)} in the last minute, the most it can. "
            "Try again in {wait} second{s}.")])


def key_limit(key_id, workspace):
    """Per API key and per workspace limits (KEY_LIMITS) on calls made with keys."""
    if not rate_limits_on():
        return
    per_key, per_workspace = (env_int(f"RATE_LIMIT_API_{who}", default)
                              for who, default in zip(("KEY", "WORKSPACE"), KEY_LIMITS))
    _take([(("api", "key", key_id), per_key,
            f"This API key has made {per_key} calls in the last minute, the most one key can. "
            "Try again in {wait} second{s}."),
           (("api", "workspace", workspace), per_workspace,
            f"Your workspace's API keys have made {per_workspace} calls in the last minute, "
            "the most they can. Try again in {wait} second{s}.")])
