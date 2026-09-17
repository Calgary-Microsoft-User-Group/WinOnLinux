"""Persisted, non-secret application state.

Implements the "Persisted state store with versioned forward-only migrations" requirement
(spec.md, add-app-foundation change, D-13):

- Each store is one JSON file at ``$XDG_STATE_HOME/winonlinux/<name>.json``, falling back to
  ``~/.local/state/winonlinux`` when ``XDG_STATE_HOME`` is unset, per the XDG Base Directory
  Specification.
- Every store file carries a top-level ``schemaVersion`` integer.
- Loading a store whose ``schemaVersion`` is *lower* than the code's current version runs
  forward-only migration steps in order, then rewrites the file at the current version.
- Loading a store whose ``schemaVersion`` is *higher* than the code's current version leaves the
  file untouched on disk and returns in-memory defaults instead of crashing or truncating it.
- Writes go to a temp file in the same directory followed by ``os.replace`` so a crash mid-write
  can never leave a half-written store on disk.
- Tokens and other secret material MUST NEVER reach this store. ``save()`` refuses (raises
  ``SecretValueRejected``) any value that looks secret-shaped, structurally -- callers cannot opt
  out of the check. Refusals are logged at WARNING with the offending value redacted.

This module only ever imports the standard library ``logging`` module directly (not
``winonlinux.logging_setup``) so it has no ordering dependency on that parallel task; its log
records are shaped to compose cleanly with whatever redaction filter that module installs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "StateStore",
    "Secret",
    "SecretValueRejected",
    "REDACTED",
]

logger = logging.getLogger(__name__)

#: Fixed marker written to logs in place of a real secret value. Never the real value.
REDACTED = "[REDACTED]"

# Key names that, wherever they appear (at any nesting depth), mark their value as secret
# material regardless of what the value looks like.
_SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|pwd|credential|authorization|"
    r"api[_-]?key|client[_-]?secret|refresh|bearer|access[_-]?key)",
    re.IGNORECASE,
)

# A bare string value shaped like a JWT (three dot-separated base64url segments) is treated as
# secret even when its key name gives no hint -- this is how ID tokens (and many access tokens)
# look in practice.
_JWT_LIKE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")

# Entra access/refresh tokens are frequently *opaque* (no dot structure at all, unlike ID tokens)
# -- a long run of base64url-ish characters with nothing else in the string. Anchored to the whole
# value (this checks a discrete JSON value, not a rendered line of text, so there is no surrounding
# context to preserve) with a deliberately low 32-character floor, mirroring logging_setup's
# equivalent fallback, to catch exactly this shape even under a key name the pattern above misses.
_OPAQUE_TOKEN_LIKE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")

# A canonical UUID (8-4-4-4-12 hex, hyphenated) is EXCLUDED from the opaque-token check above,
# even though its 36 characters otherwise match that pattern's charset and length floor. Azure/
# Graph resource, tenant, and object identifiers are routinely bare GUIDs (workspaceId, resourceId,
# tenant id, etc.) and are legitimate, non-secret data this store must be able to persist -- a real
# opaque bearer/refresh token is a single contiguous base64url run and is not formatted with this
# specific dash grouping, so this carve-out does not meaningfully widen what the heuristic misses.
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: A migration step: takes the store dict as it existed at ``schemaVersion == from_version`` and
#: returns the dict upgraded to ``from_version + 1``. Must carry forward every field it does not
#: intentionally drop -- migrations are forward-only, so this is the only chance to preserve data.
MigrationFn = Callable[[dict], dict]


@dataclasses.dataclass(frozen=True)
class Secret:
    """Explicit wrapper a caller can use to mark a value as secret material.

    Wrap any value that must never be persisted -- e.g. ``Secret(access_token)`` -- before
    passing it (nested anywhere) to :meth:`StateStore.save`. Its presence refuses the whole
    write, independent of the key-name/value heuristics. This exists for values the heuristics
    would not otherwise catch; it is not required for values already token- or secret-shaped.
    """

    value: Any


class SecretValueRejected(ValueError):
    """Raised by :meth:`StateStore.save` when the payload contains secret-shaped data."""


def _find_secret(value: Any, key_path: str = "$") -> tuple[str, Any] | None:
    """Recursively scan ``value`` for secret-shaped data.

    Returns ``(json_path, offending_value)`` for the first match found (depth-first, stable
    iteration order), or ``None`` if nothing secret-shaped was found.
    """
    if isinstance(value, Secret):
        return key_path, value.value

    if isinstance(value, dict):
        for key, sub_value in value.items():
            sub_path = f"{key_path}.{key}"
            if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
                return sub_path, sub_value
            found = _find_secret(sub_value, sub_path)
            if found is not None:
                return found
        return None

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _find_secret(item, f"{key_path}[{index}]")
            if found is not None:
                return found
        return None

    if isinstance(value, str) and (
        _JWT_LIKE_PATTERN.match(value)
        or (_OPAQUE_TOKEN_LIKE_PATTERN.match(value) and not _UUID_PATTERN.match(value))
    ):
        return key_path, value

    return None


class StateStore:
    """Persists one non-secret JSON document at
    ``$XDG_STATE_HOME/winonlinux/<name>.json``.

    Parameters
    ----------
    name:
        Store name; becomes the ``<name>.json`` filename. Callers own one ``StateStore`` per
        logical store (e.g. ``"window"``, ``"accounts"``).
    schema_version:
        The current schema version this code understands. Stores older than this are migrated
        forward on load; stores newer than this are left untouched and defaults are used instead.
    migrations:
        Mapping of ``from_version -> MigrationFn`` covering every version strictly below
        ``schema_version`` that a store might be loaded at. Each function upgrades one version
        step and must preserve every field it is not intentionally dropping.
    defaults:
        Fields to use (merged under ``schemaVersion``) when no store file exists yet, or when the
        on-disk file is newer than ``schema_version``.
    state_home:
        Override for ``$XDG_STATE_HOME`` (primarily for tests). When ``None``, the real
        environment variable is consulted, falling back to ``~/.local/state`` per the XDG Base
        Directory Specification.
    """

    def __init__(
        self,
        name: str,
        *,
        schema_version: int,
        migrations: Mapping[int, MigrationFn] | None = None,
        defaults: dict | None = None,
        state_home: str | os.PathLike[str] | None = None,
    ) -> None:
        if schema_version < 0:
            raise ValueError("schema_version must be >= 0")
        self.name = name
        self.schema_version = schema_version
        self._migrations: dict[int, MigrationFn] = dict(migrations or {})
        self._defaults = dict(defaults or {})
        self._state_home = state_home

    # -- paths ---------------------------------------------------------------

    def _state_dir(self) -> Path:
        if self._state_home is not None:
            base = Path(self._state_home)
        else:
            xdg = os.environ.get("XDG_STATE_HOME")
            base = Path(xdg) if xdg else Path.home() / ".local" / "state"
        return base / "winonlinux"

    @property
    def path(self) -> Path:
        """Absolute path of this store's JSON file."""
        return self._state_dir() / f"{self.name}.json"

    # -- load / migrate --------------------------------------------------------

    def load(self) -> dict:
        """Load the store, migrating it forward in place if it is older than understood.

        Never raises for a missing, corrupt, or newer-than-understood file -- each of those
        cases falls back to in-memory defaults instead.
        """
        path = self.path
        if not path.exists():
            return self._defaulted()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "state store %s at %s is unreadable or not valid JSON; using defaults",
                self.name,
                path,
            )
            return self._defaulted()

        if not isinstance(data, dict):
            logger.warning(
                "state store %s at %s does not contain a JSON object; using defaults",
                self.name,
                path,
            )
            return self._defaulted()

        stored_version = data.get("schemaVersion", 0)
        if not isinstance(stored_version, int):
            stored_version = 0

        if stored_version > self.schema_version:
            logger.warning(
                "state store %s has schemaVersion %s, newer than the %s this build "
                "understands; leaving the file untouched and using in-memory defaults",
                self.name,
                stored_version,
                self.schema_version,
            )
            return self._defaulted()

        if stored_version < self.schema_version:
            migrated = self._migrate(data, stored_version)
            migrated["schemaVersion"] = self.schema_version
            self._write_atomic(migrated)
            return migrated

        return data

    def _defaulted(self) -> dict:
        data = dict(self._defaults)
        data["schemaVersion"] = self.schema_version
        return data

    def _migrate(self, data: dict, from_version: int) -> dict:
        version = from_version
        while version < self.schema_version:
            step = self._migrations.get(version)
            if step is None:
                raise KeyError(
                    f"state store {self.name!r}: no migration registered to carry "
                    f"schemaVersion {version} forward to {version + 1}"
                )
            data = step(dict(data))
            version += 1
        return data

    # -- save --------------------------------------------------------------

    def save(self, data: Mapping[str, Any]) -> None:
        """Persist ``data`` atomically, refusing the write if it contains secret material.

        Raises
        ------
        SecretValueRejected
            If ``data`` contains a key/value the store identifies as secret-shaped, or a
            :class:`Secret` wrapper anywhere in its structure. The write does not happen; the
            existing file, if any, is left exactly as it was.
        """
        offending = _find_secret(data)
        if offending is not None:
            json_path, _value = offending
            logger.warning(
                "refused to persist secret-shaped value at %s in state store %s: %s",
                json_path,
                self.name,
                REDACTED,
            )
            raise SecretValueRejected(
                f"state store {self.name!r}: refused to persist secret-shaped value "
                f"at {json_path!r}"
            )

        payload = dict(data)
        payload["schemaVersion"] = self.schema_version
        self._write_atomic(payload)

    def _write_atomic(self, data: dict) -> None:
        directory = self._state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.name}.", suffix=".tmp", dir=directory
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
