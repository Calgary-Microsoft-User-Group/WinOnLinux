# Design: fix-redaction-hardening

## Context

The redaction mechanism itself audited clean — LogRecord-factory enforcement is order-independent, covers
third-party loggers, and closes the traceback path. The defects are in the *patterns*: one over-matches
(`\b[A-Za-z0-9_-]{32,}\b` swallows hyphenated GUIDs), one under-matches (`^...$` anchoring misses embedded
tokens), and one entry path skips installation entirely. The two stores already disagree about GUIDs —
`state_store.py` carves them out, `logging_setup.py` does not — so this change also removes an inconsistency.

## Goals / Non-Goals

**Goals:**

- Cloud PC GUIDs and `oid.tid` account ids survive in logs; real token shapes never do.
- A token concatenated into a longer string is refused by the state store.
- No documented start path runs without the record factory installed.

**Non-Goals:**

- No change to UPN redaction, verbose mode, `.rdpw` heuristics, or the factory mechanism.
- No attempt at perfect token detection — these are defense-in-depth heuristics behind the primary rule (callers
  never pass tokens); ambiguity keeps resolving toward redaction/refusal.

## Decisions

- **Carve-out order: UUID first, then opaque-token sub.** Replace canonical UUIDs (and dotted pairs) with
  placeholders before applying `_OPAQUE_TOKEN_PATTERN`, then restore — the same approach `state_store.py:66-76`
  uses, keeping one idiom across both modules. A 32+ run that is *not* a canonical UUID still redacts; JWT and
  `Authorization`-header patterns run unchanged and still win over the carve-out (a token containing a UUID-shaped
  substring is not exempted, because the JWT/header patterns match first on the full run).
- **State store gains unanchored `search` variants** of the JWT and opaque patterns for string values, alongside
  the existing whole-value checks. False-positive risk (a legitimate 32-char alnum substring in a bookmark name)
  resolves toward refusal per D-13's posture; the error message names the offending key path so the caller can fix
  its data.
- **`app.main()` calls `configure_logging()`** rather than deleting `app.py`'s `__main__` block: idempotent (the
  module already guards double-install), keeps the developer convenience, and structurally covers any future entry
  point that goes through `main()`. `__main__.py`'s explicit call stays as the place verbose will someday hang off.

## Risks

- The carve-out must not create a bypass: a test asserts a real JWT containing a UUID-shaped segment is still
  redacted in full.
