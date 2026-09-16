"""Central logging configuration and redaction (spec.md section 10.7, design.md "logging layer").

Redaction is enforced structurally, not by caller discipline. Tokens, ``Authorization`` header
values, and full ``.rdpw`` bodies are redacted at every log level; UPNs are redacted unless
``configure_logging(verbose=True)`` was called.

Enforcement mechanism -- why a plain root-logger ``Filter`` is not enough
--------------------------------------------------------------------------
The obvious approach -- ``logging.getLogger().addFilter(RedactionFilter())`` -- does **not**
redact records from the per-module loggers this codebase actually uses
(``logging.getLogger(__name__)``). That is a well-known stdlib gotcha, worth spelling out because
getting it wrong would silently defeat the whole requirement:

``Logger.handle(record)`` calls ``self.filter(record)`` -- ``self`` being the *originating*
logger a call like ``logging.getLogger("winonlinux.foo").info(...)`` was made on -- and only then
walks up the ancestor chain (``Logger.callHandlers``) invoking each ancestor's *handlers*.
Ancestor **loggers'** own filter lists are never consulted again during that walk. So a filter
attached only to the root ``Logger`` object fires solely for records logged directly on the root
logger, not for records from any named child logger -- which is every logger in this codebase.

Filters attached to a **handler**, by contrast, run for every record that reaches that handler,
regardless of which logger originated it (``Handler.handle`` -> ``Handler.filter``, invoked once
per handler as ``callHandlers`` walks the chain). That is closer to "regardless of which module
logs", but it is still order-dependent against *other* handlers attached to the root logger --
notably a test harness's own capture handler (e.g. pytest's ``caplog``), which may be registered
before or after ours and would see the raw, unredacted record if it runs first.

So the actual, order-independent enforcement point this module uses is
``logging.setLogRecordFactory``: every ``LogRecord``, from every logger, is redacted once at
*construction* time, before any logger or handler filter list -- including one attached by a test
harness -- ever sees it. A ``RedactionFilter`` (a real ``logging.Filter``) is still provided and is
attached to the root logger and its handler by :func:`configure_logging`, both because the task
calls for "a logging.Filter" and for defense-in-depth (it is a harmless no-op re-application on an
already-redacted record), but the record factory is what actually makes redaction mandatory.

The ".rdpw content" marking convention
---------------------------------------
Pattern-matching a rendered log message for RDP-file-shaped lines (``full address:s:...`` /
``username:s:...``) catches most accidental logging of a full ``.rdpw`` body, but it is fragile:
a fragment of the file, a differently-ordered file, or content built up before those particular
keys are written would not match. :class:`RdpwContent` is a ``str`` subclass a caller can wrap a
value in (or use :func:`mark_rdpw_content`) to *guarantee* the whole value is replaced with the
redaction marker regardless of its shape, e.g. ``logger.debug("composed config: %s",
mark_rdpw_content(body))``. Both mechanisms are active at all times; the marker exists for the
cases the pattern would miss, not as a replacement for it.

Redaction never partially masks a value (no "last 4 characters visible" schemes) -- a matched
value, or a whole ``.rdpw``-shaped message, is replaced outright with :data:`REDACTION_MARKER`.

Token-pattern caveat
---------------------
The token patterns here are heuristics (JWT-shaped triples, and long opaque base64url-ish runs),
not validated against a captured, real MSAL/Entra token corpus. Per CLAUDE.md's "verify, don't
assert": treat this as decided-but-unverified until checked against real token shapes, and prefer
widening the patterns (more false positives, i.e. more redaction) over narrowing them.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

__all__ = [
    "REDACTION_MARKER",
    "RdpwContent",
    "mark_rdpw_content",
    "RedactionFilter",
    "configure_logging",
]

REDACTION_MARKER = "[REDACTED]"


class RdpwContent(str):
    """A ``str`` subclass marking a value as full (or partial) ``.rdpw`` file content.

    Wrap a value that is or contains ``.rdpw`` data before passing it to a logging call --
    as the message itself, a ``%``-style arg, or an ``extra`` value -- to guarantee it is
    replaced with :data:`REDACTION_MARKER` even if it does not match the RDP-file-line pattern
    the redaction filter also checks for. See the module docstring for why this exists alongside
    pattern matching rather than instead of it.
    """

    __slots__ = ()


def mark_rdpw_content(value: str) -> RdpwContent:
    """Wrap ``value`` as :class:`RdpwContent` so the logging layer redacts it unconditionally."""

    return RdpwContent(value)


# --- Redaction patterns -----------------------------------------------------------------------
#
# All are heuristic and intentionally biased toward over-matching (redacting something that
# turns out not to have been sensitive) rather than under-matching (letting a secret through).

# OAuth/OIDC access tokens are near-universally JWTs: three '.'-separated base64url segments.
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

# Refresh tokens and other opaque bearer secrets: a long run of base64url-ish characters with no
# internal whitespace. 32 chars is a deliberately low floor to prefer over-redaction.
_OPAQUE_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")

# `Authorization: Bearer <...>` / `Authorization=Basic <...>` / dict-style `'Authorization': '...'`.
# Consumes the whole value token (scheme + credential) so nothing sensitive survives the sub. The
# optional `["']?` between the key and the separator matches dict/JSON-repr shapes where the key
# itself is quoted immediately before the colon (e.g. `{'Authorization': 'Basic ...'}` renders as
# `Authorization': 'Basic ...` right up to that colon) -- without it, that whole shape silently
# failed to match and a short (non-JWT) Basic/Negotiate credential logged that way went unredacted.
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|negotiate)?\s*[^\s\"',}]+"
)

# Basic email-address shape, used as the UPN heuristic per spec.md section 10.7.
_UPN_PATTERN = re.compile(r"\b[A-Za-z0-9.+_-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")

# A `.rdp`/`.rdpw` file is a sequence of `key:type:value` lines; `full address:s:` and
# `username:s:` are two keys guaranteed to appear in every connection file FreeRDP consumes
# (spec.md's fixture list). Finding either at the start of a line is treated as "this text is (or
# contains) a full rdpw body" and the *entire* log message is replaced, not just the line --
# .rdpw content is redacted wholesale, per the requirement, not field-by-field.
_RDPW_LINE_PATTERN = re.compile(r"(?im)^[ \t]*(?:full address|username):s:")

# LogRecord attributes the stdlib sets itself; everything else on a record's __dict__ is a
# caller-supplied `extra` field and gets the same redaction treatment as the message.
_STANDARD_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",  # Python 3.12+
        "message",
    }
)


def _redact_text(text: str, *, verbose: bool) -> str:
    """Apply every pattern-based redaction rule to a fully-rendered string."""

    if _RDPW_LINE_PATTERN.search(text):
        return REDACTION_MARKER

    text = _AUTH_HEADER_PATTERN.sub(f"Authorization: {REDACTION_MARKER}", text)
    text = _JWT_PATTERN.sub(REDACTION_MARKER, text)
    text = _OPAQUE_TOKEN_PATTERN.sub(REDACTION_MARKER, text)
    if not verbose:
        text = _UPN_PATTERN.sub(REDACTION_MARKER, text)
    return text


def _redact_value(value: Any, *, verbose: bool) -> Any:
    """Redact a single value (a message, a %-arg, or an `extra` attribute), recursively.

    Recurses into ``dict``/``list``/``tuple`` so a secret nested inside a structured ``extra``
    value (e.g. ``extra={"headers": {"Authorization": "Bearer ..."}}``) is caught the same way a
    top-level string value is -- mirroring ``state_store._find_secret``'s recursion, which exists
    for the identical reason. Without this, only the outermost value of a structured extra was
    ever inspected and anything nested inside a dict/list sailed through unredacted.
    """

    if isinstance(value, RdpwContent):
        return REDACTION_MARKER
    if isinstance(value, str):
        return _redact_text(value, verbose=verbose)
    if isinstance(value, dict):
        return {key: _redact_value(val, verbose=verbose) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        redacted_items = [_redact_value(item, verbose=verbose) for item in value]
        return type(value)(redacted_items) if isinstance(value, tuple) else redacted_items
    return value


_state_lock = threading.Lock()
_verbose_enabled = False


def _is_verbose() -> bool:
    with _state_lock:
        return _verbose_enabled


def _set_verbose(value: bool) -> None:
    global _verbose_enabled
    with _state_lock:
        _verbose_enabled = value


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact one LogRecord in place: the message (however it was built) and any extras.

    Handles all three ways sensitive data reaches a record:
      * a raw/pre-formatted string message,
      * %-style lazy args (``logger.info("token=%s", token)``),
      * structured ``extra`` kwargs, which land as arbitrary attributes on the record.
    """

    verbose = _is_verbose()

    # Neutralize explicit RdpwContent markers *before* %-formatting, so the merged message
    # never contains the raw payload in the first place.
    if isinstance(record.msg, RdpwContent):
        record.msg = REDACTION_MARKER
    args = record.args
    if isinstance(args, tuple):
        record.args = tuple(
            REDACTION_MARKER if isinstance(a, RdpwContent) else a for a in args
        )
    elif isinstance(args, dict):
        record.args = {
            key: (REDACTION_MARKER if isinstance(val, RdpwContent) else val)
            for key, val in args.items()
        }

    try:
        rendered = record.getMessage()
    except Exception:  # pragma: no cover - malformed %-args; fail safe, don't crash logging
        rendered = str(record.msg)

    record.msg = _redact_text(rendered, verbose=verbose)
    record.args = None  # already merged above; prevent a second, now-mismatched % pass

    # Exception tracebacks (logger.exception(...) / exc_info=True) are a real, easy-to-miss leak
    # path: Handler.emit() renders record.exc_info to text lazily, *after* this factory/filter has
    # already run, via logging.Formatter.formatException() -- so leaving exc_info untouched here
    # would let a raw, unredacted traceback (HTTP client libraries routinely embed the request URL
    # or an Authorization header in an exception's own message) reach every handler regardless of
    # everything else this function does. Render and redact it eagerly here instead, then clear
    # exc_info so no downstream formatter can re-render the original, unredacted traceback text.
    if record.exc_info:
        exc_text = record.exc_text
        if exc_text is None:
            exc_text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = _redact_text(exc_text, verbose=verbose)
        record.exc_info = None
    if record.stack_info:
        record.stack_info = _redact_text(str(record.stack_info), verbose=verbose)

    for key, value in list(record.__dict__.items()):
        if key in _STANDARD_RECORD_ATTRS:
            continue
        record.__dict__[key] = _redact_value(value, verbose=verbose)

    return record


