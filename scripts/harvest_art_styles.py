#!/usr/bin/env python3
"""Harvest distinct art direction templates into src/prompts/art_direction/.

Scans campaign and version ``art_direction_template.json`` files (and any
``art_direction/*.json``) under one or more campaigns roots. Dedupes by
canonical JSON content hash and writes new named style files.

Dry-run by default; pass ``--write`` to create files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from app_paths import default_campaigns_root  # noqa: E402
from art_styles import ART_DIRECTION_DIRNAME, DEFAULT_ART_STYLE_STEM  # noqa: E402


def canonical_hash(data: bytes | str | dict) -> str:
    if isinstance(data, dict):
        obj = data
    else:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("art style must be a JSON object")
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def slug_from_base_style(base_style: str, max_len: int = 40) -> str:
    first = re.split(r"[.!?\n]", base_style.strip(), maxsplit=1)[0]
    first = first.split(",")[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", first).strip("-")
    if not slug:
        slug = "style"
    return slug[:max_len].rstrip("-")


def unique_slug(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    name = f"{base}-{n}"
    used.add(name)
    return name


def load_existing_styles(target_dir: Path) -> dict[str, Path]:
    """Map content hash -> existing file path."""
    found: dict[str, Path] = {}
    if not target_dir.exists():
        return found
    for path in sorted(target_dir.glob("*.json")):
        if path.stem.startswith("_"):
            continue
        try:
            h = canonical_hash(path.read_bytes())
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        found[h] = path
    return found


def iter_art_files(campaigns_root: Path, *, campaign_level_only: bool) -> list[Path]:
    if not campaigns_root.exists():
        return []
    paths: list[Path] = []
    # Named library files under campaign art_direction/
    paths.extend(sorted(campaigns_root.glob(f"*/{ART_DIRECTION_DIRNAME}/*.json")))
    # Legacy / version snapshot filenames
    if campaign_level_only:
        paths.extend(sorted(campaigns_root.glob("*/art_direction_template.json")))
    else:
        paths.extend(sorted(campaigns_root.rglob("art_direction_template.json")))
    # Dedupe paths
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p.stem.startswith("_"):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaigns-root",
        action="append",
        type=Path,
        default=None,
        help="Campaigns root to scan (repeatable). Defaults to user app-data campaigns root.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=SRC / "prompts" / ART_DIRECTION_DIRNAME,
        help="Output directory for harvested styles",
    )
    parser.add_argument(
        "--campaign-level-only",
        action="store_true",
        help="Only scan campaign-level art_direction_template.json (skip version trees)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write new style files (default is dry-run)",
    )
    args = parser.parse_args()

    roots: list[Path] = list(args.campaigns_root or [])
    if not roots:
        roots = [default_campaigns_root()]

    target: Path = args.target
    existing = load_existing_styles(target)
    used_slugs = {p.stem for p in existing.values()}
    # Ensure default name is reserved if present
    if (target / f"{DEFAULT_ART_STYLE_STEM}.json").exists():
        used_slugs.add(DEFAULT_ART_STYLE_STEM)

    # hash -> list of source paths
    groups: dict[str, list[Path]] = {}
    objects: dict[str, dict] = {}
    for root in roots:
        for path in iter_art_files(root, campaign_level_only=args.campaign_level_only):
            try:
                raw = path.read_text(encoding="utf-8")
                obj = json.loads(raw)
                h = canonical_hash(obj)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                print(f"SKIP unreadable {path}: {exc}")
                continue
            groups.setdefault(h, []).append(path)
            objects[h] = obj

    print(f"Target: {target}")
    print(f"Existing styles: {len(existing)}")
    print(f"Distinct hashes scanned: {len(groups)}")
    print()

    new_count = 0
    for h, sources in sorted(groups.items(), key=lambda item: -len(item[1])):
        if h in existing:
            print(f"KEEP  {existing[h].name}  hash={h[:12]}  n={len(sources)}")
            continue
        obj = objects[h]
        base = obj.get("base_style", "") if isinstance(obj.get("base_style"), str) else ""
        slug = unique_slug(slug_from_base_style(base), used_slugs)
        out = target / f"{slug}.json"
        sample = sources[0]
        preview = (base[:72] + "…") if len(base) > 72 else base
        print(f"NEW   {slug}.json  hash={h[:12]}  n={len(sources)}")
        print(f"      base={preview!r}")
        print(f"      e.g. {sample}")
        if len(sources) > 1:
            for extra in sources[1:4]:
                print(f"           {extra}")
            if len(sources) > 4:
                print(f"           … +{len(sources) - 4} more")
        if args.write:
            target.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
            new_count += 1
        else:
            new_count += 1

    mode = "wrote" if args.write else "would write"
    print()
    print(f"Done: {mode} {new_count} new style(s).")
    if not args.write and new_count:
        print("Re-run with --write to create files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
