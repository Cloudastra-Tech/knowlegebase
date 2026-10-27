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

PROVIDERS. Every model is made here, through a workspace's Models (for_workspace):

    openai     OpenAI (the default): ANSWER_MODEL, FAST_MODEL, text-embedding-3-small.
               Made exactly as before: each caller passes its own ChatOpenAI /
               OpenAIEmbeddings name, so a test can stand in for one call site.
    bedrock    AWS Bedrock in the company's own AWS account and region: chat
               through the Converse API (langchain-aws ChatBedrockConverse:
               streaming and tool calls), embeddings through InvokeModel (Titan
               Text Embeddings V2 by default). Documents and questions never
               reach OpenAI.
    (azure_openai, later: a Spec.provider value and a branch in Models.chat /
               Models.embeddings, like bedrock's.)

The server's provider is MODEL_PROVIDER (openai unless set) with its AWS_REGION and
BEDROCK_* models, using the server's own AWS credentials (boto3's default chain:
an instance role, AWS_PROFILE, AWS_ACCESS_KEY_ID...). A workspace's admin can bring
their own AWS instead (model_providers_store.py, the Assistant screen): a role in
THEIR account the server assumes (STS AssumeRole with the ExternalId this app
generated for them; credentials kept until they expire) or access keys sealed
under the workspace's key (secret_box).

