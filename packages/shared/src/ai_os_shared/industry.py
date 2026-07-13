"""Industry registry — the config-driven catalogue of industries.

An "industry" is NOT code. It is a pack manifest (`packs/<industry>/pack.json`) whose
`industry` field is not "generic" and that (optionally) carries a `workspace` block
describing how a frontend should render that industry's workspace — nav, terminology,
theme, entities, copilots. Every industry talks to the SAME backend/API; only this
config differs. Adding a new industry = dropping a new `pack.json`. No code changes.

Both the gateway (public `/industries` list, pre-login) and the identity service
(`/workspace/config` for the logged-in user, and signup validation) read this registry.

Runtime note: `ai_os_shared` is installed into site-packages, so we cannot walk up from
`__file__` to find `packs/` inside a container. Resolution order:
  1. `$AIOS_PACKS_DIR` (set to `/app/packs` in the service image),
  2. a `packs/` dir found by walking up from this file (source checkouts / tests),
  3. `/app/packs`, then `./packs`.
If none resolve, the registry is simply empty (endpoints return `[]` rather than crash).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from ai_os_shared.workflow.schema import PackManifest, WorkspaceConfig

GENERIC_INDUSTRY = "generic"


class Industry(BaseModel):
    """A resolved industry: its key (== a user's `login_source`), display name,
    frontend workspace config, and the workflow packs that belong to it."""

    key: str
    name: str
    workspace: WorkspaceConfig
    workflow_packs: list[str] = []

    def summary(self) -> dict:
        """Lightweight shape for the public `/industries` list (signup dropdowns)."""
        return {
            "key": self.key,
            "name": self.name,
            "tagline": self.workspace.tagline,
            "theme": self.workspace.theme,
        }


def _packs_root() -> Path:
    env = os.getenv("AIOS_PACKS_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "packs")
    candidates.append(Path("/app/packs"))
    candidates.append(Path.cwd() / "packs")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return Path("/app/packs")


def _load_manifests(root: Path) -> list[PackManifest]:
    manifests: list[PackManifest] = []
    if not root.is_dir():
        return manifests
    for child in sorted(root.iterdir()):
        manifest_path = child / "pack.json"
        if not manifest_path.exists():
            continue
        manifests.append(
            PackManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        )
    return manifests


@lru_cache(maxsize=1)
def _registry() -> dict[str, Industry]:
    """Build {industry_key -> Industry} from all non-generic pack manifests. Cached:
    packs are static config for the life of the process."""
    by_industry: dict[str, Industry] = {}
    for manifest in _load_manifests(_packs_root()):
        if manifest.industry == GENERIC_INDUSTRY:
            continue
        existing = by_industry.get(manifest.industry)
        if existing:
            # A second pack for the same industry: merge its workflows in; keep the
            # first workspace config (industries have one canonical workspace pack).
            existing.workflow_packs.append(manifest.key)
            continue
        workspace = manifest.workspace or WorkspaceConfig(display_name=manifest.name)
        by_industry[manifest.industry] = Industry(
            key=manifest.industry,
            name=workspace.display_name or manifest.name,
            workspace=workspace,
            workflow_packs=[manifest.key],
        )
    return by_industry


def list_industries() -> list[Industry]:
    """All configured industries, sorted by key (stable ordering for the FE)."""
    return [_registry()[k] for k in sorted(_registry())]


def get_industry(key: str | None) -> Industry | None:
    if not key:
        return None
    return _registry().get(key)


def industry_keys() -> set[str]:
    """Valid `login_source` values — signup validates against this, not a frozen set."""
    return set(_registry())


def reload() -> None:
    """Drop the cache (tests / hot-reload after editing pack.json)."""
    _registry.cache_clear()
