"""YAML load/dump helpers shared by sync phases.

Sync phases need to read and write ``config.yaml`` and
``context_length_cache.yaml``. Both use the same dump formatting:
block style, ``sort_keys=False`` so operator order is preserved,
``allow_unicode=True`` so non-ASCII keys/values survive, ``width=100``
to avoid gratuitous line wrapping on typical config keys.

A redaction-sensitive validation step runs on dumped output before
write: ``yaml.safe_load`` round-trips must succeed or the caller is
told the file is unsafe to write. This catches the rare case where
talaria produces YAML it can't parse back (a sign of a bug, not a
bad config).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict:
    """Load a YAML file as a dict.

    An empty or null file returns ``{}`` rather than ``None`` so
    callers can ``.get()`` on the result without a None check. A
    missing file also returns ``{}`` — callers that need to
    distinguish "absent" from "empty" should check
    ``path.exists()`` first.
    """
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def dump_yaml(data: dict) -> str:
    """Serialise *data* to a YAML string with the talaria-sync conventions.

    See module docstring for the formatting rationale. The output is
    not terminated with a newline — callers append their own as needed
    (most just ``open(path, "w").write(output)``).
    """
    return yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def validate_yaml(text: str) -> tuple[bool, str | None]:
    """Confirm *text* round-trips through :func:`yaml.safe_load`.

    Returns ``(True, None)`` on success or ``(False, error_message)``
    on parse failure. Phases call this on their dump before writing
    so a malformed result never reaches disk.
    """
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, str(e)
    return True, None