from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from art_styles import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    default_art_direction_template_path,
)
from entities import WorldStateCheckpoint, format_character_details
from prompt_templates import (
    PAGE_PROMPT_TEMPLATE_FILENAME,
    render_prompt_template,
)
from scriptwriter import ScriptCheckpoint


DEFAULT_ART_DIRECTION_TEMPLATE_PATH = default_art_direction_template_path()
# Suggested classic fields and pretty labels (not a required allowlist).
ART_DIRECTION_TEMPLATE_FIELDS = (
    ("base_style", "Base Style"),
    ("characters", "Characters"),
    ("color_palette", "Color Palette"),
    ("layout_and_composition", "Layout & Composition"),
    ("lettering_and_dialog", "Lettering & Dialog"),
    ("text_rendering_guide", "Text Rendering Guide"),
)
ART_DIRECTION_FIELD_LABELS = dict(ART_DIRECTION_TEMPLATE_FIELDS)


def _default_art_direction_template_json() -> str:
    return DEFAULT_ART_DIRECTION_TEMPLATE_PATH.read_text(encoding="utf-8").strip()


def _humanize_art_key(field_name: str) -> str:
    return ART_DIRECTION_FIELD_LABELS.get(
        field_name,
        field_name.replace("_", " ").strip().title(),
    )


def _normalize_art_template_object(template: dict, *, source: str) -> dict[str, str]:
    """Normalize a loaded art-direction JSON object: keep every non-empty string key in order."""
    invalid_fields: list[str] = []
    normalized_template: dict[str, str] = {}
    for field_name, value in template.items():
        key = str(field_name)
        if not isinstance(value, str) or not value.strip():
            invalid_fields.append(key)
            continue
        normalized_template[key] = value.strip()

    if invalid_fields:
        raise ValueError(
            "Art direction template fields must be non-empty strings "
            f"at {source}: {', '.join(invalid_fields)}"
        )
    if not normalized_template:
        raise ValueError(
            "Art direction template must contain at least one non-empty string field "
            f"at {source}."
        )
    return normalized_template


def _collect_panel_text(script: ScriptCheckpoint) -> str:
    parts: list[str] = []
    for panel in script.panels:
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


def _character_is_referenced(name: str, panel_text: str) -> bool:
    pattern = r"(?<!\\w)" + re.escape(name) + r"(?!\\w)"
    return re.search(pattern, panel_text, flags=re.IGNORECASE) is not None


def _format_character_details(world: WorldStateCheckpoint, script: ScriptCheckpoint) -> str:
    panel_text = _collect_panel_text(script)
    all_characters = list(world.player_characters) + list(world.npcs)
    details = [
        format_character_details(character)
        for character in all_characters
        if _character_is_referenced(character.name, panel_text)
    ]
    return "\n\n".join(details)


def _load_art_template(art_style_template_path: Path) -> dict[str, str]:
    if not art_style_template_path.exists():
        raise FileNotFoundError(
            "Art direction template file not found at "
            f"{art_style_template_path}. "
            "Create this file before running Phase 4. "
            f"Suggested starter content: {_default_art_direction_template_json()}"
        )

    template_text = art_style_template_path.read_text(encoding="utf-8").strip()
    if not template_text:
        raise ValueError(
            f"Art direction template file is empty at {art_style_template_path}."
        )

    try:
        template = json.loads(template_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Art direction template file is not valid JSON at {art_style_template_path}."
        ) from exc

    if not isinstance(template, dict):
        raise ValueError(
            f"Art direction template file must contain a JSON object at {art_style_template_path}."
        )

    return _normalize_art_template_object(
        template, source=str(art_style_template_path)
    )


def _format_art_direction(template: dict[str, str]) -> str:
    return "\n".join(
        f"{_humanize_art_key(field_name)}: {value}"
        for field_name, value in template.items()
    )


def _resolve_page_number(script: ScriptCheckpoint) -> int:
    page_numbers = sorted({panel.page_number for panel in script.panels})
    if not page_numbers:
        raise ValueError("Script must contain at least one panel to generate a page prompt.")
    if len(page_numbers) != 1:
        raise ValueError(
            "Page prompt generation expects a single-page script checkpoint; "
            f"found page numbers: {page_numbers}."
        )
    return page_numbers[0]


def _is_panel_prompt(output_path: Path | None) -> bool:
    return output_path is not None and "_panel_" in output_path.name


def _format_output_goal(generation_mode: str = "page") -> str:
    if generation_mode == "panel":
        return "one single comic panel image showing only the specified panel below."
    return "one single comic page image containing all panels below in order."