FAIL CLOSED. A workspace on Bedrock never falls back to OpenAI: a model that can't
be made (no region, a role that can't be assumed) raises ProviderError, and the
caller fails as it would with OpenAI down. The code that runs for a workspace
runs inside scope(its Models) (agent.run, an index run, a job), so the layers
that don't know the workspace (the guards, HyDE, chunk context) still ask for
ITS models: current().

PROMPT CACHING. OpenAI caches the longest repeated PREFIX of a prompt
automatically once a prompt is long enough (1,024 tokens on current models), at
a discount on those input tokens. So prompts here put what never changes first
(the agent's rules, its tool list) and what changes last (the conversation, the
passages found). prompt_cache_key() groups requests that share that prefix, so
OpenAI routes them to the same cache: one key per workspace and role keeps one
workspace's traffic from probing another's cache. On Bedrock, caching is opt-in
per request: a cachePoint after the system prompt (BedrockChat), for the models
that support it (BEDROCK_PROMPT_CACHE). The tokens served from the cache are
logged per question (question_log tokens_cached), from either provider.
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

log = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_EMBED_MODEL = "text-embedding-3-small"

PROVIDERS = ("openai", "bedrock")

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

# What a value may look like (they go into AWS endpoints and ARNs, so nothing else).
REGION = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d{1,2}$")
MODEL_ID = re.compile(r"^(arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{0,12}:[a-z-]+/)?"
                      r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$")
ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]{1,512}$")


class ProviderError(RuntimeError):
    """A workspace's models can't be made or reached (Bedrock not set up, a role
    that can't be assumed, ...). Never a reason to use another provider."""


# ---- The server's settings ---------------------------------------------------------------
def answer_model():
    return (os.getenv("ANSWER_MODEL") or "").strip() or DEFAULT_MODEL


def fast_model():
    return (os.getenv("FAST_MODEL") or "").strip() or DEFAULT_MODEL


def prompt_cache_key(collection, role):
    """The prompt_cache_key for a workspace's (collection's) agent for one role."""
    return f"kb:{collection or 'langchain'}:{role}"[:64]


def server_provider():
    """MODEL_PROVIDER: openai (the default) or bedrock. Anything else is refused
    rather than read as openai: a typo must not send a private server's data out."""
    value = (os.getenv("MODEL_PROVIDER") or "openai").strip().lower() or "openai"
    if value not in PROVIDERS:
        raise ProviderError(f"MODEL_PROVIDER={value!r} isn't one of: {', '.join(PROVIDERS)}.")
    return value


def server_region():
    return ((os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
            or None)


def _env_int(name, default):
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def server_spec():
    """The server's own provider and models (.env)."""
    provider = server_provider()
    if provider == "openai":
        return Spec()
    return Spec(provider="bedrock", region=server_region(), auth="default",
                chat_model=(os.getenv("BEDROCK_CHAT_MODEL") or "").strip() or BEDROCK_CHAT,
                fast_model=(os.getenv("BEDROCK_FAST_MODEL") or "").strip() or BEDROCK_FAST,
                embed_model=(os.getenv("BEDROCK_EMBED_MODEL") or "").strip() or BEDROCK_EMBED,
                embed_dims=_env_int("BEDROCK_EMBED_DIMENSIONS", BEDROCK_EMBED_DIMENSIONS))


# ---- A workspace's provider ---------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    """Which provider and models a workspace uses, and how to reach them.
    auth (bedrock): default = the server's AWS credentials; role = assume role_arn
    with external_id; keys = (access key id, secret access key)."""
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

    def cache_key(self):
        """Identifies the credentials and settings (a hash stands for the keys)."""
        secret = hashlib.sha256(repr(self.keys).encode()).hexdigest()[:16] if self.keys else ""
        return (self.provider, self.region, self.auth, self.role_arn, self.external_id,
                secret, self.workspace)


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
        return self.spec.provider

    @property
    def bedrock(self):
        return self.spec.provider == "bedrock"

    # ---- Names ----
    @property
    def answer_name(self):
        return self.spec.chat_model if self.bedrock else answer_model()

    @property
    def fast_name(self):
        return self.spec.fast_model if self.bedrock else fast_model()

    def name(self, role):
        return self.answer_name if role == "answer" else self.fast_name

    @property
    def embed_name(self):
        """The embedding model as the index and stored vectors record it: OpenAI's
        name alone (as before), or bedrock:<model>:<dimensions>."""
        if self.bedrock:
            return f"bedrock:{self.spec.embed_model}:{self.spec.embed_dims}"
        return OPENAI_EMBED_MODEL

    def release(self):
        """What changes this workspace's answers on the model side (the answer cache)."""
        return f"{self.provider}|{self.answer_name}|{self.fast_name}"

    # ---- Chat ----
    def chat(self, role="answer", openai=None, cache_key=None, **kwargs):
        """A chat model for `role` (answer or fast). openai: the caller's ChatOpenAI
        (a test's stand-in); cache_key: the prompt cache's group (prompt_cache_key).
        kwargs: temperature, max_tokens, timeout, max_retries."""
        if not self.bedrock:
            if cache_key is not None:
                kwargs["model_kwargs"] = {"prompt_cache_key": cache_key}
            return (openai or _openai_chat())(model=self.name(role), **kwargs)
        return self._bedrock_chat(self.name(role), **kwargs)

    def answer(self, openai=None, cache_key=None, **kwargs):
        return self.chat("answer", openai, cache_key, **kwargs)

    def fast(self, openai=None, **kwargs):
        return self.chat("fast", openai, **kwargs)

    def vision(self, model, openai=None, **kwargs):
        """A model that reads images: ATTACHMENT_VISION_MODEL on OpenAI, the chat
        model on Bedrock (it must be a multimodal one: Nova Lite / Pro, Claude)."""
        if not self.bedrock:
            return (openai or _openai_chat())(model=model, **kwargs)
        return self._bedrock_chat(self.spec.chat_model, **kwargs)

    def _bedrock_chat(self, model, temperature=None, max_tokens=None, timeout=None,
                      max_retries=None):
        runtime = runtime_client(self.spec, timeout=timeout, retries=max_retries)
        control = client(self.spec, "bedrock")      # only an application profile's lookup
        return _cls("BedrockChat")(model=model, client=runtime, bedrock_client=control,
                                   region_name=self.spec.region, temperature=temperature,
                                   max_tokens=max_tokens, prompt_cache=prompt_cache_on(model))

    # ---- Embeddings ----
    def embeddings(self, openai=None):
        """The embeddings of this workspace's index (and anything compared with it)."""
        if not self.bedrock:
            return (openai or _openai_embeddings())(model=OPENAI_EMBED_MODEL)
        return _cls("BedrockEmbed")(model_id=self.spec.embed_model,
                                    client=runtime_client(self.spec),
                                    region_name=self.spec.region,
                                    dimensions=self.spec.embed_dims, normalize=True)

    def query_embeddings(self, openai=None):
        """The embeddings to search the workspace's index with, or None when it was
        built by ANOTHER provider (switching is rebuilding it): the search is then
        by keywords only until the rebuild is done, rather than send the question
        to a provider the workspace has left."""
        built = self.spec.index_embeddings
        if built is not None and _provider_of(built) != self.provider:
            return None
        if self.bedrock and built and built != self.embed_name:
            # Same provider, other model: the index's own model until it's rebuilt.
            _, model, dims = built.split(":", 2)
            return replace_models(self, embed_model=model,
                                  embed_dims=int(dims) if dims.isdigit() else None
                                  ).embeddings(openai)
        return self.embeddings(openai)

    # ---- What else may see the workspace's text ----
    def allows_reranker(self, reranker):
        """Whether the search may send candidates to `reranker` (rerankers.py): on
        Bedrock, only one that keeps them in-house (local, or Bedrock's own)."""
        if reranker is None or not self.bedrock:
            return True
        return bool(getattr(reranker, "private", False))


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
    return "bedrock" if (embed_name or "").startswith("bedrock:") else "openai"


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
    """An AWS error's code and message, without anything secret in it."""
    response = getattr(e, "response", None) or {}
    err = response.get("Error") or {}
    if err:
        return f"{err.get('Code', 'Error')}: {err.get('Message', '')}".strip()
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
    """BedrockChat / BedrockEmbed, made on first use: importing this file never
    needs langchain-aws or boto3 (an OpenAI-only server)."""
    if name not in _made:
        _made[name] = (_make_bedrock_chat if name == "BedrockChat" else _make_bedrock_embed)()
    return _made[name]


def __getattr__(name):
    if name in ("BedrockChat", "BedrockEmbed"):
        return _cls(name)
    raise AttributeError(name)


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


def permissions_policy(spec):
    """The IAM policy the workspace's role (or user) needs, for its models."""
    region = spec.region or "REGION"
    chat = sorted({a for m in {spec.chat_model, spec.fast_model} if m
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


TEST_PROMPT = "Reply with the single word OK."


def test_connection(spec):
    """One tiny Converse call to the chat model and one embedding: what Test
    connection on the Assistant screen shows. Raises ProviderError with AWS's reason."""
    from langchain_core.messages import HumanMessage
    on = Models(spec)
    try:
        reply = on.chat("answer", temperature=0, max_tokens=10, timeout=30,
                        max_retries=0).invoke([HumanMessage(content=TEST_PROMPT)])
        vector = on.embeddings().embed_query("connection test")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(_aws_error(e)) from e
    usage = getattr(reply, "usage_metadata", None) or {}
    return {"chat_model": spec.chat_model, "reply": text_of(reply.content).strip()[:40],
            "tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
            "embed_model": spec.embed_model, "dimensions": len(vector)}
