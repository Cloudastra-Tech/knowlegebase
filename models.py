"""
models.py  —  which model does which job, and from which PROVIDER (pipeline.md P1-5,
model routing; PROGRESS.md "AWS Bedrock as a model provider").

    ANSWER_MODEL   writes the answer the user reads (agent.py's agent node)
    FAST_MODEL     the small jobs: the guards' classifiers (safety.py), rewriting a
                   follow-up, HyDE, multi-query variants (rag.py), the optional
                   chunk context at indexing (ingest.py), titles, follow-ups,
                   summaries, insights labels, verified-answer rewording
    embeddings     the search index's vectors, and the questions searched with it

Both chat models default to gpt-4o-mini, the model every step used before, so
nothing changes until they are set in .env. A cheaper FAST_MODEL (gpt-4.1-nano,
gpt-5-nano) is the likely saving; check the leak and injection suites before
switching (python -m eval.harness --gate; python evaluate.py --redteam), since
the guards' classifiers run on it.

PROVIDERS. Every model is made here, through a workspace's Models (for_workspace).
What each provider offers (chat, embeddings, images, prompt caching, tool calls)
and how its models are made is its entry in REGISTRY (Provider, register()):

    openai     OpenAI (the default): ANSWER_MODEL, FAST_MODEL, text-embedding-3-small.
               Made exactly as before: each caller passes its own ChatOpenAI /
               OpenAIEmbeddings name, so a test can stand in for one call site.
    bedrock    AWS Bedrock in the company's own AWS account and region: chat
               through the Converse API (langchain-aws ChatBedrockConverse:
               streaming and tool calls), embeddings through InvokeModel (Titan
               Text Embeddings V2 by default). Documents and questions never
               reach OpenAI.
    anthropic  Claude through Anthropic's API (langchain-anthropic ChatAnthropic:
               streaming, tool calls, images): ANTHROPIC_CHAT_MODEL (Sonnet) and
               ANTHROPIC_FAST_MODEL (Haiku). Chat only: no embeddings.
    local      embeddings only, on the server's CPU (fastembed, ONNX Runtime):
               LOCAL_EMBED_MODEL, downloaded on first use into LOCAL_EMBED_CACHE.
               What a chat provider without embeddings gets when none is set.

CHAT AND EMBEDDINGS are chosen apart (Spec.provider, Spec.embed_provider), since
not every provider has embeddings: "Claude for chat, OpenAI or Bedrock for the
index" is a valid workspace. Adding a provider is a register(Provider(...)) with
its make_chat (and make_embeddings, if it has them), at the end of this file.

The server's providers come from .env: MODEL_PROVIDER (openai unless set) sets
both, as it always has; CHAT_PROVIDER and EMBED_PROVIDER override one each. An
embeddings provider must have embeddings, and one that isn't known is refused,
never read as openai. Bedrock uses AWS_REGION and BEDROCK_* models with the
server's own AWS credentials (boto3's default chain: an instance role,
AWS_PROFILE, AWS_ACCESS_KEY_ID...). A workspace's admin can bring their own
instead (model_providers_store.py, the Assistant screen): a provider for chat and
one for embeddings, an API key per provider sealed under the workspace's key
(secret_box), or their own AWS: a role in THEIR account the server assumes (STS
AssumeRole with the ExternalId this app generated for them; credentials kept
until they expire) or access keys, sealed too.

A CHAT'S PICK. A workspace's admin can enable several answer models, across the
chat providers it has credentials for (model_providers_store.enabled; the curated
list per provider is CATALOGUE), and members pick one per chat
(kb/services/model_choice.py). Models.with_answer() swaps the ANSWER model only
(Spec.answer_provider when it's another provider's): the fast model, so the
guards' classifiers, stays the workspace's default whatever is picked, and the
credentials are the workspace's own. release() names the answer model, so the
answer cache never serves one model's answer as another's.

TEST CONNECTION costs as little as proves it (test_connection): OpenAI and
Anthropic list their models, for free; Bedrock answers one token on its default
chat model and, when it embeds, makes one short embedding (a list wouldn't show
the model access granted, and would need a permission of its own).

FAIL CLOSED. A workspace never falls back to another provider: a model that can't
be made (no region, a role that can't be assumed, no API key) raises
ProviderError, and the caller fails as it would with OpenAI down. The code that
runs for a workspace runs inside scope(its Models) (agent.run, an index run, a
job), so the layers that don't know the workspace (the guards, HyDE, chunk
context) still ask for ITS models: current().

PROMPT CACHING. OpenAI caches the longest repeated PREFIX of a prompt
automatically once a prompt is long enough (1,024 tokens on current models), at
a discount on those input tokens. So prompts here put what never changes first
(the agent's rules, its tool list) and what changes last (the conversation, the
passages found). prompt_cache_key() groups requests that share that prefix, so
OpenAI routes them to the same cache: one key per workspace and role keeps one
workspace's traffic from probing another's cache. On Bedrock, caching is opt-in
per request: a cachePoint after the system prompt (BedrockChat), for the models
that support it (BEDROCK_PROMPT_CACHE). On Anthropic, a cache_control breakpoint
after the agent's rules (AnthropicChat; ANTHROPIC_PROMPT_CACHE): tools render
before the system prompt, so it caches both. The tokens served from the cache are
logged per question (question_log tokens_cached), from any provider.
"""

import contextlib
import contextvars
import hashlib
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Callable

log = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_EMBED_MODEL = "text-embedding-3-small"

# Bedrock's defaults (deploy.md, "AWS Bedrock"): Amazon's own models, which need no
# extra agreement in the Bedrock console, and support the Converse API with
# streaming and tool use. Each can be a model id, an inference profile id
# (us.amazon.nova-pro-v1:0, eu..., apac...) or an inference profile ARN.
BEDROCK_CHAT = "amazon.nova-pro-v1:0"
BEDROCK_FAST = "amazon.nova-lite-v1:0"
BEDROCK_EMBED = "amazon.titan-embed-text-v2:0"
BEDROCK_EMBED_DIMENSIONS = 1024
# Titan Text Embeddings V2 makes 1024, 512 or 256 numbers; Cohere Embed v3, 1024.
EMBED_DIMENSIONS = {"amazon.titan-embed-text-v2:0": (1024, 512, 256),
                    "cohere.embed-english-v3": (1024,),
                    "cohere.embed-multilingual-v3": (1024,)}
EMBED_CONCURRENCY = 4         # Titan embeds one text per call: this many at once

# Local embeddings (deploy.md, "Local embeddings"): fastembed (ONNX Runtime, no
# torch) on the server's CPU. The model is downloaded on first use into
# LOCAL_EMBED_CACHE. Sizes of the models likely to be picked; another one's comes
# from fastembed's own list.
LOCAL_EMBED = "BAAI/bge-small-en-v1.5"
LOCAL_EMBED_DIMENSIONS = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768,
                          "BAAI/bge-large-en-v1.5": 1024,
                          "sentence-transformers/all-MiniLM-L6-v2": 384,
                          "intfloat/multilingual-e5-large": 1024}
