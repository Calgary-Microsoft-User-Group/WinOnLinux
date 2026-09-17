"""Hardened OAuth 2.0 loopback redirect listener (spec.md section 6.1/section 10.3, D-6).

Interactive sign-in (auth_manager.py, a parallel task) drives MSAL's authorization-code-with-PKCE
flow through the system browser. The browser's final hop is a redirect to
``http://127.0.0.1:<ephemeral-port>/?code=...&state=...`` (or an ``error``/``error_description``
pair on a denial) that this module -- not MSAL's own bundled server -- catches, because the
contract callers integrate against needs an explicit two-phase start/wait API: the port must be
known *before* the redirect_uri is built and handed to MSAL's ``initiate_auth_code_flow``, and the
wait must be bounded and single-response per spec.md's "hardened loopback redirect" requirement.

Why not MSAL's own loopback helper: it does not expose a "give me the port now, then let me wait
later" split, and this module needs full control over state-validation and single-response
semantics to satisfy the "Loopback listener rejects a second or forged response" scenario exactly.

Security properties (§10.3, D-6), enforced here and only here:
  * Binds ``127.0.0.1`` exclusively -- never ``0.0.0.0`` or any other interface. A redirect
    listener reachable from the network would let any other host on the LAN race the real browser
    for the authorization code.
  * Binds port 0 (kernel-assigned ephemeral port) -- never a fixed, predictable port.
  * Validates the ``state`` query parameter against the exact value the caller is waiting for.
    Anything else (wrong state, missing state, a stray probe) is rejected with a plain response
    and does *not* count as "the" response -- the listener keeps waiting for the real one within
    the original deadline.
  * Accepts exactly one matching response. Every connection after that -- even one that would have
    matched -- gets a plain "already used" response and its contents are never parsed into a
    result. This is what makes a second, forged, or replayed hit against an already-satisfied
    listener inert.
  * The wait has a hard deadline measured from ``start()``, not from when ``wait_for_redirect()``
    happens to be called, so a caller that does other setup work between the two calls does not
    silently get more than the configured timeout.

Nothing sensitive is logged here: the authorization ``code`` value itself is never logged, only
coarse state-machine facts (a request arrived, whether its ``state`` matched, the listener timed
out). Per CLAUDE.md's log-redaction discipline, an auth code is exactly the kind of value that must
never appear in a log line, so this module simply never passes one to a logging call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

__all__ = ["LoopbackTimeout", "LoopbackResult", "LoopbackListener"]

_logger = logging.getLogger(__name__)

# Generous upper bound on how many bytes of a request we will read before giving up on parsing
# it. A real browser's GET redirect request line + headers is a few hundred bytes; this floor
# guards against a misbehaving or hostile connection trying to make us buffer indefinitely.
_MAX_REQUEST_BYTES = 16 * 1024

_OK_BODY = b"You may close this window and return to WinOnLinux."
_RESPONSE_OK = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: " + str(len(_OK_BODY)).encode("ascii") + b"\r\n"
    b"\r\n" + _OK_BODY
)

_INVALID_BODY = b"Invalid request. Please try signing in again."
_INVALID_RESPONSE = (
    b"HTTP/1.1 400 Bad Request\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: " + str(len(_INVALID_BODY)).encode("ascii") + b"\r\n"
    b"\r\n" + _INVALID_BODY
)

_ALREADY_USED_BODY = b"This sign-in link has already been used. You may close this window."
_ALREADY_USED_RESPONSE = (
    b"HTTP/1.1 410 Gone\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: " + str(len(_ALREADY_USED_BODY)).encode("ascii") + b"\r\n"
    b"\r\n" + _ALREADY_USED_BODY
)


class LoopbackTimeout(Exception):
    """Raised by :meth:`LoopbackListener.wait_for_redirect` when no matching redirect arrived
    before the deadline (measured from :meth:`LoopbackListener.start`)."""


@dataclass(frozen=True)
class LoopbackResult:
    """The query parameters of the one redirect request the listener accepted.

    ``query_params`` holds e.g. ``{"code": ..., "state": ..., "session_state": ...}`` on a
    successful auth-code redirect, or ``{"error": ..., "error_description": ..., "state": ...}``
    on an auth-server-side denial that still completed the redirect (e.g. the user declined
    consent) -- both shapes reach the listener the same way, as ordinary query parameters on the
    one GET request whose ``state`` matched.
    """

    query_params: dict[str, str]


@dataclass
class LoopbackListener:
    """A single-use, single-account OAuth loopback redirect catcher (D-6).

    Usage (matching the two-phase contract every caller of this module codes against)::

        listener = LoopbackListener(timeout_seconds=300.0)
        try:
            port = await listener.start()
            redirect_uri = f"http://127.0.0.1:{port}"
            flow = initiate_auth_code_flow(..., redirect_uri=redirect_uri)  # msal
            await listener.arm(flow["state"])  # before opening the browser -- see arm()'s docstring
            webbrowser.open(flow["auth_uri"])
            result = await listener.wait_for_redirect(flow["state"])
        finally:
            await listener.close()

    Not reusable across sign-in attempts: construct a fresh instance per attempt.
    """

    # Keyword-only per the shared contract's `LoopbackListener(*, timeout_seconds: float = 300.0)`
    # signature (kw_only requires Python 3.10+; this project targets >=3.11).
    timeout_seconds: float = field(default=300.0, kw_only=True)

    _server: asyncio.Server | None = field(default=None, init=False, repr=False)
    _deadline: float | None = field(default=None, init=False, repr=False)
    _accepted_event: asyncio.Event | None = field(default=None, init=False, repr=False)
    _accepted_result: LoopbackResult | None = field(default=None, init=False, repr=False)
    _expected_state: str | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    # Guards the accept-vs-reject decision in _handle_connection so two genuinely concurrent
    # connections can never both observe "not yet accepted" and both write to _accepted_result --
    # see arm()/_handle_connection for why a plain is_set() check on its own is not enough.
    _accept_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)

    async def start(self) -> int:
        """Bind ``127.0.0.1`` on an ephemeral port and start the deadline clock.

        Returns the bound port immediately -- does not wait for any connection. Callers need the
        port before they can build the ``redirect_uri`` MSAL's ``initiate_auth_code_flow`` expects,
        which must exist before the browser is opened, which is in turn before any redirect could
        possibly arrive -- so this method must not block on incoming traffic.
        """

        if self._server is not None:
            raise RuntimeError("LoopbackListener.start() called more than once")

        self._accepted_event = asyncio.Event()
        self._accept_lock = asyncio.Lock()
        # D-6 / §10.3: loopback-only, ephemeral port. Never 0.0.0.0, never a fixed port. `limit`
        # caps each connection's internal read buffer at _MAX_REQUEST_BYTES so a misbehaving or
        # hostile connection can't make us buffer an unbounded request before we give up on it.
        self._server = await asyncio.start_server(
            self._handle_connection, host="127.0.0.1", port=0, limit=_MAX_REQUEST_BYTES
        )
        self._deadline = time.monotonic() + self.timeout_seconds

        sockets = self._server.sockets or ()
        if not sockets:  # pragma: no cover - defensive; asyncio always gives us a bound socket
            raise RuntimeError("loopback listener failed to bind a socket")
        port = sockets[0].getsockname()[1]
        _logger.debug("loopback listener bound on 127.0.0.1:%d", port)
        return port

    async def arm(self, expected_state: str) -> None:
        """Set the expected ``state`` value as early as possible.

        Call this immediately after ``initiate_auth_code_flow()`` returns its ``state`` -- and
        before opening the browser -- not just as the first line of :meth:`wait_for_redirect`.
        The server (from :meth:`start`) is already accepting connections at that point, so a
        legitimate, unusually fast redirect (e.g. Entra seamless SSO / PRT silent auth, which can
        complete in well under a second) could otherwise arrive and be rejected as "non-matching"
        during the window between ``start()`` and whenever the caller gets around to calling
        :meth:`wait_for_redirect` -- ``initiate_auth_code_flow`` and ``webbrowser.open`` are both
        real await-suspension points that leave that window open. Safe to call before ``start()``
        has produced a request to check against; not safe to call before :meth:`start` itself.
        """
        if self._server is None:
            raise RuntimeError("arm() called before start()")
        self._expected_state = expected_state

    async def wait_for_redirect(self, expected_state: str) -> LoopbackResult:
        """Wait for the one redirect request whose ``state`` equals ``expected_state``.

        Prefer calling :meth:`arm` with this same value as early as possible (see its docstring)
        rather than relying solely on this method to set it -- this parameter is kept so existing
        callers and tests that call this method directly still work, and re-arming to the same
        value here is harmless, but arming only here reopens the timing window :meth:`arm` exists
        to close.

        The deadline is counted from :meth:`start`, not from this call. Any request with a
        missing or mismatched ``state`` is rejected inline by the connection handler and does not
        affect this wait -- the listener simply keeps waiting for the real one until the deadline.

        Documented behavior for a second call after one redirect has already been accepted:
        this method is idempotent, not re-armed. Every connection that arrives after acceptance
        is turned away by the connection handler ("already used") and never parsed into a new
        result, so a second call simply returns the same already-accepted :class:`LoopbackResult`
        immediately -- it neither hangs waiting for a second (impossible) response nor manufactures
        one from a connection that was in fact rejected. Callers in this codebase call it exactly
        once per listener instance; this is intentionally the more forgiving of the two reasonable
        options (raising ``RuntimeError`` was the alternative) since replaying the one legitimate
        result is harmless and a caller should never need to.
        """

        if self._server is None or self._deadline is None or self._accepted_event is None:
            raise RuntimeError("wait_for_redirect() called before start()")

        self._expected_state = expected_state

        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise LoopbackTimeout("loopback listener deadline already passed")

        try:
            await asyncio.wait_for(self._accepted_event.wait(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise LoopbackTimeout(
                f"no matching redirect within {self.timeout_seconds:.1f}s"
            ) from exc

        assert self._accepted_result is not None  # the event is only ever set alongside this
        return self._accepted_result

    async def close(self) -> None:
        """Stop accepting connections. Idempotent; safe even if :meth:`start` was never called."""

        if self._closed:
            return
        self._closed = True
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover - best-effort shutdown
                pass
        _logger.debug("loopback listener closed")

    # -- connection handling ----------------------------------------------------------------

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle exactly one inbound TCP connection: parse it just enough to pull the query
        string off the GET request line, decide accept/reject/already-used, respond, close.

        Never raises out of this callback -- an asyncio server callback that raises would only
        log an "Exception in callback" warning and otherwise be silently swallowed, so failures
        here are handled defensively and the connection is always closed.
        """

        try:
            query_params = await self._read_query_params(reader)
        except Exception:
            _logger.debug("loopback listener: malformed request, ignoring", exc_info=True)
            await self._respond(writer, _INVALID_RESPONSE)
            return

        try:
            # The accept-vs-reject decision and the write to _accepted_result/_accepted_event
            # must be atomic with respect to OTHER connection handlers, not just internally
            # consistent -- asyncio.start_server dispatches each connection as its own task, so
            # two genuinely concurrent connections (a replay, or simply two near-simultaneous
            # requests) could otherwise both observe "not yet accepted" before either one commits,
            # and both proceed to treat themselves as the accepted response. Holding this lock for
            # the whole decide-and-commit sequence -- not just the is_set() check -- is what makes
            # "exactly one accepted response" true under concurrency, not just in the common case.
            async with self._accept_lock:
                # Once a valid response has been accepted, permanently stop accepting new content
                # -- every subsequent connection gets "already used" and is never parsed further
                # into a second LoopbackResult, regardless of how convincing its state looks.
                if self._accepted_event is not None and self._accepted_event.is_set():
                    _logger.debug(
                        "loopback listener: rejecting connection after one already accepted"
                    )
                    await self._respond(writer, _ALREADY_USED_RESPONSE)
                    return

                state = query_params.get("state")
                if self._expected_state is None or state != self._expected_state:
                    _logger.debug("loopback listener: rejecting request with non-matching state")
                    await self._respond(writer, _INVALID_RESPONSE)
                    return

                # Commit BEFORE the network write (await self._respond below): the lock keeps
                # other handlers out while we hold it, but _respond's own await would otherwise
                # let another handler run before this one's result/event were actually recorded.
                self._accepted_result = LoopbackResult(query_params=query_params)
                if self._accepted_event is not None:
                    self._accepted_event.set()

            await self._respond(writer, _RESPONSE_OK)
            _logger.debug("loopback listener: accepted matching redirect")
        except Exception:  # pragma: no cover - defensive; never let the server task die
            _logger.debug("loopback listener: error handling connection", exc_info=True)

    @staticmethod
    async def _read_query_params(reader: asyncio.StreamReader) -> dict[str, str]:
        """Read enough of an HTTP request to extract its query string as a flat dict.

        Only the request line is needed (``GET /?code=...&state=... HTTP/1.1``); headers and any
        body are irrelevant to us and are not read. ``readline`` on a ``StreamReader`` already
        bounds itself by the reader's internal buffer limit, matching the ``_MAX_REQUEST_BYTES``
        intent without needing a manual byte-count loop.
        """

        request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not request_line:
            raise ValueError("empty request")

        try:
            text = request_line.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("non-ASCII request line") from exc

        parts = text.strip("\r\n").split(" ")
        if len(parts) < 2 or parts[0] != "GET":
            raise ValueError(f"unexpected request line: {parts[0] if parts else ''!r}")

        target = parts[1]
        split = urlsplit(target)
        return dict(parse_qsl(split.query, keep_blank_values=True))

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, payload: bytes) -> None:
        try:
            writer.write(payload)
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover - best-effort
                pass
