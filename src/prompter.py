from __future__ import annotations

import argparse
import json
from pathlib import Path

from entities import WorldStateCheckpoint
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    render_prompt_template,
)
from scriptwriter import ScriptCheckpoint


ART_DIRECTION_TEMPLATE_FILENAME = "art_direction_template.json"
DEFAULT_ART_DIRECTION_TEMPLATE_PATH = (
    DEFAULT_PROMPTS_DIR / ART_DIRECTION_TEMPLATE_FILENAME
)
ART_DIRECTION_TEMPLATE_FIELDS = (
    ("base_style", "Base Style"),
    ("color_palette", "Color Palette"),
    ("layout_and_composition", "Layout & Composition"),
    ("lettering_and_dialog", "Lettering & Dialog"),
)


def _default_art_direction_template_json() -> str:
    return DEFAULT_ART_DIRECTION_TEMPLATE_PATH.read_text(encoding="utf-8").strip()


def _format_character_details(world: WorldStateCheckpoint) -> str:
    details = [
        f"{character.name}: {character.description}"
        for character in world.characters
    ]
    return " | ".join(details)


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

    normalized_template: dict[str, str] = {}
    missing_fields: list[str] = []
    for field_name, field_label in ART_DIRECTION_TEMPLATE_FIELDS:
        value = template.get(field_name)
        if not isinstance(value, str) or not value.strip():
            missing_fields.append(f"{field_name} ({field_label})")
            continue
        normalized_template[field_name] = value.strip()

    if missing_fields:
        raise ValueError(
            "Art direction template file is missing required non-empty string fields "
            f"at {art_style_template_path}: {', '.join(missing_fields)}"
        )

    return normalized_template


def _format_art_direction(template: dict[str, str]) -> str:
    return "\n".join(
        f"{field_label}: {template[field_name]}"
        for field_name, field_label in ART_DIRECTION_TEMPLATE_FIELDS
    )


def _format_panel_block(script: ScriptCheckpoint) -> str:
    panel_lines: list[str] = []
    for panel in script.panels:
        dialogue = (
            " | ".join(panel.dialogue_overlay)
            if panel.dialogue_overlay
            else "None"
        )
        panel_lines.append(
            "\n".join(
                [
                    f"Panel {panel.index}:",
                    f"- Panel Scale: {panel.panel_scale}",
                    f"- Panel Shape: {panel.panel_shape}",
                    f"- Setting: {panel.setting}",
                    f"- Visual Action: {panel.visual_action}",
                    f"- Dialogue Overlay: {dialogue}",
                ]
            )
        )

    return "\n\n".join(panel_lines)


def generate_page_prompt(
    script_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    entities_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_entities.json"),
    art_style_template_path: Path = Path(
        f"campaigns/<campaign>/{ART_DIRECTION_TEMPLATE_FILENAME}"
    ),
    page_prompt_template_path: Path | None = None,
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/04_page_prompt.txt"),
) -> str:
    script = ScriptCheckpoint.model_validate_json(
        script_checkpoint_path.read_text(encoding="utf-8")
    )
    world = WorldStateCheckpoint.model_validate_json(
        entities_checkpoint_path.read_text(encoding="utf-8")
    )

    art_direction_template = _load_art_template(art_style_template_path)
    character_details = _format_character_details(world)
    panel_block = _format_panel_block(script)

    prompt_text = render_prompt_template(
        PAGE_PROMPT_TEMPLATE_FILENAME,
        template_path=page_prompt_template_path,
        art_direction=_format_art_direction(art_direction_template),
        character_details=character_details,
        panel_count=script.panel_count,
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
            f"(e.g. campaigns/<campaign>/{ART_DIRECTION_TEMPLATE_FILENAME})"
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output page prompt text file path (e.g. campaigns/<campaign>/<episode>/v001/04_page_prompt.txt)",
    )

    args = parser.parse_args()
    prompt_text = generate_page_prompt(
        script_checkpoint_path=Path(args.script_input),
        entities_checkpoint_path=Path(args.entities_input),
        art_style_template_path=Path(args.art_style_template),
        output_path=Path(args.output),
    )
    print(f"Saved page prompt ({len(prompt_text)} chars) to {args.output}")


if __name__ == "__main__":
    _run_cli()
