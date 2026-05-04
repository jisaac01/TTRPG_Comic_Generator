from __future__ import annotations

import argparse
from pathlib import Path

from analyzer import WorldStateCheckpoint
from scriptwriter import ScriptCheckpoint


DEFAULT_ART_STYLE_TEMPLATE = (
    "Base Style: Brutalist, hand-inked graphic novel aesthetic. "
    "High contrast, Gothic shadows, heavy ink washes, grainy texture. "
    "No colors, black and white only."
)


def _format_character_details(world: WorldStateCheckpoint) -> str:
    details = [
        f"{character.name}: {character.description}; demeanor={character.demeanor}"
        for character in world.characters
    ]
    return " | ".join(details)


def _load_art_template(art_style_template_path: Path) -> str:
    if not art_style_template_path.exists():
        raise FileNotFoundError(
            "Art direction template file not found at "
            f"{art_style_template_path}. "
            "Create this file before running Phase 4. "
            f"Suggested starter content: {DEFAULT_ART_STYLE_TEMPLATE}"
        )

    template = art_style_template_path.read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError(
            f"Art direction template file is empty at {art_style_template_path}."
        )
    return template


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
    art_style_template_path: Path = Path("campaigns/<campaign>/art_direction_template.txt"),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/04_page_prompt.txt"),
) -> str:
    script = ScriptCheckpoint.model_validate_json(
        script_checkpoint_path.read_text(encoding="utf-8")
    )
    world = WorldStateCheckpoint.model_validate_json(
        entities_checkpoint_path.read_text(encoding="utf-8")
    )

    art_style_template = _load_art_template(art_style_template_path)
    character_details = _format_character_details(world)
    panel_block = _format_panel_block(script)

    prompt_text = (
        f"{art_style_template}\n\n"
        "Output goal: one single comic page image containing all panels below in order.\n"
        "Layout: keep every panel visible in one page composition, clear gutters, consistent character design across panels.\n"
        "Rendering constraints: black-and-white only, no color.\n"
        "Character details (apply to every panel): "
        f"{character_details}\n\n"
        f"Panel count: {script.panel_count}\n"
        "Panel specifications:\n"
        f"{panel_block}\n\n"
        "Final format: single comic page image."
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")
    return prompt_text


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate image prompts from script panels and a reusable art template."
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
        help="Path to the reusable art direction template file (e.g. campaigns/<campaign>/art_direction_template.txt)",
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
