"""Unit tests for winonlinux.graph_client (add-cloudpc-enumeration change, tasks.md 1.1/1.2, 5.4).

Pure asyncio -- driven with plain ``asyncio.run``, matching tests/test_freerdp_probe.py,
tests/test_auth_loopback.py, and tests/test_auth_manager.py's convention rather than a
pytest-asyncio plugin. No real HTTP call and no real ``AuthManager`` is ever constructed: every
test monkeypatches ``requests.get`` with a fake, and supplies a minimal fake auth-manager stub
exposing only the ``acquire_token_silently`` coroutine method the contract requires
(``winonlinux.graph_client.graph_get_json`` never touches anything else on it).

``asyncio.sleep`` is monkeypatched in every throttling test so these tests complete instantly and
assert the wrapper's *intent* to wait (the recorded argument) rather than real wall-clock time.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
import requests

from winonlinux.graph_client import (
    GraphConsentRequired,
    GraphError,
    GraphNetworkError,
    GraphNotFound,
    GraphProxyError,
    GraphThrottled,
    graph_get_json,
)

pytestmark = pytest.mark.unit

FAKE_TOKEN = "fake-graph-access-token-super-secret-value-should-never-be-logged"
FAKE_URL = "https://graph.microsoft.com/v1.0/me/cloudPCs"
FAKE_HOME_ACCOUNT_ID = "acct-graph-client-test"
FAKE_SCOPES = ["CloudPC.Read.All"]


# --- test doubles --------------------------------------------------------------------------------


class FakeAuthManager:
    """Minimal stand-in exposing only ``acquire_token_silently`` -- the one method
    ``graph_get_json`` calls on its ``auth_manager`` argument."""

    def __init__(self, *, token: str = FAKE_TOKEN, error_factory=None) -> None:
        self.token = token
        self.error_factory = error_factory  # Callable[[], BaseException] | None
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def acquire_token_silently(self, home_account_id: str, scopes: list[str]) -> str:
        self.calls.append((home_account_id, tuple(scopes)))
        if self.error_factory is not None:
            raise self.error_factory()
        return self.token

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` -- only the attributes/methods
    ``graph_get_json`` reads are implemented."""

    def __init__(
        self,
        status_code: int,
        *,
        json_body: dict | None = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body on this fake response")
        return self._json_body


def _patch_sleep(monkeypatch) -> list[float]:
    """Replace asyncio.sleep with an instant no-op that records the requested duration."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


# --- success / status-code classification ---------------------------------------------------------


def test_graph_get_json_success_returns_parsed_json(monkeypatch):
    expected = {"value": [{"id": "cpc-1"}]}

    def fake_get(url, headers=None, timeout=None):
        assert url == FAKE_URL
        assert headers == {"Authorization": f"Bearer {FAKE_TOKEN}"}
        assert timeout is not None
        return FakeResponse(200, json_body=expected)

    monkeypatch.setattr(requests, "get", fake_get)
    auth_manager = FakeAuthManager()

    async def scenario():
        return await graph_get_json(
            FAKE_URL,
            auth_manager=auth_manager,
            home_account_id=FAKE_HOME_ACCOUNT_ID,
            scopes=FAKE_SCOPES,
        )

    result = asyncio.run(scenario())
    assert result == expected
    assert auth_manager.call_count == 1


def test_graph_get_json_404_raises_graph_not_found(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(404, json_body={"error": "not found"}))
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphNotFound) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.status_code == 404


def test_graph_get_json_403_raises_graph_consent_required(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse(403, json_body={"error": "consent_required"})
    )
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphConsentRequired) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.status_code == 403
    # admin_consent_url cannot be principled composed from a bare GET 403 -- documented as None.
    assert excinfo.value.admin_consent_url is None


def test_graph_get_json_other_status_raises_graph_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(500, text="internal error"))
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphError) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.status_code == 500


# --- throttling (spec.md section 9) ---------------------------------------------------------------


def test_graph_get_json_429_with_retry_after_sleeps_then_succeeds(monkeypatch):
    """A single 429 with Retry-After must be retried transparently -- no exception surfaces to the
    caller, matching spec.md's "no user-visible error on the first throttle"."""
    recorded_sleeps = _patch_sleep(monkeypatch)
    expected = {"value": []}
    call_sequence = [
        FakeResponse(429, headers={"Retry-After": "2"}, text="throttled"),
        FakeResponse(200, json_body=expected),
    ]
    calls: list[int] = []

    def fake_get(*a, **k):
        calls.append(1)
        return call_sequence[len(calls) - 1]

    monkeypatch.setattr(requests, "get", fake_get)
    auth_manager = FakeAuthManager()

    async def scenario():
        return await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    result = asyncio.run(scenario())

    assert result == expected
    assert len(calls) == 2
    # Slept for at least the Retry-After value given (2 seconds).
    assert len(recorded_sleeps) == 1
    assert recorded_sleeps[0] >= 2
    # acquire_token_silently is called before EVERY attempt, not just the first.
    assert auth_manager.call_count == 2


def test_graph_get_json_429_persisting_past_max_retries_raises_graph_throttled(monkeypatch):
    recorded_sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse(429, headers={}, text="throttled")
    )
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL,
            auth_manager=auth_manager,
            home_account_id=FAKE_HOME_ACCOUNT_ID,
            scopes=FAKE_SCOPES,
            max_retries=3,
        )

    with pytest.raises(GraphThrottled) as excinfo:
        asyncio.run(scenario())

    assert excinfo.value.status_code == 429
    # No Retry-After header was ever present in this scenario -- exponential backoff path.
    assert excinfo.value.retry_after_seconds is None
    # 1 initial attempt + 3 retries = 4 total HTTP attempts, and a fresh token before each.
    assert auth_manager.call_count == 4
    assert len(recorded_sleeps) == 3


