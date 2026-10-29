"""
url_guard.py  —  the server only calls a customer's sign-in provider on the
public internet.

A workspace admin types in their provider's address, and the server then fetches
from it (its settings, its signing keys, the sign-in token). Without a check, an
admin could point it at something only the server can reach — a cloud metadata
service (169.254.169.254), a database on localhost, the office network — and
read the reply in an error message. So every such address must be https, and
its host must resolve only to public IP addresses.

For local testing against a provider on this machine (e.g. Keycloak in Docker),
list it in .env:
    SSO_ALLOW_PRIVATE_HOSTS=localhost:8180
Listed host:port pairs may also use plain http.

Connectors whose address an admin types in (an Outline server, a website to
crawl) are checked the same way with check_connector(), against their OWN
list, CONNECTOR_ALLOW_PRIVATE_HOSTS (for a test site on this machine), so
opening sign-in to a local IdP never opens the crawler to localhost.

A model provider whose address a workspace admin types in (Azure OpenAI's
endpoint, an OpenAI-compatible server's base URL: models.py) is checked with
check_model_endpoint(), against MODEL_ALLOW_PRIVATE_HOSTS (a vLLM or Ollama on
this machine or its network), on save and on every request (model_http()).

The check runs when the settings are saved and again on every request
(an httpx event hook), since a host's DNS can change after it was saved.
httpx looks the host up again to connect, so a DNS server that answers
differently a moment later (DNS rebinding) isn't caught; pinning the checked
address would close that.
"""

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit


# IPv6 prefixes that carry an IPv4 address (NAT64): judge them as never public.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


class UnsafeURL(ValueError):
    """The address isn't one the server may call. The message says why."""


SSO_ALLOW = "SSO_ALLOW_PRIVATE_HOSTS"
CONNECTOR_ALLOW = "CONNECTOR_ALLOW_PRIVATE_HOSTS"
MODEL_ALLOW = "MODEL_ALLOW_PRIVATE_HOSTS"
# Who is refused a private address, as the message says it.
_WHO = {SSO_ALLOW: "sign-in", CONNECTOR_ALLOW: "connectors", MODEL_ALLOW: "model providers"}


def _allowed_private(env=SSO_ALLOW):
    return {h.strip().lower() for h in os.getenv(env, "").split(",") if h.strip()}


def _parse(url, env=SSO_ALLOW):
    """(host, port, allowed_private) for a URL, or UnsafeURL."""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UnsafeURL("That isn't a valid web address.") from None
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        raise UnsafeURL("That isn't a valid web address.")
    allowed = f"{host}:{port}" in _allowed_private(env)
    if parts.scheme != "https" and not (allowed and parts.scheme == "http"):
        raise UnsafeURL("The address must start with https://.")
    return host, port, allowed


def _check_addresses(host, infos, who="sign-in"):
    if not infos:
        raise UnsafeURL(f"{host} can't be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or any(ip in net for net in _NAT64):
            raise UnsafeURL(f"{host} points to a private network address, "
                            f"which {who} can't use.")


def require_https(url):
    """For addresses only the BROWSER visits (an IdP's sign-in page): https, or a
    listed local host. Raises UnsafeURL."""
    _parse(url)


def _resolve(host, port):
    try:
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeURL(f"{host} can't be found.") from None


def check(url, allow_env=SSO_ALLOW):
    """Raise UnsafeURL unless the server may call `url`."""
    host, port, allowed = _parse(url, allow_env)
    if not allowed:
        _check_addresses(host, _resolve(host, port), _WHO.get(allow_env, "sign-in"))


def check_connector(url):
    """check() for an address a connector calls (CONNECTOR_ALLOW_PRIVATE_HOSTS)."""
    check(url, CONNECTOR_ALLOW)


def check_model_endpoint(url):
    """check() for a model provider's address an admin typed (MODEL_ALLOW_PRIVATE_HOSTS)."""
    check(url, MODEL_ALLOW)


async def check_async(url, allow_env=SSO_ALLOW):
    host, port, allowed = _parse(url, allow_env)
    if not allowed:
        infos = await asyncio.get_running_loop().run_in_executor(None, _resolve, host, port)
        _check_addresses(host, infos, _WHO.get(allow_env, "sign-in"))


async def httpx_hook(request):
    """httpx `event_hooks={"request": [httpx_hook]}`: refuse unsafe requests."""
    await check_async(str(request.url))


def httpx_hook_sync(request):
    check(str(request.url))


def model_http():
    """(httpx.Client, httpx.AsyncClient) for a model provider at an address an admin
    typed: every request is checked first (check_model_endpoint), so a host whose DNS
    moves to a private address after it was saved is refused too. Made once and
    shared: the check is per request, not per client."""
    global _model_http
    if _model_http is None:
        import httpx

        async def hook_async(request):
            await check_async(str(request.url), MODEL_ALLOW)

        def hook(request):
            check_model_endpoint(str(request.url))

        _model_http = (httpx.Client(event_hooks={"request": [hook]}),
                       httpx.AsyncClient(event_hooks={"request": [hook_async]}))
    return _model_http


_model_http = None