LOCAL_EMBED_THREADS = 2       # ONNX Runtime threads: indexing mustn't swamp a small server
LOCAL_EMBED_BATCH = 32

# Anthropic's defaults (deploy.md, "Anthropic"): the current Sonnet writes answers,
# the current Haiku does the small jobs. Claude has no embeddings: an Anthropic
# workspace's come from another provider (EMBED_PROVIDER, or the workspace's own).
ANTHROPIC_CHAT = "claude-sonnet-5-5"
ANTHROPIC_FAST = "claude-haiku-5-5"
ANTHROPIC_MIN_TOKENS = 1024

# What a value may look like (they go into AWS endpoints and ARNs, so nothing else).
REGION = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d{1,2}$")
MODEL_ID = re.compile(r"^(arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{0,12}:[a-z-]+/)?"
                      r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$")
ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]{1,512}$")


class ProviderError(RuntimeError):
    """A workspace's models can't be made or reached (Bedrock not set up, a role
    that can't be assumed, no API key, ...). Never a reason to use another provider."""


# ---- The registry: what each provider offers, and how its models are made ----------------
@dataclass(frozen=True)
class Provider:
    """One model provider. make_chat(models, model, openai=, cache_key=, **kwargs)
    makes a chat model; make_embeddings(models, model, dims, openai=) its
    embeddings (None: it has none). defaults() -> {chat_model, fast_model,
    embed_model, embed_dims}, the server's settings for it (.env)."""
    name: str
    label: str
    make_chat: Callable | None = None
    make_embeddings: Callable | None = None
    defaults: Callable = dict
    vision: bool = False              # its chat models read images
    prompt_caching: str = "none"      # automatic / marked (a marker per request) / none
    tool_calls: bool = False
    in_house: bool = False            # text stays in the customer's own cloud account
    api_key_env: str | None = None    # the server's key; a workspace can bring its own
    image_format: str = "standard"    # openai: image_url data URLs; standard: LangChain's

    @property
    def chat(self):
        return self.make_chat is not None

    @property
    def embeddings(self):
        return self.make_embeddings is not None

    def describe(self):
        """What the settings screen shows of it (GET /api/admin/assistant/models)."""
        return {"name": self.name, "label": self.label, "chat": self.chat,
                "embeddings": self.embeddings, "vision": self.vision,
                "prompt_caching": self.prompt_caching, "tool_calls": self.tool_calls,
                "in_house": self.in_house, "api_key": self.api_key_env is not None}


REGISTRY = {}


def register(provider):
    REGISTRY[provider.name] = provider
    return provider


def provider_info(name, offers=None):
    """The registry's entry for `name`; offers ("chat" / "embeddings"): it must
    offer that. Raises ProviderError otherwise (never a stand-in provider)."""
    found = REGISTRY.get(name)
    if found is None:
        raise ProviderError(f"Unknown model provider {name!r}.")
    if offers and not getattr(found, offers):
        raise ProviderError(f"{found.label} has no {offers} API.")
    return found


def chat_providers():
    return tuple(n for n, p in REGISTRY.items() if p.chat)


def embedding_providers():
    return tuple(n for n, p in REGISTRY.items() if p.embeddings)


# ---- The server's settings ---------------------------------------------------------------
def answer_model():
    return (os.getenv("ANSWER_MODEL") or "").strip() or DEFAULT_MODEL


def fast_model():
    return (os.getenv("FAST_MODEL") or "").strip() or DEFAULT_MODEL


def prompt_cache_key(collection, role):
    """The prompt_cache_key for a workspace's (collection's) agent for one role."""
    return f"kb:{collection or 'langchain'}:{role}"[:64]


def _env_provider(name, offers):
    """A provider named in .env (lowercased), or None when unset. Anything that
    isn't known, or doesn't offer what it's named for, is refused rather than
    read as openai: a typo must not send a private server's data out."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return None
    choices = chat_providers() if offers == "chat" else embedding_providers()
    if value not in choices:
        known = REGISTRY.get(value)
        why = (f"{known.label} has no {offers} API" if known else "it isn't a known provider")
        raise ProviderError(f"{name}={value!r}: {why}. Choose one of: {', '.join(choices)}.")
    return value


def server_provider():
    """The server's chat provider: CHAT_PROVIDER, else MODEL_PROVIDER, else openai."""
    return server_providers()[0]


def server_providers():
    """(chat, embeddings): the server's providers. MODEL_PROVIDER sets both, as it
    always has; CHAT_PROVIDER and EMBED_PROVIDER override one each. Without
    either for embeddings, the chat provider's own are used when it has them;
    when it has none (and MODEL_PROVIDER names none that does), that's an
    error naming EMBED_PROVIDER, never OpenAI by default."""
    base = (os.getenv("MODEL_PROVIDER") or "").strip().lower()
    if base and base not in REGISTRY:
        raise ProviderError(f"MODEL_PROVIDER={base!r} isn't one of: {', '.join(REGISTRY)}.")
    chat = _env_provider("CHAT_PROVIDER", "chat") or base or "openai"
    provider_info(chat, "chat")
    embed = _env_provider("EMBED_PROVIDER", "embeddings")
    if embed is None:
        for candidate in (base, chat):
            if candidate and REGISTRY[candidate].embeddings:
                embed = candidate
                break
    if embed is None:
        embed = default_embeddings_for(chat)
    return chat, embed


_said = set()


def default_embeddings_for(chat):
    """The embeddings provider a chat provider without embeddings (Anthropic) gets
    when none is set: local, on the server's own CPU (LOCAL_EMBED_MODEL), so a
    server with only an Anthropic key can search. Never OpenAI by default. Said
    once in the log."""
    if chat not in _said:
        _said.add(chat)
        log.info("Models: %s has no embeddings and EMBED_PROVIDER isn't set: the index "
                 "uses local embeddings (%s, on this server's CPU).", REGISTRY[chat].label,
                 _local_defaults()["embed_model"])
    return "local"


def log_server_providers():
    """At startup: which providers the server's models are on (or why they can't be)."""
    try:
        chat, embed = server_providers()
        log.info("Models: chat on %s, embeddings on %s.", chat, embed)
    except ProviderError as e:
        log.error("Models: %s", e)


