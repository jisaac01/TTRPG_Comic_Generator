#!/usr/bin/env python3
"""Migrate story bible JSON checkpoints to parallel plain-text files.

Finds ``02_5_story_bible.json`` and ``02_6_story_bible_page*.json`` under one
or more directory trees, extracts the ``story_bible`` string field, and writes a
sibling ``.txt`` file with real newlines. Original JSON files are never modified
or deleted.

Dry-run by default; pass ``--write`` to apply.

Default search root is the **user app-data campaigns directory**
(``app_paths.default_campaigns_root()``), not a folder inside the git repo.

Examples::

    # Dry-run entire user campaigns tree (default)
    python scripts/migrate_story_bibles_to_txt.py

    # Dry-run a single version folder
    python scripts/migrate_story_bibles_to_txt.py \\
        "$HOME/Library/Application Support/TTRPG_Comic_Generator/campaigns/flail/<episode>/v023"

    # Dry-run one episode (all versions + working/)
    python scripts/migrate_story_bibles_to_txt.py \\
        "$HOME/Library/Application Support/TTRPG_Comic_Generator/campaigns/flail/<episode>"

    # Apply for that folder only
    python scripts/migrate_story_bibles_to_txt.py PATH/TO/v023 --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from app_paths import default_campaigns_root  # noqa: E402


def story_bible_json_candidates(root: Path) -> list[Path]:
    """Return story-bible JSON paths under *root* (full + page/panel slices).

    If *root* is itself a matching JSON file, return that file only.
    """
    if root.is_file():
        name = root.name
        if name == "02_5_story_bible.json" or (
            name.startswith("02_6_story_bible_page") and name.endswith(".json")
        ):
            return [root]
        return []

    found: list[Path] = []
    for path in root.rglob("02_5_story_bible.json"):
        found.append(path)
    for path in root.rglob("02_6_story_bible_page*.json"):
        found.append(path)
    return sorted(found)


def extract_story_bible_text(json_path: Path) -> str:
    """Load *json_path* and return the non-empty story_bible string field."""
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("root value is not an object")
    text = payload.get("story_bible")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("missing or empty 'story_bible' string field")
    return text.strip()


def migrate_file(json_path: Path, *, write: bool) -> str:
    """Migrate one JSON story bible. Returns a status token for reporting."""
    txt_path = json_path.with_suffix(".txt")
    if txt_path.exists():
        return "skip-exists"
    text = extract_story_bible_text(json_path)
    if write:
        txt_path.write_text(text + "\n", encoding="utf-8")
        return "wrote"
    return "would-write"


def _display_path(path: Path, campaigns_root: Path | None = None) -> str:
    for base in (campaigns_root, ROOT):
        if base is None:
            continue
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_root = default_campaigns_root()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "Folder tree(s) or story-bible JSON file(s) to scan. "
            f"Defaults to the user campaigns root: {default_root}"
        ),
    )
    parser.add_argument(
        "--campaigns-root",
        type=Path,
        action="append",
        default=None,
        help="Extra search root (repeatable). Alias for positional paths.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply migrations (default is dry-run)",
    )
    args = parser.parse_args(argv)

    roots: list[Path] = list(args.paths)
    if args.campaigns_root:
        roots.extend(args.campaigns_root)
    if not roots:
        roots = [default_root]

    counts = {"wrote": 0, "would-write": 0, "skip-exists": 0, "error": 0, "roots": 0}
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            print(f"skip missing path: {_display_path(root, default_root)}")
            continue
        counts["roots"] += 1
        candidates = story_bible_json_candidates(root)
        print(
            f"scanning {_display_path(root, default_root)} "
            f"({len(candidates)} story-bible JSON file(s))"
        )
        if not candidates:
            continue
        for json_path in candidates:
            rel = _display_path(json_path, default_root)
            try:
                status = migrate_file(json_path, write=args.write)
            except ValueError as exc:
                print(f"ERROR {rel}: {exc}")
                counts["error"] += 1
                continue
            print(f"  {status}: {rel} -> {json_path.with_suffix('.txt').name}")
            counts[status] += 1

    mode = "write" if args.write else "dry-run"
    print(
        f"done ({mode}): roots={counts['roots']} "
        f"wrote={counts['wrote']} would-write={counts['would-write']} "
        f"skip-exists={counts['skip-exists']} error={counts['error']}"
    )
    if not args.paths and not args.campaigns_root:
        print(f"default campaigns root: {default_root}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
