"""Dot-path helpers for filtering ``config.yaml`` during sync.

Sync applies the same element-level merge the original
``hermes-config-sync.py`` did: the operator excludes paths
(``-e mcp_servers model``) or restricts to specific paths
(``--only moa.max_tokens``). These helpers back those modes
without re-implementing them.

The implementation is small and pure: no CLI, no I/O, no
filesystem. Phases that need YAML I/O compose these helpers with
:mod:`yaml_io`.
"""

from __future__ import annotations

import copy
from typing import Any


def get_path(data: dict, dotpath: str) -> tuple[bool, Any]:
    """Look up *dotpath* in *data* and return ``(found, value)``.

    Returns ``(False, None)`` when any intermediate key is missing or
    is not a dict. A *dotpath* of ``""`` is not supported; pass an
    explicit key.
    """
    keys = dotpath.split(".")
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def set_path(data: dict, dotpath: str, value: Any) -> None:
    """Set *value* at *dotpath*, creating intermediate dicts as needed.

    The existing structure is preserved where possible — only the
    leaf and missing intermediates are replaced. The final segment
    is always written, even if it overwrites a scalar.
    """
    keys = dotpath.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def del_path(data: dict, dotpath: str) -> bool:
    """Delete *dotpath* from *data*. Returns ``True`` if anything was deleted.

    Empty parent dicts left behind are also cleaned up so that
    ``-e model.aliases`` on a target without ``model`` does not leave
    a stray ``model: {}`` in the merged result.
    """
    keys = dotpath.split(".")
    if not keys:
        return False
    current: Any = data
    parents: list[tuple[dict, str]] = []
    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            return False
        parents.append((current, key))
        current = current[key]
    if not isinstance(current, dict) or keys[-1] not in current:
        return False
    del current[keys[-1]]
    for parent, key in reversed(parents):
        child = parent[key]
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break
    return True


def list_keys(data: dict, *, prefix: str = "", depth: int = 0, max_depth: int = 2) -> list[str]:
    """Enumerate dot-notation paths in *data* down to *max_depth*.

    Used by ``--list`` to show the operator which paths are available
    for ``--exclude`` / ``--only``. The depth applies to nesting, not
    to the number of keys emitted — depth 1 returns only top-level
    keys, depth 2 includes one level of children, etc.
    """
    result: list[str] = []
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else key
        result.append(path)
        if isinstance(val, dict) and depth + 1 < max_depth:
            result.extend(list_keys(val, prefix=path, depth=depth + 1, max_depth=max_depth))
    return result


def sync_exclude(source: dict, target: dict, excludes: list[str]) -> dict:
    """Build a merged config from *source* minus the excluded paths.

    Excluded paths are restored from *target* when present; if the
    target also lacks the path, it is dropped from the result so the
    merge does not silently retain a stale value. Target-only
    top-level keys (profile-specific extras not in source) are
    preserved at the top level.
    """
    result = copy.deepcopy(source)
    for path in excludes:
        target_found, target_val = get_path(target, path)
        if target_found:
            set_path(result, path, copy.deepcopy(target_val))
        else:
            del_path(result, path)
    for key in target:
        if key not in result:
            result[key] = copy.deepcopy(target[key])
    return result


def sync_only(source: dict, target: dict, only_paths: list[str]) -> dict:
    """Build a merged config starting from *target* with only the listed paths copied from *source*.

    Paths that don't exist in *source* are silently skipped (a warning
    is emitted by the caller so the operator sees it). Everything else
    in *target* is preserved.
    """
    result = copy.deepcopy(target)
    for path in only_paths:
        source_found, source_val = get_path(source, path)
        if source_found:
            set_path(result, path, copy.deepcopy(source_val))
    return result