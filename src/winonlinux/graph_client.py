"""Shared async Microsoft Graph call wrapper (add-cloudpc-enumeration change, tasks.md 1.1/1.2).

This module owns exactly one thing every outbound Graph call in this change funnels through:
:func:`graph_get_json`. It composes three concerns that spec.md requires every such call to get
right, so no caller has to reimplement them:

- **Silent token acquisition first, every attempt** (FR-4-AC-1). ``auth_manager`` (a real
  :class:`winonlinux.auth_manager.AuthManager`, or any stand-in exposing the same
  ``acquire_token_silently`` coroutine method in tests) is asked for a fresh token before *every*
  HTTP attempt in a retry sequence, not once up front and reused -- a token can expire mid-backoff
  on a slow retry sequence, and re-asking is exactly what "silent" acquisition is for (it returns a
  cached, still-valid token with zero network cost when nothing has expired; see
  ``AuthManager.acquire_token_silently``'s own docstring). Any exception
  ``acquire_token_silently`` raises (one of ``AuthManager``'s ``AuthError`` subclasses) propagates
  completely unmodified -- this module never catches, wraps, or reclassifies it.
- **`429` handling with no user-visible error on the first throttle** (spec.md §9). A `Retry-After`
  header is honored when present (integer-seconds form only -- see the note below); otherwise
  exponential backoff starting at 1s, doubling, capped at 30s. Only once ``max_retries`` throttled
  attempts are exhausted does this surface as :class:`GraphThrottled`.
- **Proxy failures reported distinctly from plain network failures** (spec.md §5.9). This module
  does not configure proxying itself -- ``requests`` already honors ``HTTP_PROXY``/``HTTPS_PROXY``/
  ``NO_PROXY`` and desktop proxy settings by default (``trust_env=True`` is the default for a bare
  ``requests.get`` call, and this module makes no ``Session`` that could override it) -- its job is
  only to classify ``requests.exceptions.ProxyError`` into :class:`GraphProxyError`, distinctly
  from :class:`GraphNetworkError` for every other connection-level failure.

ASSUMED, needing verification (per this repo's "verify, don't assert" discipline, CLAUDE.md):
``Retry-After`` arriving in the HTTP-date form (e.g. ``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``)
rather than plain integer seconds is treated as an unhandled edge case -- this module falls back to
exponential backoff when the header's value does not parse as an integer. Graph's own documented
behavior for `429` responses uses the integer-seconds form, so the HTTP-date form is assumed
uncommon in practice for this endpoint, but that has not been verified against a real throttled
response.

Logging discipline (spec.md §10.7): the ``Authorization`` header value (and therefore the access
token) is held only in a local variable inside :func:`graph_get_json`'s attempt loop, is never
passed to a logging call, and every log line here carries only the URL, HTTP status code (when
one exists), exception type name, and retry/attempt counters -- never a header, and never a raw
response body (Graph error bodies typically do not echo request headers back, but this module is
conservative and does not log response body content at all, even on error, precisely because "not
secret in the common case" is not the same guarantee as "never secret").
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import requests

from winonlinux.task_registry import run_blocking

__all__ = [
    "GraphError",
    "GraphNotFound",
    "GraphConsentRequired",
    "GraphProxyError",
    "GraphNetworkError",
    "GraphThrottled",
    "graph_get_json",
]

logger = logging.getLogger(__name__)

#: Connect+read timeout passed to every ``requests.get`` call so a hung connection cannot block a
#: retry cycle (or the caller) forever.
_REQUEST_TIMEOUT_SECONDS = 30

#: Exponential-backoff parameters used when a `429` response carries no (parseable) `Retry-After`
#: header (spec.md §9).
_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0


class GraphError(Exception):
    """Base class for every error this module raises for a failed Graph call.

    ``response_body`` is whatever text/JSON content the response carried -- Graph error bodies are
    not secret, but this is never populated with anything from the *request* (headers included).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GraphNotFound(GraphError):
    """Raised specifically for HTTP 404 (spec.md: e.g. `/me/cloudPCs` on an unlicensed account)."""