def server_region():
    return ((os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
            or None)


def _env_int(name, default):
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def server_spec():
    """The server's own providers and models (.env)."""
    chat, embed = server_providers()
    if chat == embed == "openai":
        return Spec()
    on_chat, on_embed = REGISTRY[chat].defaults(), REGISTRY[embed].defaults()
    bedrock = "bedrock" in (chat, embed)
    return Spec(provider=chat, embed_provider=None if embed == chat else embed,
                region=server_region() if bedrock else None, auth="default",
                chat_model=on_chat.get("chat_model"), fast_model=on_chat.get("fast_model"),
                embed_model=on_embed.get("embed_model"), embed_dims=on_embed.get("embed_dims"))


# ---- A workspace's provider ---------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    """Which providers and models a workspace uses, and how to reach them.
    provider: chat (answer, fast, images); embed_provider: embeddings (None: the
    same). auth (bedrock): default = the server's AWS credentials; role = assume
    role_arn with external_id; keys = (access key id, secret access key).
    api_keys: ((provider, key), ...) for the providers the workspace brings its
    own key for; a provider listed with no key fails closed (never the server's).
    answer_provider: a chat's pick from another enabled provider (with_answer)."""
    provider: str = "openai"
    region: str | None = None
    auth: str = "default"
    role_arn: str | None = None
    external_id: str | None = None
    keys: tuple | None = field(default=None, repr=False)
    chat_model: str | None = None
    fast_model: str | None = None
    embed_model: str | None = None
    embed_dims: int | None = None
    workspace: str | None = None
    # What the workspace's search index was built with (embed_name), when known:
    # questions are embedded with it, or not at all when it's another provider's.
    index_embeddings: str | None = None
    error: str | None = None          # why it can't be used: every model call says so
    embed_provider: str | None = None
    api_keys: tuple | None = field(default=None, repr=False)
    # The chat's own choice of answer model (with_answer): its provider, when it
    # isn't `provider`. chat_model is then that provider's model; fast_model (the
    # guards, HyDE, titles) stays on `provider` whatever a chat picks.
    answer_provider: str | None = None

    @property
    def embeddings_provider(self):
        return self.embed_provider or self.provider

    def cache_key(self):
        """Identifies the credentials and settings (a hash stands for the keys).
        The workspace stays last (forget())."""
        secret = (hashlib.sha256(repr((self.keys, self.api_keys)).encode()).hexdigest()[:16]
                  if self.keys or self.api_keys else "")
        return (self.provider, self.embeddings_provider, self.region, self.auth,
                self.role_arn, self.external_id, secret, self.workspace)


_scope = contextvars.ContextVar("models_scope", default=None)


def current():
    """The Models of the workspace whose work this is (scope()), else the server's."""
    return _scope.get() or Models(server_spec())


@contextlib.contextmanager
def scope(models):
    """Run the code inside with `models` (a Models, or a workspace's slug)."""
    if isinstance(models, str):
        models = for_workspace(models)
    token = _scope.set(models)
    try:
        yield models
    finally:
        _scope.reset(token)


def for_workspace(ws, index_embeddings=None):
    """The workspace's Models: its own provider (model_providers_store.py) or the
    server's. index_embeddings: what its index was built with (indexing.py)."""
    import model_providers_store
    try:
        spec = model_providers_store.spec(ws) if ws else None
    except Exception as e:
        # Its settings can't be read (a shredded key, ...): it may have chosen
        # Bedrock, so nothing is sent anywhere. Fail closed.
        log.exception("Models: couldn't read workspace %s's provider", ws)
        spec = Spec(provider="bedrock", workspace=ws,
                    error=f"The workspace's model provider can't be read ({type(e).__name__}).")
    spec = spec or replace(server_spec(), workspace=ws)
    if index_embeddings is not None:
        spec = replace(spec, index_embeddings=index_embeddings)
    return Models(spec)


def _openai_chat():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI


def _openai_embeddings():
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings


class Models:
    """The models of one workspace (or the server): chat(), embeddings(), and the
    names the question log, the answer cache and the index fingerprint record."""

    def __init__(self, spec):
        self.spec = spec

    def __repr__(self):
        return f"Models({self.spec.provider}, {self.spec.workspace!r})"

    @property
    def provider(self):
        """The chat provider: who answers (the question log's provider)."""
        return self.spec.provider

    @property
    def answer_provider(self):
        """Who writes the answer: the chat's pick (with_answer), else `provider`."""
        return self.spec.answer_provider or self.spec.provider

    @property
    def embed_provider(self):
        return self.spec.embeddings_provider

    @property
    def bedrock(self):
        """The chat models are Bedrock's."""
        return self.spec.provider == "bedrock"

    @property
    def providers(self):
        """Every provider this work sends text to: the chat (fast) provider, the
        answer provider (a chat's pick, with_answer) and the embeddings provider."""
        return tuple(dict.fromkeys((self.provider, self.answer_provider, self.embed_provider)))

    def uses(self, name):
        """The chat, answer or embeddings provider is `name`. The answer provider
        counts: a chat answered on its Bedrock pick reranks with the workspace's
        own AWS (rerankers.BedrockReranker), never the server's."""
        return name in self.providers

    @property
    def in_house(self):
        """Some of the workspace's text stays in its own cloud account (Bedrock):
        then nothing else (an API reranker) may see it either."""
        return any(REGISTRY[p].in_house for p in self.providers if p in REGISTRY)

    def _defaults(self, name):
        p = REGISTRY.get(name)
        return p.defaults() if p else {}

    # ---- Names ----
    @property
    def answer_name(self):
        return self.spec.chat_model or self._defaults(self.answer_provider).get("chat_model")

    @property
    def fast_name(self):
        return self.spec.fast_model or self._defaults(self.provider).get("fast_model")

    def name(self, role):
        return self.answer_name if role == "answer" else self.fast_name

    @property
    def embed_model(self):
        return self.spec.embed_model or self._defaults(self.embed_provider).get("embed_model")

    @property
    def embed_dims(self):
        if self.spec.embed_dims or self.spec.embed_model:
            return self.spec.embed_dims
        return self._defaults(self.embed_provider).get("embed_dims")

    @property
    def embed_name(self):
        """The embedding model as the index and stored vectors record it: OpenAI's
        name alone (as before), or <provider>:<model>:<dimensions>."""
        return embed_name(self.embed_provider, self.embed_model, self.embed_dims)

    def release(self):
        """What changes this workspace's answers on the model side (the answer cache):
        the answer model a chat picked is part of it, so an answer written by one
        model is never served as another's. The same as before for the default."""
        out = f"{self.answer_provider}|{self.answer_name}|{self.fast_name}"
        return out if self.answer_provider == self.provider else f"{out}|{self.provider}"

    def with_answer(self, provider, model):
        """These Models with a chat's pick as the ANSWER model (an enabled one:
        kb/services/model_choice.py checks it). The fast model, the embeddings and
        the credentials stay the workspace's. The default itself changes nothing."""
        if provider == self.answer_provider and model == self.answer_name:
            return self
        if provider == self.provider:
            return Models(replace(self.spec, chat_model=model, answer_provider=None))
        return Models(replace(self.spec, chat_model=model, answer_provider=provider,
                              fast_model=self.fast_name))

    # ---- Keys ----
    def api_key(self, provider):
        """The workspace's own API key for `provider`, or None: the server's (the
        client reads its env). A provider the workspace brings its own key for,
        whose key is missing, fails closed: never the server's key instead."""
        own = dict(self.spec.api_keys or ())
        if provider in own:
            if not own[provider]:
                raise ProviderError(f"{REGISTRY[provider].label} is selected with the "
                                    "workspace's own API key, but none is saved.")
            return own[provider]
        return None

    def secrets(self):
        """Every secret this spec holds (to scrub from an error message)."""
        out = [k for _, k in self.spec.api_keys or () if k]
        return out + [k for k in self.spec.keys or () if k]

    # ---- Chat ----
    def chat(self, role="answer", openai=None, cache_key=None, **kwargs):
        """A chat model for `role` (answer or fast). openai: the caller's ChatOpenAI
        (a test's stand-in); cache_key: the prompt cache's group (prompt_cache_key).
        kwargs: temperature, max_tokens, timeout, max_retries."""
        if self.spec.error:
            raise ProviderError(self.spec.error)
        p = provider_info(self.answer_provider if role == "answer" else self.provider, "chat")
        return p.make_chat(self, self.name(role), openai=openai, cache_key=cache_key, **kwargs)

    def answer(self, openai=None, cache_key=None, **kwargs):
        return self.chat("answer", openai, cache_key, **kwargs)

    def fast(self, openai=None, **kwargs):
        return self.chat("fast", openai, **kwargs)

    def vision(self, model, openai=None, **kwargs):
        """A model that reads images: ATTACHMENT_VISION_MODEL on OpenAI, the chat
        model elsewhere (it must be a multimodal one: Nova Lite / Pro, Claude)."""
        if self.spec.error:
            raise ProviderError(self.spec.error)
        p = provider_info(self.answer_provider, "chat")
        if self.answer_provider == "openai":
            return p.make_chat(self, model, openai=openai, **kwargs)
        if not p.vision:
            raise ProviderError(f"{p.label}'s models don't read images.")
        return p.make_chat(self, self.answer_name, **kwargs)

    def image_block(self, mime, data_b64):
        """An image as this chat provider takes it in a message's content."""
        if provider_info(self.answer_provider).image_format == "openai":
            return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data_b64}"}}
        return {"type": "image", "source_type": "base64", "mime_type": mime, "data": data_b64}

    # ---- Embeddings ----
    def embeddings(self, openai=None):
        """The embeddings of this workspace's index (and anything compared with it)."""
        if self.spec.error:
            raise ProviderError(self.spec.error)
        p = provider_info(self.embed_provider, "embeddings")
        return p.make_embeddings(self, self.embed_model, self.embed_dims, openai=openai)

    def query_embeddings(self, openai=None):
        """The embeddings to search the workspace's index with, or None when it was
        built by ANOTHER provider (switching is rebuilding it): the search is then
        by keywords only until the rebuild is done, rather than send the question
        to a provider the workspace has left."""
        built = self.spec.index_embeddings
        if built is None:
            return self.embeddings(openai)
        provider, model, dims = parse_embed_name(built)
        if provider != self.embed_provider:
            return None
        if built != self.embed_name:
            # Same provider, other model: the index's own model until it's rebuilt.
            return replace_models(self, embed_model=model, embed_dims=dims).embeddings(openai)
        return self.embeddings(openai)

    # ---- What else may see the workspace's text ----
    def allows_reranker(self, reranker):
        """Whether the search may send candidates to `reranker` (rerankers.py): when
        the workspace keeps text in its own cloud (Bedrock, for chat or embeddings),
        only one that keeps them in-house (local, or Bedrock's own)."""
        if reranker is None or not self.in_house:
            return True
        return bool(getattr(reranker, "private", False))


def embed_name(provider, model, dims):
    """How an index records its embeddings: OpenAI's model name alone (every index
    from before providers has it), else <provider>:<model>:<dimensions>."""
    if provider == "openai":
        return model or OPENAI_EMBED_MODEL
    return f"{provider}:{model}:{dims}"


def parse_embed_name(name):
    """embed_name()'s parts: (provider, model, dimensions or None). A model id may
    hold colons itself (amazon.titan-embed-text-v2:0), the dimensions never do."""
    head, _, rest = (name or "").partition(":")
    if head not in REGISTRY or head == "openai" or not rest:
        return "openai", name or OPENAI_EMBED_MODEL, None
    model, _, dims = rest.rpartition(":")
    if not model:                     # <provider>:<model> with no size
        return head, dims, None
    return head, model, int(dims) if dims.isdigit() else None


class Lazy:
    """Embeddings made on first use (opening a store to read or drop it needs none)."""

    def __init__(self, make):
        self._make, self._made = make, None

    def _get(self):
        if self._made is None:
            self._made = self._make()
        return self._made

    def embed_documents(self, texts):
        return self._get().embed_documents(texts)

    def embed_query(self, text):
        return self._get().embed_query(text)


class NoEmbeddings:
    """Stands in for embeddings where none may be used (the index was built by a
    provider the workspace has left): a meaning search is refused, never sent."""

    def embed_documents(self, texts):
        raise ProviderError("This index is being rebuilt with the workspace's new provider.")

    embed_query = embed_documents


def scope_for(ws):
    """scope() for the workspace, keeping the one already set when it's the same
    workspace's (it may know more: what the index was built with)."""
    found = _scope.get()
    if found is not None and found.spec.workspace == ws:
        return contextlib.nullcontext(found)
    return scope(for_workspace(ws))


def replace_models(models, **change):
    return Models(replace(models.spec, **change))


def _provider_of(embed_name):
    return parse_embed_name(embed_name)[0]


def prompt_cache_on(model):
    """BEDROCK_PROMPT_CACHE: auto (the default) caches the system prompt for the
    models Bedrock caches prompts for (Amazon Nova, Anthropic Claude); on for any
    model, off for none."""
    mode = (os.getenv("BEDROCK_PROMPT_CACHE") or "auto").strip().lower()
    if mode in ("0", "off", "false", "no"):
        return False
    if mode in ("1", "on", "true", "yes"):
        return True
    m = (model or "").lower()
    return "amazon.nova" in m or "anthropic.claude" in m


# ---- Bedrock: credentials and clients -----------------------------------------------------
_clients = {}
_clients_lock = threading.Lock()
SESSION_SECONDS = 3600        # an assumed role's credentials last this long (STS default)


def _new_client(session, service, region, config=None):
    """A boto3 client (tests replace this with stubbed ones)."""
    return session.client(service, region_name=region, config=config)


def _session_name(workspace):
    """An AssumeRole session name: what the customer's CloudTrail shows ([\\w+=,.@-], 2-64)."""
    return re.sub(r"[^\w+=,.@-]", "-", f"knowledge-{workspace or 'server'}")[:64]


def _base_session(region):
    import boto3
    return boto3.Session(region_name=region)


def assume_role(spec):
    """Temporary credentials for the workspace's role (botocore's metadata shape)."""
    sts = _new_client(_base_session(spec.region), "sts", spec.region)
    try:
        got = sts.assume_role(RoleArn=spec.role_arn, ExternalId=spec.external_id,
                              RoleSessionName=_session_name(spec.workspace),
                              DurationSeconds=SESSION_SECONDS)["Credentials"]
    except Exception as e:
        raise ProviderError(f"Couldn't assume the AWS role {spec.role_arn}: "
                            f"{_aws_error(e)}") from e
    return {"access_key": got["AccessKeyId"], "secret_key": got["SecretAccessKey"],
            "token": got["SessionToken"], "expiry_time": got["Expiration"].isoformat()}


def session(spec):
    """A boto3 session with the spec's credentials. An assumed role's are refreshed
    shortly before they expire (botocore RefreshableCredentials), not per call."""
    import boto3
    if spec.error:
        raise ProviderError(spec.error)
    if not spec.region:
        raise ProviderError("Bedrock is selected but no AWS region is set.")
    if spec.auth == "keys":
        if not spec.keys or not all(spec.keys):
            raise ProviderError("Bedrock is selected with access keys, but none are saved.")
        return boto3.Session(aws_access_key_id=spec.keys[0], aws_secret_access_key=spec.keys[1],
                             region_name=spec.region)
    if spec.auth == "role":
        if not (spec.role_arn and spec.external_id):
            raise ProviderError("Bedrock is selected with a role, but no role ARN is saved.")
        from botocore.credentials import RefreshableCredentials
        from botocore.session import get_session
        credentials = RefreshableCredentials.create_from_metadata(
            metadata=assume_role(spec), refresh_using=lambda: assume_role(spec),
            method="sts-assume-role")
        core = get_session()
        core._credentials = credentials
        core.set_config_variable("region", spec.region)
        return boto3.Session(botocore_session=core)
    return _base_session(spec.region)


def client(spec, service, timeout=None, retries=None):
    """A cached boto3 client of `service` for the spec (one per credentials, so an
    assumed role is assumed once and refreshed, not on every question)."""
    from botocore.config import Config
    key = (spec.cache_key(), service, timeout, retries)
    with _clients_lock:
        found = _clients.get(key)
    if found is not None:
        return found
    config = Config(read_timeout=timeout or 120, connect_timeout=10,
                    retries={"max_attempts": 1 + (3 if retries is None else retries),
                             "mode": "standard"})
    made = _new_client(_cached_session(spec), service, spec.region, config)
    with _clients_lock:
        return _clients.setdefault(key, made)


def _cached_session(spec):
    key = ("session", spec.cache_key())
    with _clients_lock:
        found = _clients.get(key)
    if found is None:
        found = session(spec)
        with _clients_lock:
            found = _clients.setdefault(key, found)
    return found


def runtime_client(spec, timeout=None, retries=None):
    return client(spec, "bedrock-runtime", timeout, retries)


def forget(workspace=None):
    """Drop cached clients (a workspace's settings changed): the next call makes new ones."""
    with _clients_lock:
        for key in [k for k in _clients if workspace is None or k[1][-1] == workspace
                    or k[0][-1] == workspace]:
            del _clients[key]


def _aws_error(e):
    """An AWS error's code and message, without anything secret in it. Another
    provider's API error (Anthropic's, OpenAI's): its class, type and message."""
    response = getattr(e, "response", None)
    err = (response.get("Error") or {}) if isinstance(response, dict) else {}
    if err:
        return f"{err.get('Code', 'Error')}: {err.get('Message', '')}".strip()
    body = getattr(e, "body", None)
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("message"):
        return f"{type(e).__name__} ({detail.get('type', 'error')}): {detail['message']}"
    return f"{type(e).__name__}: {e}"


# ---- Bedrock: the models -------------------------------------------------------------------
def text_of(content):
    """A message's text: Bedrock replies in content blocks ([{"type": "text", ...}]);
    the rest of the app reads a plain string, as OpenAI's are."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                   else b if isinstance(b, str) else "" for b in content or [])


def _bedrock_classes():
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    return ChatBedrockConverse, BedrockEmbeddings


def _make_bedrock_chat():
    from langchain_core.messages import SystemMessage
    from langchain_core.outputs import ChatGenerationChunk, ChatResult
    ChatBedrockConverse, _ = _bedrock_classes()

    class BedrockChat(ChatBedrockConverse):
        """ChatBedrockConverse whose replies are plain text, as OpenAI's are (tool
        calls stay in .tool_calls), with the system prompt marked for Bedrock's
        prompt cache when prompt_cache is on."""
        prompt_cache: bool = False

        def _cached(self, messages):
            if not self.prompt_cache or not messages or not isinstance(messages[0], SystemMessage):
                return messages
            first = messages[0]
            blocks = ([{"type": "text", "text": first.content}]
                      if isinstance(first.content, str) else list(first.content))
            return [SystemMessage(content=blocks + [{"cachePoint": {"type": "default"}}]),
                    *messages[1:]]

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(self._cached(messages), stop, run_manager, **kwargs)
            for g in result.generations:
                g.message.content = text_of(g.message.content)
            return ChatResult(generations=result.generations, llm_output=result.llm_output)

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            for chunk in super()._stream(self._cached(messages), stop, run_manager, **kwargs):
                message = chunk.message
                message.content = text_of(message.content)
                yield ChatGenerationChunk(message=message,
                                          generation_info=chunk.generation_info)

    return BedrockChat


def _make_bedrock_embed():
    _, BedrockEmbeddings = _bedrock_classes()

    class BedrockEmbed(BedrockEmbeddings):
        """BedrockEmbeddings embedding a batch EMBED_CONCURRENCY texts at a time
        (Titan takes one text per call)."""

        def _iteratively_embed_documents(self, texts):
            def one(text):
                vector = self._embedding_func(text)
                return self._normalize_vector(vector) if self.normalize else vector
            if len(texts) < 2:
                return [one(t) for t in texts]
            with ThreadPoolExecutor(max_workers=EMBED_CONCURRENCY) as pool:
                return list(pool.map(one, texts))

    return BedrockEmbed


_made = {}


def _cls(name):
    """BedrockChat / BedrockEmbed / AnthropicChat, made on first use: importing
    this file never needs langchain-aws, boto3 or langchain-anthropic (an
    OpenAI-only server)."""
    if name not in _made:
        _made[name] = {"BedrockChat": _make_bedrock_chat, "BedrockEmbed": _make_bedrock_embed,
                       "AnthropicChat": _make_anthropic_chat}[name]()
    return _made[name]


def __getattr__(name):
    if name in ("BedrockChat", "BedrockEmbed", "AnthropicChat"):
        return _cls(name)
    raise AttributeError(name)


# ---- Anthropic: the models -----------------------------------------------------------------
def _make_anthropic_chat():
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import AIMessage, SystemMessage
    from langchain_core.outputs import ChatGenerationChunk, ChatResult

    class AnthropicChat(ChatAnthropic):
        """ChatAnthropic whose replies are plain text, as OpenAI's are (tool calls
        stay in .tool_calls), with the stable prompt marked for Anthropic's prompt
        cache when prompt_cache is on.

        A reply's own content blocks (thinking, tool_use) are kept in
        additional_kwargs[KEPT] and given back to Claude when the reply is part
        of the next request (the agent's tool loop): Claude needs its thinking
        blocks back unchanged, the rest of the app reads text."""
        prompt_cache: bool = False

        def _prepared(self, messages):
            out = []
            for m in messages:
                if isinstance(m, AIMessage) and m.additional_kwargs.get(KEPT) is not None:
                    m = m.model_copy(update={"content": m.additional_kwargs[KEPT]})
                out.append(m)
            # The leading system messages as one: the agent's rules (the same for
            # every question) first, then what this question adds (glossary
            # notes, ...). The cache breakpoint goes after the rules: with the tool
            # list before them, that is the prefix every question shares.
            lead = 0
            while lead < len(out) and isinstance(out[lead], SystemMessage):
                lead += 1
            if not lead:
                return out
            marked = any(isinstance(b, dict) and "cache_control" in b for m in out[:lead]
                         if isinstance(m.content, list) for b in m.content)
            blocks = []
            for i, m in enumerate(out[:lead]):
                part = ([{"type": "text", "text": m.content}] if isinstance(m.content, str)
                        else [dict(b) if isinstance(b, dict) else {"type": "text", "text": b}
                              for b in m.content])
                part = [b for b in part if b.get("type") != "text" or b.get("text")]
                if i == 0 and self.prompt_cache and part and not marked:
                    part[-1] = {**part[-1], "cache_control": {"type": "ephemeral"}}
                blocks += part
            return [SystemMessage(content=blocks), *out[lead:]]

        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            # No system prompt but tools (rare here): the breakpoint after them.
            tools = payload.get("tools")
            if self.prompt_cache and tools and not payload.get("system"):
                payload["tools"] = [*tools[:-1], {**tools[-1],
                                                  "cache_control": {"type": "ephemeral"}}]
            return payload

        @staticmethod
        def _plain(message):
            if isinstance(message.content, list):
                message.additional_kwargs[KEPT] = message.content
                message.content = text_of(message.content)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(self._prepared(messages), stop, run_manager, **kwargs)
            for g in result.generations:
                self._plain(g.message)
            return ChatResult(generations=result.generations, llm_output=result.llm_output)

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            # The tokens are announced here, as text (ChatAnthropic announces only
            # string chunks, and with tools bound its chunks are content blocks).
            for chunk in super()._stream(self._prepared(messages), stop, None, **kwargs):
                message = chunk.message
                self._plain(message)
                chunk = ChatGenerationChunk(message=message, generation_info=chunk.generation_info)
                if run_manager:
                    run_manager.on_llm_new_token(message.content, chunk=chunk)
                yield chunk

    return AnthropicChat


KEPT = "anthropic_content"


# ---- Bedrock: what the admin sets up, and checking it ---------------------------------------
_PROFILE = re.compile(r"^(us|eu|apac|ca|au|jp|in|us-gov|global)\.")


def model_arns(model, region):
    """The IAM resources a model id needs: its foundation model in `region`, or for
    an inference profile (us.amazon.nova-pro-v1:0, global....) the profile AND its
    foundation model in every region the profile may send requests to (AWS: "you
    must also specify the foundation model in each Region associated with it")."""
    if model.startswith("arn:"):
        return [model]
    if _PROFILE.match(model):
        base = _PROFILE.sub("", model)
        return [f"arn:aws:bedrock:{region}:*:inference-profile/{model}",
                f"arn:aws:bedrock:*::foundation-model/{base}"]
    return [f"arn:aws:bedrock:{region}::foundation-model/{model}"]


def permissions_policy(spec, enabled=()):
    """The IAM policy the workspace's role (or user) needs, for its models (and the
    other Bedrock chat models it enables, `enabled`). Test connection invokes
    them too (test_connection), so it needs nothing more."""
    region = spec.region or "REGION"
    chat = sorted({a for m in {spec.chat_model, spec.fast_model, *enabled} if m
                   for a in model_arns(m, region)})
    embed = model_arns(spec.embed_model, region) if spec.embed_model else []
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "KnowledgeAssistantChat", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": chat},
        {"Sid": "KnowledgeAssistantEmbeddings", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel"], "Resource": embed}]}


def trust_policy(external_id):
    """The role's trust policy: the server's AWS principal may assume it, only with
    the workspace's ExternalId. BEDROCK_SERVER_PRINCIPAL: the server's AWS account
    id or IAM role ARN (deploy.md, "AWS Bedrock")."""
    principal = (os.getenv("BEDROCK_SERVER_PRINCIPAL") or "").strip()
    if re.fullmatch(r"\d{12}", principal):
        principal = f"arn:aws:iam::{principal}:root"
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"AWS": principal or "SERVER_AWS_ACCOUNT_OR_ROLE_ARN"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": external_id}}}]}


TEST_PROMPT = "Reply with the single word OK."     # Bedrock's test (test_connection)
_KEYLIKE = re.compile(r"\b(sk|key)-[A-Za-z0-9_\-]{6,}")


def describe_error(e, secrets=()):
    """A provider's error as the admin is shown it: AWS's code and message, or
    the client's own, with any key in it blanked out."""
    return scrub(_aws_error(e), secrets)


def scrub(text, secrets=()):
    """`text` with each secret, and anything shaped like an API key, blanked out."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return _KEYLIKE.sub(lambda m: m.group(1) + "-***", text)


# ---- Test connection: as little paid usage as proves it ---------------------------------
# OpenAI and Anthropic: listing the models is free and proves the key (and which
# enabled models it reaches). Bedrock: listing proves neither the role's right to
# invoke nor the model access granted in the account (ListFoundationModels lists
# models with no access), and needs a permission of its own. So one Converse of a
# single token on its default chat model and, when it embeds, one short embedding:
# a fraction of a cent, with only the InvokeModel the policy already grants.
TEST_TIMEOUT = 20


def _openai_client(key):
    """The OpenAI SDK's client (tests replace it): the workspace's key, else the
    server's OPENAI_API_KEY (the client reads it)."""
    import openai
    return openai.OpenAI(api_key=key or None, timeout=TEST_TIMEOUT, max_retries=0)


def _anthropic_client(key):
    import anthropic
    return anthropic.Client(api_key=key, timeout=TEST_TIMEOUT, max_retries=0)


def _list_openai(on):
    return {m.id for m in _openai_client(on.api_key("openai")).models.list()}


def _list_anthropic(on):
    key = on.api_key("anthropic") or (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ProviderError("Anthropic is selected but no API key is set.")
    return {m.id for m in _anthropic_client(key).models.list(limit=1000)}


# provider -> its free list of model ids (tests replace one to stand in for its API).
LISTS = {"openai": _list_openai, "anthropic": _list_anthropic}


def _check_bedrock(on, looked_for):
    """Bedrock's test: one Converse of a single token on its default chat model
    (the workspace's default when that's Bedrock's, else its first enabled Bedrock
    model), and one short embedding when Bedrock embeds. -> the model ids it
    reached. The other enabled Bedrock models aren't invoked: each would be paid."""
    checked = []
    chat = (on.answer_name if on.answer_provider == "bedrock"
            else next((m for m in looked_for if m), None))
    if chat:
        runtime_client(on.spec, timeout=TEST_TIMEOUT, retries=0).converse(
            modelId=chat, messages=[{"role": "user", "content": [{"text": TEST_PROMPT}]}],
            inferenceConfig={"maxTokens": 1, "temperature": 0})
        checked.append(chat)
    if on.embed_provider == "bedrock":
        on.embeddings().embed_query("connection test")
        checked.append(on.embed_model)
    return checked


# provider -> its minimal real check (no free list proves enough): the models reached.
CHECKS = {"bedrock": _check_bedrock}


def test_connection(spec, wanted=()):
    """Test connection on the Assistant screen: each provider the spec uses lists
    its models, for free (OpenAI, Anthropic), or makes its minimal real check
    (Bedrock, CHECKS). wanted: ((provider, model), ...), the chat models to look
    for, the default first (their providers are tested too). -> {"ok",
    "providers": [{"provider", "label", "ok", "models": how many it listed (None:
    no list), "missing": wanted models it didn't list, "checked"?: the models a
    real call reached, "error"?}]}. A provider listing its models without one of
    the wanted ones fails, saying which: an id mistyped, or a model the key
    can't use. A provider with neither (local) is ok without a call. A
    provider's own failure is its row's error, the key blanked out."""
    if spec.error:
        raise ProviderError(spec.error)
    on = Models(spec)
    by = {}
    for provider, model in wanted:
        by.setdefault(provider, []).append(model)
    for name in (spec.provider, spec.embeddings_provider):
        by.setdefault(name, [])
    rows = []
    for name, looked_for in by.items():
        row = {"provider": name, "label": provider_info(name).label, "ok": True,
               "models": None, "missing": []}
        lister, check = LISTS.get(name), CHECKS.get(name)
        try:
            if lister is not None:
                ids = lister(on)
                row["models"] = len(ids)
                row["missing"] = [m for m in dict.fromkeys(looked_for)
                                  if m and m not in ids]
                if row["missing"]:
                    row.update(ok=False, error=(
                        f"It lists {len(ids):,} models, but not {', '.join(row['missing'])}: "
                        "check the model id and that this key can use it."))
            elif check is not None:
                row["checked"] = check(on, looked_for)
        except Exception as e:
            text = (scrub(str(e), on.secrets()) if isinstance(e, ProviderError)
                    else describe_error(e, on.secrets()))
            row.update(ok=False, error=text[:500])
        rows.append(row)
    return {"ok": all(r["ok"] for r in rows), "providers": rows}


# ---- The catalogue: the chat models an admin can enable, per provider -----------------------
@dataclass(frozen=True)
class ModelInfo:
    """One chat model the settings screen offers. tier: fast / balanced / best;
    vision: it reads images; tool_calls: it can call the agent's tools."""
    id: str
    label: str
    tier: str = "balanced"
    vision: bool = True
    tool_calls: bool = True

    def describe(self):
        return {"id": self.id, "label": self.label, "tier": self.tier, "vision": self.vision,
                "tool_calls": self.tool_calls}


TIERS = ("fast", "balanced", "best")
# Curated: what's current and supports tool calls. An admin can add any other id
# (a custom model); Test connection says whether the provider lists it.
CATALOGUE = {
    "openai": (
        ModelInfo("gpt-5", "GPT-5", "best"),
        ModelInfo("gpt-5-mini", "GPT-5 mini", "balanced"),
        ModelInfo("gpt-4.1", "GPT-4.1", "balanced"),
        ModelInfo("gpt-4o", "GPT-4o", "balanced"),
        ModelInfo("gpt-4o-mini", "GPT-4o mini", "fast"),
        ModelInfo("gpt-4.1-mini", "GPT-4.1 mini", "fast"),
        ModelInfo("gpt-5-nano", "GPT-5 nano", "fast"),
    ),
    "anthropic": (
        ModelInfo("claude-opus-5-5", "Claude Opus 5.5", "best"),
        ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", "balanced"),
        ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", "fast"),
    ),
    "bedrock": (
        ModelInfo("amazon.nova-pro-v1:0", "Amazon Nova Pro", "balanced"),
        ModelInfo("amazon.nova-lite-v1:0", "Amazon Nova Lite", "fast"),
        ModelInfo("amazon.nova-micro-v1:0", "Amazon Nova Micro", "fast", vision=False),
    ),
}


def model_info(provider, model):
    """The catalogue's entry for a model, or one for a custom id: its id as its
    label, balanced, with what its provider can do."""
    for m in CATALOGUE.get(provider, ()):
        if m.id == model:
            return m
    p = REGISTRY.get(provider)
    return ModelInfo(model, model, "balanced", vision=bool(p and p.vision),
                     tool_calls=bool(p and p.tool_calls))


# ---- The providers ---------------------------------------------------------------------------
def _openai_make_chat(on, model, openai=None, cache_key=None, **kwargs):
    """ChatOpenAI as every call site made it before providers: the caller's class
    (a test's stand-in), the model, the prompt cache's key; the workspace's own
    API key only when it brings one."""
    if cache_key is not None:
        kwargs["model_kwargs"] = {"prompt_cache_key": cache_key}
    key = on.api_key("openai")
    if key:
        kwargs["api_key"] = key
    return (openai or _openai_chat())(model=model, **kwargs)


def _openai_make_embeddings(on, model, dims, openai=None):
    key = on.api_key("openai")
    return (openai or _openai_embeddings())(model=model, **({"api_key": key} if key else {}))


def _openai_defaults():
    return {"chat_model": answer_model(), "fast_model": fast_model(),
            "embed_model": OPENAI_EMBED_MODEL, "embed_dims": None}


def _bedrock_make_chat(on, model, openai=None, cache_key=None, temperature=None,
                       max_tokens=None, timeout=None, max_retries=None):
    runtime = runtime_client(on.spec, timeout=timeout, retries=max_retries)
    control = client(on.spec, "bedrock")      # only an application profile's lookup
    return _cls("BedrockChat")(model=model, client=runtime, bedrock_client=control,
                               region_name=on.spec.region, temperature=temperature,
                               max_tokens=max_tokens, prompt_cache=prompt_cache_on(model))


def _bedrock_make_embeddings(on, model, dims, openai=None):
    return _cls("BedrockEmbed")(model_id=model, client=runtime_client(on.spec),
                                region_name=on.spec.region, dimensions=dims, normalize=True)


def _bedrock_defaults():
    return {"chat_model": (os.getenv("BEDROCK_CHAT_MODEL") or "").strip() or BEDROCK_CHAT,
            "fast_model": (os.getenv("BEDROCK_FAST_MODEL") or "").strip() or BEDROCK_FAST,
            "embed_model": (os.getenv("BEDROCK_EMBED_MODEL") or "").strip() or BEDROCK_EMBED,
            "embed_dims": _env_int("BEDROCK_EMBED_DIMENSIONS", BEDROCK_EMBED_DIMENSIONS)}


register(Provider(
    name="openai", label="OpenAI", make_chat=_openai_make_chat,
    make_embeddings=_openai_make_embeddings, defaults=_openai_defaults, vision=True,
    prompt_caching="automatic", tool_calls=True, api_key_env="OPENAI_API_KEY",
    image_format="openai"))
register(Provider(
    name="bedrock", label="AWS Bedrock", make_chat=_bedrock_make_chat,
    make_embeddings=_bedrock_make_embeddings, defaults=_bedrock_defaults, vision=True,
    prompt_caching="marked", tool_calls=True, in_house=True))


def _anthropic_make_chat(on, model, openai=None, cache_key=None, temperature=None,
                         max_tokens=None, timeout=None, max_retries=None):
    """Claude through Anthropic's API (langchain-anthropic): the workspace's own key,
    else the server's ANTHROPIC_API_KEY; neither fails closed. temperature is not
    sent: current Claude models refuse sampling settings other than their own.
    A small max_tokens is raised to ANTHROPIC_MIN_TOKENS, since thinking counts
    toward it and a one-word classifier's reply mustn't be cut off by it."""
    key = on.api_key("anthropic") or (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ProviderError("Anthropic is selected but no API key is set (ANTHROPIC_API_KEY, "
                            "or the workspace's own on the Assistant screen).")
    kwargs = {"model": model, "api_key": key, "prompt_cache": anthropic_prompt_cache_on(model)}
    if max_tokens is not None:
        kwargs["max_tokens"] = max(max_tokens, ANTHROPIC_MIN_TOKENS)
    if timeout is not None:
        kwargs["default_request_timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    effort = (os.getenv("ANTHROPIC_FAST_EFFORT") or "").strip().lower()
    if effort and model == on.fast_name and model != on.answer_name:
        kwargs["effort"] = effort
    return _cls("AnthropicChat")(**kwargs)


def _anthropic_defaults():
    return {"chat_model": (os.getenv("ANTHROPIC_CHAT_MODEL") or "").strip() or ANTHROPIC_CHAT,
            "fast_model": (os.getenv("ANTHROPIC_FAST_MODEL") or "").strip() or ANTHROPIC_FAST,
            "embed_model": None, "embed_dims": None}


def anthropic_prompt_cache_on(model):
    """ANTHROPIC_PROMPT_CACHE: on (the default) marks the stable prompt for every
    Claude model; off for none. A prompt shorter than the model's minimum (512 to
    4,096 tokens) just isn't cached."""
    mode = (os.getenv("ANTHROPIC_PROMPT_CACHE") or "on").strip().lower()
    return mode not in ("0", "off", "false", "no")


def _local_cache_dir():
    """LOCAL_EMBED_CACHE, else models/fastembed next to the database (the data
    directory, a persisted volume in Docker), never inside the code."""
    import db
    return ((os.getenv("LOCAL_EMBED_CACHE") or "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "models", "fastembed"))


def _local_defaults():
    model = (os.getenv("LOCAL_EMBED_MODEL") or "").strip() or LOCAL_EMBED
    return {"chat_model": None, "fast_model": None, "embed_model": model,
            "embed_dims": local_dimensions(model)}


def local_dimensions(model):
    """How many numbers `model` makes (the index records it), or None if unknown."""
    if model in LOCAL_EMBED_DIMENSIONS:
        return LOCAL_EMBED_DIMENSIONS[model]
    try:
        from fastembed import TextEmbedding
        return next((m["dim"] for m in TextEmbedding.list_supported_models()
                     if m["model"] == model), None)
    except Exception:
        return None


def _fastembed():
    """fastembed's TextEmbedding (tests replace it: no download in CI)."""
    from fastembed import TextEmbedding
    return TextEmbedding


_local_models = {}
_local_lock = threading.Lock()


def _local_model(model, cache_dir, threads):
    """One loaded model per (model, folder, threads), shared: loading it (and the
    first download) happens once, under a lock."""
    key = (model, cache_dir, threads)
    with _local_lock:
        found = _local_models.get(key)
        if found is None:
            os.makedirs(cache_dir, exist_ok=True)
            try:
                found = _fastembed()(model_name=model, cache_dir=cache_dir, threads=threads)
            except Exception as e:
                raise ProviderError(f"The local embedding model {model} couldn't be loaded "
                                    f"({type(e).__name__}: {e}).") from e
            _local_models[key] = found
    return found


class LocalEmbeddings:
    """LangChain's Embeddings interface over fastembed, loading the model on the
    first text (opening an index to read it needs none), BATCH texts at a time."""

    def __init__(self, model, cache_dir, threads, batch_size):
        self.model, self.cache_dir = model, cache_dir
        self.threads, self.batch_size = threads, batch_size

    def _loaded(self):
        return _local_model(self.model, self.cache_dir, self.threads)

    def embed_documents(self, texts):
        texts = list(texts)
        if not texts:
            return []
        return [v.tolist() for v in self._loaded().embed(texts, batch_size=self.batch_size)]

    def embed_query(self, text):
        return next(iter(self._loaded().query_embed(text))).tolist()


def _local_make_embeddings(on, model, dims, openai=None):
    return LocalEmbeddings(model, _local_cache_dir(),
                           max(1, _env_int("LOCAL_EMBED_THREADS", LOCAL_EMBED_THREADS)),
                           max(1, _env_int("LOCAL_EMBED_BATCH", LOCAL_EMBED_BATCH)))


register(Provider(
    name="local", label="Local (on this server)", make_embeddings=_local_make_embeddings,
    defaults=_local_defaults))
register(Provider(
    name="anthropic", label="Anthropic", make_chat=_anthropic_make_chat,
    defaults=_anthropic_defaults, vision=True, prompt_caching="marked", tool_calls=True,
    api_key_env="ANTHROPIC_API_KEY"))
PROVIDERS = tuple(REGISTRY)
