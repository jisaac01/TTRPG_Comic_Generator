"""Campaign character-reference images attached to Gemini image requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from entities import EPISODE_ENTITIES_FILENAME, Character, WorldStateCheckpoint
from model_catalog import input_image_cap
from prompter import _character_reference_phrases
from scriptwriter import Panel, ScriptCheckpoint

CHARACTERS_DIRNAME = "characters"
PNG_SUFFIX = ".png"
# Gemini generateContent native image MIME types:
# https://ai.google.dev/gemini-api/docs/vision#supported-image-formats
IMAGE_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
_SUFFIX_PREFERENCE = {
    ".png": 0,
    ".jpeg": 1,
    ".jpg": 2,
    ".webp": 3,
    ".heic": 4,
    ".heif": 5,
}


@dataclass(frozen=True)
class CharacterReference:
    name: str
    slug: str
    path: Path

    @property
    def mime_type(self) -> str:
        return mime_type_for_image(self.path)


def character_filename(name: str) -> str:
    return f"{name.strip().replace(' ', '_')}{PNG_SUFFIX}"


def mime_type_for_image(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    mime = IMAGE_SUFFIX_MIME.get(suffix)
    if mime is None:
        raise ValueError(f"Unsupported character reference image format: {path}")
    return mime


def scan_character_library(campaign_root: Path) -> dict[str, Path]:
    folder = Path(campaign_root) / CHARACTERS_DIRNAME
    if not folder.is_dir():
        return {}
    library: dict[str, Path] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in IMAGE_SUFFIX_MIME:
            continue
        key = path.stem.casefold()
        existing = library.get(key)
        if existing is None or _SUFFIX_PREFERENCE[suffix] < _SUFFIX_PREFERENCE[existing.suffix.lower()]:
            library[key] = path
    return library


def lookup_character_image(character: Character, library: dict[str, Path]) -> Path | None:
    for label in (character.name, *(character.aliases or [])):
        key = label.strip().replace(" ", "_").casefold()
        path = library.get(key)
        if path is not None:
            return path
    return None


def count_character_mentions(character: Character, text: str) -> int:
    phrases = _character_reference_phrases(character)
    if not phrases or not text:
        return 0
    ordered = sorted(phrases, key=len, reverse=True)
    pattern = "|".join(rf"(?<!\w){re.escape(phrase)}(?!\w)" for phrase in ordered)
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _collect_panels_text(panels: list[Panel]) -> str:
    parts: list[str] = []
    for panel in panels:
        parts.append(panel.summary)
        parts.extend(panel.characters)
        parts.append(panel.camera_framing)
        parts.append(panel.setting)
        parts.append(panel.visual_action)
        parts.extend(panel.dialogue_overlay)
        parts.extend(panel.held_items_before.keys())
        parts.extend(panel.held_items_after.keys())
        parts.extend(panel.narrative_overlays_and_text_direction)
    return " ".join(parts)


def _panels_for_scope(
    script: ScriptCheckpoint, page_number: int, panel_number: int | None
) -> list[Panel]:
    panels = [
        panel
        for page in script.pages
        if page.page_number == page_number
        for panel in page.panels
    ]
    if panel_number is None:
        return panels
    return [panel for panel in panels if panel.index == panel_number]


def _load_episode_entities(version_dir: Path) -> WorldStateCheckpoint | None:
    path = version_dir / EPISODE_ENTITIES_FILENAME
    if not path.exists():
        return None
    return WorldStateCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


def _load_script_page(version_dir: Path, page_number: int) -> ScriptCheckpoint | None:
    styled = version_dir / f"03_5_styled_script_page_{page_number:03d}.json"
    raw = version_dir / f"03_script_page_{page_number:03d}.json"
    path = styled if styled.exists() else raw
    if not path.exists():
        return None
    return ScriptCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


def select_character_reference_images(
    *,
    campaign_root: Path | None,
    version_dir: Path,
    page_number: int,
    panel_number: int | None,
    model: str,
) -> list[CharacterReference]:
    if campaign_root is None:
        return []
    library = scan_character_library(campaign_root)
    if not library:
        return []
    world = _load_episode_entities(version_dir)
    script = _load_script_page(version_dir, page_number)
    if world is None or script is None:
        return []
    panels = _panels_for_scope(script, page_number, panel_number)
    if not panels:
        return []
    text = _collect_panels_text(panels)
    cap = input_image_cap(model)

    ranked: list[tuple[int, int, int, Character, Path]] = []
    for role_rank, characters in (
        (0, world.player_characters),
        (1, world.npcs),
    ):
        for index, character in enumerate(characters):
            path = lookup_character_image(character, library)
            if path is None:
                continue
            count = count_character_mentions(character, text)
            if count <= 0:
                continue
            ranked.append((count, role_rank, index, character, path))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected: list[CharacterReference] = []
    seen_paths: set[Path] = set()
    for _count, _role, _index, character, path in ranked:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        selected.append(
            CharacterReference(name=character.name, slug=path.stem, path=path)
        )
        if len(selected) >= cap:
            break
    return selected
