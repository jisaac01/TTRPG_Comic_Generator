#!/usr/bin/env python3
"""Migrate campaign-level art_direction_template.json into art_direction/<stem>.json.

Does not touch version directories. Dry-run by default; pass ``--write`` to apply.
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
from art_styles import (  # noqa: E402
    ART_DIRECTION_DIRNAME,
    ART_DIRECTION_TEMPLATE_FILENAME,
    DEFAULT_ART_STYLE_STEM,
    bundled_art_direction_dir,
)


def canonical_hash(obj: dict) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def slug_from_base_style(base_style: str, max_len: int = 40) -> str:
    first = re.split(r"[.!?\n]", base_style.strip(), maxsplit=1)[0]
    first = first.split(",")[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", first).strip("-")
    if not slug:
        slug = "style"
    return slug[:max_len].rstrip("-")


def bundled_hash_to_stem() -> dict[str, str]:
    mapping: dict[str, str] = {}
    directory = bundled_art_direction_dir()
    if not directory.exists():
        return mapping
    for path in directory.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            mapping[canonical_hash(obj)] = path.stem
        except (json.JSONDecodeError, OSError):
            continue
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaigns-root",
        type=Path,
        action="append",
        default=None,
        help=(
            "Campaigns root (repeatable). "
            f"Defaults to user app-data campaigns: {default_campaigns_root()}"
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply migrations (default is dry-run)",
    )
    args = parser.parse_args()

    roots: list[Path] = list(args.campaigns_root or [])
    if not roots:
        roots = [default_campaigns_root()]

    bundled = bundled_hash_to_stem()
    print(f"Bundled styles known: {len(bundled)}")

    for root in roots:
        if not root.exists():
            print(f"SKIP missing root {root}")
            continue
        print(f"\nRoot: {root}")
        for campaign_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            legacy = campaign_dir / ART_DIRECTION_TEMPLATE_FILENAME
            if not legacy.exists():
                continue
            try:
                obj = json.loads(legacy.read_text(encoding="utf-8"))
                h = canonical_hash(obj)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"  SKIP {campaign_dir.name}: {exc}")
                continue

            stem = bundled.get(h)
            if stem is None:
                base = obj.get("base_style", "") if isinstance(obj.get("base_style"), str) else ""
                stem = slug_from_base_style(base) or DEFAULT_ART_STYLE_STEM

            dest_dir = campaign_dir / ART_DIRECTION_DIRNAME
            dest = dest_dir / f"{stem}.json"
            action = "MOVE"
            if dest.exists():
                try:
                    if canonical_hash(json.loads(dest.read_text(encoding="utf-8"))) == h:
                        action = "DROP_LEGACY"  # already migrated
                    else:
                        # avoid overwrite
                        n = 2
                        while (dest_dir / f"{stem}-{n}.json").exists():
                            n += 1
                        dest = dest_dir / f"{stem}-{n}.json"
                        action = "MOVE_AS"
                except (json.JSONDecodeError, OSError):
                    action = "MOVE"

            print(f"  {action} {campaign_dir.name}: {legacy.name} -> art_direction/{dest.name}")
            if args.write:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if action != "DROP_LEGACY":
                    dest.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
                legacy.unlink()

    if not args.write:
        print("\nDry-run only. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
