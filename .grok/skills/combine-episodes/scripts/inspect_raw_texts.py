#!/usr/bin/env python3
"""Inspect and mechanically concatenate 01_raw_text.json episode files.

Prints an overlap report for entity lists (PCs, NPCs, items, locations,
factions, quests). Optionally writes concatenated recap/quote/outline fields
to a skeleton JSON. Entity lists are left empty — the agent must merge those.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


ENTITY_FIELDS = (
    "player_characters",
    "npcs",
    "items",
    "locations",
    "factions",
    "quests",
)
VARIANT_KEYS = ("standard", "long", "short", "alternate")


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _join_recaps(titles: list[str], texts: list[str]) -> str:
    parts: list[str] = []
    for title, text in zip(titles, texts):
        body = (text or "").strip()
        if not body:
            continue
        heading = (title or "").strip()
        parts.append(f"{heading}\n\n{body}" if heading else body)
    return "\n\n".join(parts)


def _report_entities(episodes: list[dict]) -> None:
    for field in ENTITY_FIELDS:
        by_name: dict[str, list[tuple[int, str, str | None]]] = defaultdict(list)
        for i, ep in enumerate(episodes, 1):
            for item in ep.get(field) or []:
                name = item.get("name") or ""
                by_name[_normalize_name(name)].append(
                    (i, name, item.get("description"))
                )
        print(f"\n=== {field} ===")
        if not by_name:
            print("(none)")
            continue
        for key, entries in by_name.items():
            names = sorted({name for _, name, _ in entries})
            eps = [ep for ep, _, _ in entries]
            descs = [desc for _, _, desc in entries]
            unique = len({d or "" for d in descs})
            label = names[0] if len(names) == 1 else f"{names[0]} (also {', '.join(names[1:])})"
            print(f"\n--- {label}  eps={eps}  unique_descs={unique} ---")
            for ep, name, desc in entries:
                shown_name = "" if name == label.split(" (also")[0] else f" as {name!r}"
                print(f"  [ep{ep}]{shown_name} {desc}")


def _concat_quotes(episodes: list[dict]) -> list[dict]:
    quotes: list[dict] = []
    seen: set[tuple[str, str | None]] = set()
    for ep in episodes:
        for quote in ep.get("quotes") or []:
            sig = (quote.get("text") or "", quote.get("attribution"))
            if sig in seen:
                continue
            seen.add(sig)
            quotes.append(quote)
    return quotes


def _concat_outline(episodes: list[dict]) -> list[str]:
    outline: list[str] = []
    for ep in episodes:
        outline.extend(ep.get("outline") or [])
    return outline


def _skeleton(episodes: list[dict], *, title: str, url: str) -> dict:
    titles = [ep.get("title") or f"Episode {i}" for i, ep in enumerate(episodes, 1)]
    recap_variants = {
        key: _join_recaps(titles, [ep.get("recap_variants", {}).get(key, "") for ep in episodes])
        for key in VARIANT_KEYS
    }
    source_selector = next(
        (ep.get("source_selector") for ep in episodes if ep.get("source_selector")),
        "div.mt-3 div.text-left.text-sm",
    )
    scraped_at = next(
        (ep.get("scraped_at") for ep in reversed(episodes) if ep.get("scraped_at")),
        "",
    )
    return {
        "url": url,
        "title": title,
        "author": None,
        "content": _join_recaps(titles, [ep.get("content") or "" for ep in episodes]),
        "recap_variants": recap_variants,
        "selected_recap": "standard",
        "source_selector": source_selector,
        "scraped_at": scraped_at or "1970-01-01T00:00:00+00:00",
        "quotes": _concat_quotes(episodes),
        "outline": _concat_outline(episodes),
        "npcs": [],
        "items": [],
        "locations": [],
        "factions": [],
        "quests": [],
        "player_characters": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="01_raw_text.json files in episode order")
    parser.add_argument("--title", default="", help="Combined title (skeleton only)")
    parser.add_argument("--url", default="", help="Dummy URL (skeleton only)")
    parser.add_argument(
        "--skeleton",
        type=Path,
        help="Write concatenated recap/quote/outline JSON with empty entity lists",
    )
    args = parser.parse_args()

    episodes = []
    for path in args.inputs:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 1
        episodes.append(_load(path))

    print(f"episodes: {len(episodes)}")
    for i, (path, ep) in enumerate(zip(args.inputs, episodes), 1):
        variants = list((ep.get("recap_variants") or {}).keys())
        print(
            f"  {i}. {path.name}  title={ep.get('title')!r}  "
            f"selected={ep.get('selected_recap')!r}  "
            f"quotes={len(ep.get('quotes') or [])}  "
            f"outline={len(ep.get('outline') or [])}  "
            f"variants={variants}"
        )
    _report_entities(episodes)

    if args.skeleton is not None:
        title = args.title.strip() or (episodes[0].get("title") or "Combined Recap")
        url = args.url.strip() or "https://no-scrape.invalid/combined"
        payload = _skeleton(episodes, title=title, url=url)
        args.skeleton.parent.mkdir(parents=True, exist_ok=True)
        args.skeleton.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote skeleton {args.skeleton}")
        print(
            "entity lists are empty; merge from the report above before validating"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
