"""Safe expression resolver for workflow step configs.

Steps reference earlier data with `{{ path }}` templates over the run context, e.g.
`{{ inputs.rfi_email.subject }}` or `{{ steps.ocr.out.text }}`. This is deliberately
NOT a general expression language and never uses `eval` — it only looks up dotted
paths (with optional list indices) into a plain dict. That keeps definitions safe,
reviewable, and sandbox-able.

Rules:
  - A string that is *exactly* one `{{ path }}` returns the resolved value with its
    native type (dict/list/number/etc.).
  - `{{ path }}` embedded in a larger string is stringified and interpolated.
  - dicts and lists are resolved recursively.
"""

from __future__ import annotations

import re
from typing import Any

_FULL = re.compile(r"^\s*\{\{\s*([^}]+?)\s*\}\}\s*$")
_EMBED = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


class ExprError(KeyError):
    """Raised when a `{{ path }}` cannot be resolved against the context."""


def _lookup(path: str, context: dict) -> Any:
    cur: Any = context
    for part in path.split("."):
        part = part.strip()
        if isinstance(cur, dict):
            if part not in cur:
                raise ExprError(f"Unresolved path '{path}' (missing '{part}')")
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError) as exc:
                raise ExprError(f"Unresolved path '{path}' (bad index '{part}')") from exc
        else:
            raise ExprError(f"Unresolved path '{path}' (cannot index into {type(cur).__name__})")
    return cur


def _resolve_str(s: str, context: dict) -> Any:
    full = _FULL.match(s)
    if full:
        return _lookup(full.group(1), context)
    return _EMBED.sub(lambda m: str(_lookup(m.group(1), context)), s)


def resolve(value: Any, context: dict) -> Any:
    """Recursively resolve `{{ }}` templates in a config value against the context."""
    if isinstance(value, str):
        return _resolve_str(value, context)
    if isinstance(value, dict):
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    return value