def _format_page_elements_instruction(title: str, page_number: int, generation_mode: str = "page") -> str:
    if generation_mode == "panel":
        return (
            "Panel framing: When generating a single panel image (or the content of any individual panel), "
            "the depicted scene must completely fill the image frame with zero external padding, margins, "
            "whitespace, or borders around the content. All visual elements, characters, setting, and action "
            "must extend fully to the four edges of the generated image (tight crop / full-bleed within the "
            "panel rectangle). Do not introduce vignette, extra space, or decorative framing that shrinks the active content area."
        )
    if page_number == 1:
        return f'Page elements: Include the title "{title}" on the page.'
    return f"Page elements: Include page number {page_number} at the bottom of the page."


def _format_panel_block(script: ScriptCheckpoint) -> str:
    panel_lines: list[str] = []
    for panel in script.panels:
        dialogue = (
            " | ".join(panel.dialogue_overlay)
            if panel.dialogue_overlay
            else "None"
        )
        characters = (
            ", ".join(panel.characters)
            if panel.characters
            else "None"
        )
        panel_content = [
            f"Panel {panel.index}:",
            f"- Summary: {panel.summary or 'None'}",
            f"- Characters: {characters}",
            f"- Camera Framing: {panel.camera_framing or 'None'}",
            f"- Panel Scale: {panel.panel_scale}",
            f"- Panel Shape: {panel.panel_shape}",
            f"- Setting: {panel.setting}",
            f"- Visual Action: {panel.visual_action}",
            f"- Dialogue Overlay: {dialogue}",
        ]

        if panel.narrative_overlays_and_text_direction:
            overlays_text = " | ".join(panel.narrative_overlays_and_text_direction)
            panel_content.append(
                f"- Narrative Overlays & Text Direction: {overlays_text}"
            )

        panel_lines.append("\n".join(panel_content))

    return "\n\n".join(panel_lines)


def generate_page_prompt(
    script_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    entities_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_entities.json"),
    art_style_template_path: Path = Path(
        "campaigns/<campaign>/art_direction/<style>.json"
    ),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/04_page_1_prompt.txt"),
    page_prompt_template_path: Path | None = None,
    aspect_ratio: str = "3:2",
) -> str:
    script = ScriptCheckpoint.model_validate_json(
        script_checkpoint_path.read_text(encoding="utf-8")
    )
    world = WorldStateCheckpoint.model_validate_json(
        entities_checkpoint_path.read_text(encoding="utf-8")
    )

    art_direction_template = _load_art_template(art_style_template_path)
    title = script.title or world.title or "Untitled story"
    page_number = _resolve_page_number(script)
    generation_mode = "panel" if _is_panel_prompt(output_path) else "page"
    character_details = _format_character_details(world, script)
    panel_block = _format_panel_block(script)

    prompt_text = render_prompt_template(
        PAGE_PROMPT_TEMPLATE_FILENAME,
        template_path=page_prompt_template_path,
        title=title,
        art_direction=_format_art_direction(art_direction_template),
        character_details=character_details,
        output_goal=_format_output_goal(generation_mode),
        page_elements_instruction=_format_page_elements_instruction(title, page_number, generation_mode),
        panel_count=script.panel_count,
        aspect_ratio=aspect_ratio,
        panel_block=panel_block,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")
    return prompt_text


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate image prompts from script panels and a reusable art direction JSON template."
    )
    parser.add_argument(
        "--script-input",
        required=True,
        help="Input script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_script.json)",
    )
    parser.add_argument(
        "--entities-input",
        required=True,
        help="Input entities checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/02_entities.json)",
    )
    parser.add_argument(
        "--art-style-template",
        required=True,
        help=(
            "Path to the reusable art direction template JSON file "
            "(e.g. src/prompts/art_direction/brutalist.json)"
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output page prompt text file path (e.g. campaigns/<campaign>/<episode>/v001/04_page_1_prompt.txt)",
    )
    parser.add_argument(
        "--page-prompt-template",
        default=None,
        help="Explicit path to the page prompt template file.",
    )

    args = parser.parse_args()
    prompt_text = generate_page_prompt(
        script_checkpoint_path=Path(args.script_input),
        entities_checkpoint_path=Path(args.entities_input),
        art_style_template_path=Path(args.art_style_template),
        output_path=Path(args.output),
        page_prompt_template_path=Path(args.page_prompt_template)
        if args.page_prompt_template
        else None,
    )
    print(f"Saved page prompt ({len(prompt_text)} chars) to {args.output}")


if __name__ == "__main__":
    _run_cli()
