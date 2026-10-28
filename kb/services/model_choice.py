"""
Which model answers a chat (the composer's model picker): one of the answer
models the workspace's admin enabled (model_providers_store.enabled), checked on
every question.

    available(ws)      what the picker offers: the enabled models (the default
                       first) with their labels and tiers, and the default. With
                       chat on the server's provider: its one model, no choice
    resolve(...)       the model a question is answered with: the one the
                       request names, else the chat's own, else the asker's last
                       pick, else the default (each only while it's enabled). A
                       model the REQUEST names that isn't enabled is refused
                       (Refused), never swapped for another silently; a chat whose
                       model was turned off since starts on the default
    remember(...)      the chat keeps its pick (conversations.model_provider/_id)

Only the answer model follows the pick. The fast model (the guards' classifiers,
HyDE, titles) stays the workspace's, so the guards never depend on it. The
credentials are always the workspace's own (model_providers_store._spec_of): a
request names a provider and a model id, never a key, and only among ITS
workspace's enabled models.
"""

import conversations_store
import model_providers_store
import models


class Refused(ValueError):
    """The model asked for isn't one this workspace offers: the message says so."""


def _server_default(ws):
    on = models.for_workspace(ws)
    return on.provider, on.answer_name


def enabled(ws):
    """[(provider, model), ...], the default first. Never empty: on the server's
    provider, its one model."""
    found = model_providers_store.enabled(model_providers_store.get(ws) or {})
    return found or [_server_default(ws)]


def available(ws, owner=None):
    """{models: [...], default: {provider, model}, last: {provider, model} | None}:
    what the picker shows. last: the asker's most recent pick, while enabled."""
    listed = enabled(ws)
    last = _last(ws, owner, listed) if owner else None
    return {"models": [model_providers_store.describe_model(p, m) for p, m in listed],
            "default": _ref(listed[0]), "last": _ref(last) if last else None}


def _ref(pair):
    return {"provider": pair[0], "model": pair[1]}


def _last(ws, owner, listed):
    found = conversations_store.last_model(ws, owner)
    return found if found in listed else None


def resolve(ws, requested=None, conversation=None, owner=None):
    """(provider, model) to answer with. requested: {provider, model} from the
    request (None: not named). conversation: the chat's row, whose own pick
    comes next (the default once it's turned off). Raises Refused for a model
    the request names that isn't enabled."""
    listed = enabled(ws)
    if requested:
        pair = (requested.get("provider"), requested.get("model"))
        if pair not in listed:
            raise Refused("That model isn't available in this workspace. "
                          "Choose another in the model menu.")
        return pair
    if conversation and conversation.get("model_id"):
        pair = (conversation.get("model_provider"), conversation["model_id"])
        # Turned off since: the chat starts on the default, as the Remove dialog
        # says and the chat screen shows. Never an error.
        return pair if pair in listed else listed[0]
    if owner:
        last = _last(ws, owner, listed)
        if last:
            return last
    return listed[0]


def remember(ws, owner, conversation_id, pair):
    """The chat keeps its pick, so it opens on it next time."""
    conversations_store.set_model(ws, owner, conversation_id, *pair)
