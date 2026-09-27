"""Unit tests for winonlinux.auth_loopback (spec.md: hardened loopback redirect, D-6, section 10.3).

Pure asyncio -- real TCP connections to 127.0.0.1, no mocking of the socket layer, so these tests
exercise the actual accept/parse/respond path. Matches tests/test_freerdp_probe.py and
tests/test_task_registry.py's convention of driving the event loop with plain ``asyncio.run``
rather than a pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from winonlinux.auth_loopback import LoopbackListener, LoopbackResult, LoopbackTimeout

pytestmark = pytest.mark.unit


async def _send_redirect(port: int, query: str) -> bytes:
    """Open a real TCP connection to the listener and send a GET redirect request, returning
    whatever bytes it wrote back before closing."""

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        request = f"GET /?{query} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode("ascii")
        writer.write(request)
        await writer.drain()
        return await reader.read()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# -- happy path -----------------------------------------------------------------------------


def test_matching_state_is_accepted_and_returns_query_params():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()

            wait_task = asyncio.ensure_future(listener.wait_for_redirect("xyz-state"))
            response = await _send_redirect(port, "code=auth-code-123&state=xyz-state&session_state=abc")

            result = await wait_task
            assert isinstance(result, LoopbackResult)
            assert result.query_params == {
                "code": "auth-code-123",
                "state": "xyz-state",
                "session_state": "abc",
            }
            assert response.startswith(b"HTTP/1.1 200 OK")
        finally:
            await listener.close()

    asyncio.run(scenario())


# -- forged / mismatched state ---------------------------------------------------------------


def test_non_matching_state_is_rejected_and_does_not_break_subsequent_correct_request():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()

            wait_task = asyncio.ensure_future(listener.wait_for_redirect("expected-state"))

            # A forged/wrong attempt arrives first.
            forged_response = await _send_redirect(port, "code=stolen&state=wrong-state")
            assert forged_response.startswith(b"HTTP/1.1 400")

            # The wait must still be pending -- the forged attempt did not satisfy it.
            await asyncio.sleep(0.02)
            assert not wait_task.done()

            # The real redirect, with the correct state, still succeeds within the same call.
            real_response = await _send_redirect(port, "code=real-code&state=expected-state")
            assert real_response.startswith(b"HTTP/1.1 200 OK")

            result = await wait_task
            assert result.query_params["code"] == "real-code"
            assert result.query_params["state"] == "expected-state"
        finally:
            await listener.close()

    asyncio.run(scenario())


def test_missing_state_is_rejected():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()
            wait_task = asyncio.ensure_future(listener.wait_for_redirect("expected-state"))

            response = await _send_redirect(port, "error=access_denied")
            assert response.startswith(b"HTTP/1.1 400")

            await asyncio.sleep(0.02)
            assert not wait_task.done()

            wait_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait_task
        finally:
            await listener.close()

    asyncio.run(scenario())


# -- single-response enforcement -------------------------------------------------------------


def test_second_connection_after_acceptance_is_rejected_and_not_parsed_into_new_result():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()

            wait_task = asyncio.ensure_future(listener.wait_for_redirect("s1"))
            first_response = await _send_redirect(port, "code=first-code&state=s1")
            first_result = await wait_task
            assert first_response.startswith(b"HTTP/1.1 200 OK")
            assert first_result.query_params["code"] == "first-code"

            # A second connection -- even with a correct-looking, matching state -- must not be
            # turned into a second LoopbackResult. Documented behavior: it gets an "already used"
            # response and is never parsed further.
            second_response = await _send_redirect(port, "code=second-code&state=s1")
            assert second_response.startswith(b"HTTP/1.1 410")

            # Calling wait_for_redirect() again must not hang, and must not manufacture a second
            # result from that rejected connection -- it returns the original accepted result.
            second_wait = await asyncio.wait_for(listener.wait_for_redirect("s1"), timeout=1.0)
            assert second_wait is first_result
            assert second_wait.query_params["code"] == "first-code"
        finally:
            await listener.close()

    asyncio.run(scenario())


# -- concurrent acceptance (TOCTOU regression) -----------------------------------------------


def test_two_concurrent_matching_requests_result_in_exactly_one_accepted():
    # Regression test: the accept-vs-reject decision and the commit of _accepted_result/
    # _accepted_event must be atomic across connections, not just internally consistent. Before
    # the _accept_lock fix, two connections dispatched genuinely concurrently (via gather, so
    # asyncio can interleave them at await points) could both observe "not yet accepted" and both
    # get a 200 OK, with whichever one's event.set() ran last silently overwriting the other's
    # result -- this test would have been flaky (occasionally ok_count == 2) before that fix.
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()
            wait_task = asyncio.ensure_future(listener.wait_for_redirect("shared-state"))

            responses = await asyncio.gather(
                _send_redirect(port, "code=race-a&state=shared-state"),
                _send_redirect(port, "code=race-b&state=shared-state"),
            )

            result = await wait_task

            ok_count = sum(1 for r in responses if r.startswith(b"HTTP/1.1 200 OK"))
            gone_count = sum(1 for r in responses if r.startswith(b"HTTP/1.1 410"))
            assert ok_count == 1
            assert gone_count == 1

            # The accepted LoopbackResult must match whichever connection actually got 200 OK --
            # not the other one, and not some mix of the two.
            accepted_code = "race-a" if responses[0].startswith(b"HTTP/1.1 200 OK") else "race-b"
            assert result.query_params["code"] == accepted_code
        finally:
            await listener.close()

    asyncio.run(scenario())


# -- arm() closes the pre-wait_for_redirect timing window ------------------------------------


def test_arm_before_wait_for_redirect_accepts_a_request_that_arrives_early():
    # The server (from start()) accepts connections immediately, but without arm(), a request
    # arriving before wait_for_redirect() sets the expected state would be wrongly rejected as
    # non-matching. arm() closes that window by letting a caller set the expected state as soon
    # as it knows it (right after initiate_auth_code_flow returns), before opening the browser.
    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()
            await listener.arm("armed-state")

            # Fully processed BEFORE wait_for_redirect() is ever called.
            response = await _send_redirect(port, "code=fast-code&state=armed-state")
            assert response.startswith(b"HTTP/1.1 200 OK")

            result = await asyncio.wait_for(
                listener.wait_for_redirect("armed-state"), timeout=1.0
            )
            assert result.query_params["code"] == "fast-code"
        finally:
            await listener.close()

    asyncio.run(scenario())


def test_arm_before_start_raises():
    async def scenario():
        listener = LoopbackListener()
        with pytest.raises(RuntimeError):
            await listener.arm("some-state")

    asyncio.run(scenario())


# -- timeout ----------------------------------------------------------------------------------


def test_timeout_raised_within_configured_bound_when_nothing_arrives():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=0.2)
        try:
            await listener.start()
            started = time.monotonic()
            with pytest.raises(LoopbackTimeout):
                await listener.wait_for_redirect("never-arrives")
            elapsed = time.monotonic() - started

            # Not immediate (it genuinely waited) and not indefinite (it respected the deadline).
            assert elapsed >= 0.15
            assert elapsed < 2.0
        finally:
            await listener.close()

    asyncio.run(scenario())


def test_deadline_is_measured_from_start_not_from_wait_for_redirect():
    async def scenario():
        listener = LoopbackListener(timeout_seconds=0.2)
        try:
            await listener.start()
            # Simulate the caller doing other setup work between start() and wait_for_redirect().
            await asyncio.sleep(0.15)

            started = time.monotonic()
            with pytest.raises(LoopbackTimeout):
                await listener.wait_for_redirect("never-arrives")
            elapsed = time.monotonic() - started

            # Only ~0.05s of budget remained by the time wait_for_redirect was called -- if the
            # deadline were (wrongly) measured from this call instead, this would take ~0.2s.
            assert elapsed < 0.15
        finally:
            await listener.close()

    asyncio.run(scenario())


# -- close() ------------------------------------------------------------------------------------


def test_close_is_idempotent_and_safe_without_start():
    async def scenario():
        never_started = LoopbackListener()
        await never_started.close()
        await never_started.close()

        started = LoopbackListener(timeout_seconds=5.0)
        await started.start()
        await started.close()
        await started.close()

    asyncio.run(scenario())


# -- oversized and stalled connections (add-audit-test-coverage, audit F-10) -------------------


def test_oversized_request_line_is_rejected_without_consuming_the_accept_slot():
    """A request line past the 16 KiB cap gets the invalid-request response, and a subsequent
    correct redirect on a new connection is still accepted -- the cap is a per-connection
    defense, not a listener-wide denial (spec.md section 10.3)."""

    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()
            wait_task = asyncio.ensure_future(listener.wait_for_redirect("good-state"))

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                # One request line far past _MAX_REQUEST_BYTES, no newline until the very end.
                writer.write(b"GET /?state=" + b"A" * (20 * 1024) + b" HTTP/1.1\r\n\r\n")
                try:
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    pass  # the handler may already have rejected and closed on us
                oversized_response = await reader.read()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

            # Either an explicit 400 or an early close -- never a 200, never an acceptance.
            if oversized_response:
                assert oversized_response.startswith(b"HTTP/1.1 400")
            assert not wait_task.done()

            # The genuine redirect still lands: the slot was not consumed.
            good_response = await _send_redirect(port, "code=real-code&state=good-state")
            assert good_response.startswith(b"HTTP/1.1 200 OK")
            result = await wait_task
            assert result.query_params["code"] == "real-code"
        finally:
            await listener.close()

    asyncio.run(scenario())


def test_stalled_connection_is_freed_at_the_read_timeout_and_real_redirect_succeeds(monkeypatch):
    """Slowloris check: a connection that never completes its request line is released at the
    per-connection read timeout (shrunk here so the test runs in real time), while the genuine
    redirect on another connection completes within the deadline."""
    from winonlinux import auth_loopback

    monkeypatch.setattr(auth_loopback, "_REQUEST_READ_TIMEOUT_SECONDS", 0.2)

    async def scenario():
        listener = LoopbackListener(timeout_seconds=5.0)
        try:
            port = await listener.start()
            wait_task = asyncio.ensure_future(listener.wait_for_redirect("good-state"))

            # The staller: connects, sends a partial request line, never finishes it.
            stall_reader, stall_writer = await asyncio.open_connection("127.0.0.1", port)
            stall_writer.write(b"GET /?st")  # no newline, ever
            await stall_writer.drain()

            # The genuine redirect is not blocked by the stalled handler.
            started = time.monotonic()
            good_response = await _send_redirect(port, "code=real-code&state=good-state")
            assert good_response.startswith(b"HTTP/1.1 200 OK")
            assert (time.monotonic() - started) < 2.0
            result = await wait_task
            assert result.query_params["code"] == "real-code"

            # And the staller is freed at the shrunken read timeout with the invalid response
            # (an "already used" 410 would also be a non-acceptance; the point is it is released
            # and never parsed into a result).
            stalled_response = await asyncio.wait_for(stall_reader.read(), timeout=2.0)
            if stalled_response:
                assert not stalled_response.startswith(b"HTTP/1.1 200")
            stall_writer.close()
            try:
                await stall_writer.wait_closed()
            except Exception:
                pass
        finally:
            await listener.close()

    asyncio.run(scenario())


def test_listener_binds_loopback_only_on_distinct_ephemeral_ports():
    """Pins the bind-host and ephemeral-port properties directly via getsockname() (audit F-22,
    controls 4 and 5): 127.0.0.1 exactly -- never wildcard -- and a fresh non-privileged port
    per listener."""

    async def scenario():
        first = LoopbackListener(timeout_seconds=5.0)
        second = LoopbackListener(timeout_seconds=5.0)
        try:
            port_one = await first.start()
            port_two = await second.start()

            for listener, port in ((first, port_one), (second, port_two)):
                socknames = [s.getsockname() for s in listener._server.sockets]
                assert socknames, "listener should hold at least one bound socket"
                for host, bound_port, *_rest in socknames:
                    assert host == "127.0.0.1"
                    assert bound_port == port
                    assert bound_port > 1024

            assert port_one != port_two
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())
