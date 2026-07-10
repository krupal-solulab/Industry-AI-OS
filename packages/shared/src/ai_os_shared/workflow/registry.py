"""Pack loading + validation.

A pack is a directory: `pack.json` (manifest) + `workflows/*.json` (definitions) +
`prompts/*.md`. `load_pack` validates every definition against the schema AND against
the manifest (pack key matches, declared connectors are known, workflow is listed).
The workflows service seeds the result into the per-tenant DB registry; tests use the
same loader to prove all shipped packs are valid.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_os_shared.workflow.schema import (
    PackManifest,
    WorkflowDefinition,
    validate_definition,
)


class PackError(ValueError):
    pass


def load_pack(pack_dir: Path) -> tuple[PackManifest, dict[str, WorkflowDefinition]]:
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.exists():
        raise PackError(f"Missing pack.json in {pack_dir}")
    manifest = PackManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))

    definitions: dict[str, WorkflowDefinition] = {}
    for wf_file in sorted((pack_dir / "workflows").glob("*.json")):
        raw = json.loads(wf_file.read_text(encoding="utf-8"))
        try:
            wf = validate_definition(raw)
        except Exception as exc:
            raise PackError(f"{wf_file.name}: {exc}") from exc
        if wf.pack != manifest.key:
            raise PackError(f"{wf_file.name}: pack '{wf.pack}' != manifest '{manifest.key}'")
        unknown = set(wf.connectors_required) - set(manifest.connectors)
        if unknown:
            raise PackError(
                f"{wf_file.name}: connectors not declared in manifest: {sorted(unknown)}"
            )
        definitions[wf.key] = wf

    listed = set(manifest.workflows)
    found = set(definitions)
    if listed and listed != found:
        raise PackError(
            f"{pack_dir.name}: manifest workflows {sorted(listed)} != files {sorted(found)}"
        )
    return manifest, definitions


def load_all_packs(root: Path) -> dict[str, tuple[PackManifest, dict[str, WorkflowDefinition]]]:
    """Load every pack directory under `root` (each with a pack.json)."""
    packs: dict[str, tuple[PackManifest, dict[str, WorkflowDefinition]]] = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "pack.json").exists():
            manifest, defs = load_pack(child)
            packs[manifest.key] = (manifest, defs)
    return packs