class RedactionFilter(logging.Filter):
    """A ``logging.Filter`` that redacts every record it sees.

    Always returns ``True`` (never drops a record) and mutates ``record`` in place. Safe to
    apply more than once to the same record -- redacting already-redacted text is a no-op.
    See the module docstring for why :func:`configure_logging` does not rely on this alone.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record)
        return True


_factory_lock = threading.Lock()
_factory_installed = False


def _install_record_factory() -> None:
    """Install a redacting ``LogRecord`` factory, process-wide, exactly once.

    This is the actual, order-independent enforcement mechanism -- see the module docstring.
    Idempotent: calling :func:`configure_logging` repeatedly does not stack wrappers.
    """

    global _factory_installed
    with _factory_lock:
        if _factory_installed:
            return
        original_factory = logging.getLogRecordFactory()

        def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = original_factory(*args, **kwargs)
            return _redact_record(record)

        logging.setLogRecordFactory(redacting_factory)
        _factory_installed = True


def configure_logging(verbose: bool = False, *, level: int | None = None) -> None:
    """Configure process-wide logging with mandatory redaction (spec.md section 10.7).

    Idempotent and safe to call more than once (e.g. if the verbosity setting changes at
    runtime) -- later calls update the effective verbosity without installing duplicate
    handlers or filters.

    Args:
        verbose: When ``True``, UPNs are left visible in log output (verbose diagnostics,
            explicitly opted into). Tokens, ``Authorization`` headers, and ``.rdpw`` bodies are
            redacted regardless of this flag -- there is no way to opt out of those. Also raises
            the root logger's level to ``DEBUG`` unless ``level`` overrides it.
        level: Explicit root logger level. Defaults to ``DEBUG`` when ``verbose`` else ``INFO``.
    """

    _set_verbose(verbose)
    _install_record_factory()

    root = logging.getLogger()

    if not any(isinstance(f, RedactionFilter) for f in root.filters):
        root.addFilter(RedactionFilter())

    root.setLevel(level if level is not None else (logging.DEBUG if verbose else logging.INFO))

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)

    for handler in root.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(RedactionFilter())