class GraphConsentRequired(GraphError):
    """Raised specifically for HTTP 403 -- the distinct unconsented-tenant handling closing G-42.

    ``admin_consent_url`` is left ``None`` unless a principled URL can be composed from what a
    plain Graph GET response actually carries. A bare Graph `403` response body does not reliably
    carry enough information (tenant ID, client ID, redirect URI) to compose a correct admin-consent
    URL the way an interactive MSAL error result does (see
    ``winonlinux.auth_errors.classify_msal_error``, which has a live ``redirect_uri`` available) --
    so this module deliberately does not attempt to fabricate one here. A caller that needs a real
    admin-consent URL should route the user through the interactive consent flow
    (``AuthManager.add_account`` / ``reauth_from_banner``), which does have what it takes to build
    one correctly, rather than trust a guess made from a GET 403 alone.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any = None,
        admin_consent_url: str | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.admin_consent_url = admin_consent_url


class GraphProxyError(GraphError):
    """Raised when the underlying failure is specifically a proxy connection failure
    (``requests.exceptions.ProxyError``), so callers can report "the proxy rejected this"
    distinctly from "the network is unreachable" (spec.md §5.9)."""


class GraphNetworkError(GraphError):
    """Raised for any other connection-level failure (``requests.exceptions.ConnectionError``,
    ``requests.exceptions.Timeout``, or any other ``requests.exceptions.RequestException`` not
    already classified above) that is not proxy-specific."""


class GraphThrottled(GraphError):
    """Raised only once `429` responses persist past the retry budget -- a single or a few `429`s
    that succeed on retry within budget never raise this or surface to the caller as an error at
    all (spec.md §9: "no user-visible error on the first throttle")."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header value as plain integer seconds.

    Returns ``None`` when the header is absent OR does not parse as an integer -- callers fall back
    to exponential backoff in either case. The HTTP-date form (RFC 7231) is a documented alternative
    but is NOT handled here; see this module's docstring for why that is an accepted, ASSUMED-rare
    gap rather than an oversight.
    """
    if value is None:
        return None
    try:
        return float(int(value.strip()))
    except (TypeError, ValueError):
        return None


def _next_backoff_seconds(previous: float | None) -> float:
    """Exponential backoff starting at :data:`_BACKOFF_INITIAL_SECONDS`, doubling each attempt,
    capped at :data:`_BACKOFF_CAP_SECONDS` (spec.md §9, used when `Retry-After` is absent/unparseable)."""
    if previous is None:
        return _BACKOFF_INITIAL_SECONDS
    return min(previous * 2, _BACKOFF_CAP_SECONDS)


async def graph_get_json(
    url: str,
    *,
    auth_manager: Any,
    home_account_id: str,
    scopes: list[str],
    max_retries: int = 5,
) -> dict:
    """GET ``url`` from Microsoft Graph, returning the parsed JSON body.

    Acquires a fresh token via ``auth_manager.acquire_token_silently(home_account_id, scopes)``
    before every attempt (FR-4-AC-1) -- see the module docstring for the full contract, retry, and
    error-classification behavior.
    """
    backoff_seconds: float | None = None
    last_retry_after_seen: float | None = None
    attempt = 0

    while True:
        # Fresh every attempt, deliberately not hoisted above the loop or cached across retries --
        # a token can expire mid-backoff on a slow retry sequence (FR-4-AC-1). Any AuthError
        # subclass raised here propagates completely unmodified.
        access_token = await auth_manager.acquire_token_silently(home_account_id, scopes)
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = await run_blocking(
                requests.get, url, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS
            )
        except requests.exceptions.ProxyError as exc:
            logger.warning(
                "graph_get_json: proxy rejected request to %s (%s)", url, type(exc).__name__
            )
            raise GraphProxyError(f"proxy error contacting {url}") from exc
        except requests.exceptions.RequestException as exc:
            # Covers ConnectionError, Timeout, and anything else requests raises that is not the
            # ProxyError subclass handled above.
            logger.warning(
                "graph_get_json: network error contacting %s (%s)", url, type(exc).__name__
            )
            raise GraphNetworkError(f"network error contacting {url}") from exc
        finally:
            # `headers` (and therefore the bearer token) never appears in a log call above or
            # below -- only the URL, status code, exception type, and retry counters do.
            del headers

        status = response.status_code

        if status == 200:
            return response.json()

        if status == 404:
            raise GraphNotFound(
                f"Graph resource not found: {url}", status_code=status, response_body=_summarize_body(response)
            )

        if status == 403:
            raise GraphConsentRequired(
                f"Graph consent required for: {url}",
                status_code=status,
                response_body=_summarize_body(response),
                admin_consent_url=None,
            )

        if status == 429:
            attempt += 1
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            if retry_after is not None:
                last_retry_after_seen = retry_after
                sleep_seconds = retry_after
            else:
                backoff_seconds = _next_backoff_seconds(backoff_seconds)
                sleep_seconds = backoff_seconds

            if attempt > max_retries:
                logger.warning(
                    "graph_get_json: %s throttled past max_retries=%d, giving up",
                    url,
                    max_retries,
                )
                raise GraphThrottled(
                    f"Graph throttled {url} past the retry budget",
                    status_code=status,
                    response_body=_summarize_body(response),
                    retry_after_seconds=last_retry_after_seen,
                )

            logger.info(
                "graph_get_json: %s throttled (attempt %d/%d), sleeping %.1fs before retry",
                url,
                attempt,
                max_retries,
                sleep_seconds,
            )
            # asyncio.sleep, never time.sleep -- this must not block the event loop (D-18).
            await asyncio.sleep(sleep_seconds)
            continue

        raise GraphError(
            f"Graph request to {url} failed with status {status}",
            status_code=status,
            response_body=_summarize_body(response),
        )


def _summarize_body(response: "requests.Response") -> Any:
    """Best-effort, conservative summary of a non-2xx response body for :attr:`GraphError.response_body`.

    Prefers the parsed JSON error object Graph normally returns (structured, and known not to echo
    request headers). Falls back to a length-truncated text snippet if the body is not JSON, rather
    than the full raw text, since this module does not have a principled guarantee that an
    arbitrary non-JSON body could never contain something request-derived.
    """
    try:
        return response.json()
    except ValueError:
        text = response.text or ""
        return text[:500]