# --- network vs proxy classification (spec.md section 5.9) -----------------------------------------


def test_graph_get_json_proxy_error_becomes_graph_proxy_error(monkeypatch):
    def fake_get(*a, **k):
        raise requests.exceptions.ProxyError("proxy refused connection")

    monkeypatch.setattr(requests, "get", fake_get)
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphProxyError):
        asyncio.run(scenario())


def test_graph_get_json_connection_error_becomes_graph_network_error(monkeypatch):
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", fake_get)
    auth_manager = FakeAuthManager()

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphNetworkError):
        asyncio.run(scenario())


def test_proxy_error_and_connection_error_are_distinct_exception_types(monkeypatch):
    """Explicit proof these two are NOT the same exception type (spec.md section 5.9)."""
    assert not issubclass(GraphProxyError, GraphNetworkError)
    assert not issubclass(GraphNetworkError, GraphProxyError)

    def raise_proxy(*a, **k):
        raise requests.exceptions.ProxyError("proxy refused")

    def raise_connection(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    auth_manager = FakeAuthManager()

    async def scenario(get_fn):
        monkeypatch.setattr(requests, "get", get_fn)
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(GraphProxyError):
        asyncio.run(scenario(raise_proxy))
    with pytest.raises(GraphNetworkError):
        asyncio.run(scenario(raise_connection))


# --- token acquisition semantics -------------------------------------------------------------------


def test_auth_manager_errors_propagate_unmodified(monkeypatch):
    """Whatever acquire_token_silently raises must propagate completely unmodified -- graph_client
    never catches/reclassifies an AuthError subclass."""

    class _FakeReauthRequiredError(Exception):
        pass

    def boom():
        raise _FakeReauthRequiredError("reauth required")

    auth_manager = FakeAuthManager(error_factory=boom)
    # requests.get should never even be reached in this scenario.
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")))

    async def scenario():
        await graph_get_json(
            FAKE_URL, auth_manager=auth_manager, home_account_id=FAKE_HOME_ACCOUNT_ID, scopes=FAKE_SCOPES
        )

    with pytest.raises(_FakeReauthRequiredError):
        asyncio.run(scenario())


# --- log redaction discipline (spec.md section 10.7) ------------------------------------------------


def test_authorization_header_value_never_logged(monkeypatch, caplog):
    _patch_sleep(monkeypatch)
    call_sequence = [
        FakeResponse(429, headers={"Retry-After": "1"}, text="throttled"),
        FakeResponse(200, json_body={"value": []}),
    ]
    calls: list[int] = []

    def sequenced_get(*a, **k):
        calls.append(1)
        return call_sequence[len(calls) - 1]

    monkeypatch.setattr(requests, "get", sequenced_get)
    auth_manager = FakeAuthManager()

    async def scenario():
        with caplog.at_level(logging.DEBUG):
            return await graph_get_json(
                FAKE_URL,
                auth_manager=auth_manager,
                home_account_id=FAKE_HOME_ACCOUNT_ID,
                scopes=FAKE_SCOPES,
            )

    result = asyncio.run(scenario())
    assert result == {"value": []}

    # Checked as a boolean, not `assert FAKE_TOKEN not in caplog.text` directly: pytest's
    # assertion-rewriting prints both operands of a failed `in`/`not in` comparison, so a real
    # redaction regression would otherwise leak the very secret this test exists to catch straight
    # into the test/CI failure output (mirrors tests/test_auth_manager.py's own convention).
    token_leaked = FAKE_TOKEN in caplog.text
    assert not token_leaked, "the bearer token value leaked into the log output"
