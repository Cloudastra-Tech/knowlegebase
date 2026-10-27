"""
Limits on what a workspace may use, as plug-ins: a plan's limits
(kb/services/billing.py, only with BILLING=on), and any other kind of cap
(spend caps, abuse limits) registers the same way, so none of the places that
check needs to know which are in force.

    @quotas.register
    def my_cap(ws, what, user=None, **amounts):
        ...                      # raise HTTPException (402, 429) to refuse, with why

    quotas.check(ws, "chat", user=user)          # before something costly

`what`, and the amounts that come with it:
    chat        one question, from any surface (rate_limit.user_limit calls this
                for every "chat", "upload", "sync", ... it limits)
    docs        uploading documents: files=<how many new>, bytes=<their size>
    connector   connecting a source: provider=<its id>

A check that doesn't know `what` lets it through. Refusals say what the limit is,
in words the person can act on ("Your plan includes 2 connectors").
"""

import threading

CHECKS = []
_loaded = False
_lock = threading.Lock()


def register(fn):
    if fn not in CHECKS:
        CHECKS.append(fn)
    return fn


def _load():
    """The built-in checks register when their module is imported."""
    global _loaded
    if not _loaded:
        with _lock:
            if not _loaded:
                from kb.services import billing  # noqa: F401
                _loaded = True


def check(ws, what, user=None, **amounts):
    _load()
    for fn in list(CHECKS):
        fn(ws, what, user=user, **amounts)
