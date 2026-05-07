from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from prompter import (
    ART_DIRECTION_TEMPLATE_FIELDS,
    _format_art_direction,
    _load_art_template,
)
from prompt_templates import (
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scriptwriter import Panel, ScriptCheckpoint


class StyleIntegrationPartialFailure(ValueError):
    def __init__(self, message: str, checkpoint: ScriptCheckpoint):
        super().__init__(message)
        self.checkpoint = checkpoint


class StyledPanelRewrite(BaseModel):
    index: int = Field(ge=1)
    setting: str = Field(min_length=1)
    visual_action: str = Field(min_length=1)


class StyledScriptPayload(BaseModel):
    panels: list[StyledPanelRewrite]


StyleGenerator = Callable[[ScriptCheckpoint, dict[str, str], str], StyledScriptPayload]


def _format_panels_for_prompt(script: ScriptCheckpoint) -> str:
    panel_payload: list[dict[str, object]] = []
    for panel in script.panels:
        panel_payload.append(
            {
                "index": panel.index,
                "panel_scale": panel.panel_scale,
                "panel_shape": panel.panel_shape,
                "setting": panel.setting,
                "visual_action": panel.visual_action,
                "dialogue_overlay": panel.dialogue_overlay,
                "held_items_before": panel.held_items_before,
                "held_items_after": panel.held_items_after,
                "caption": panel.caption,
                "voiceover": panel.voiceover,
                "chyron": panel.chyron,
                "sound_effects": panel.sound_effects,
            }
        )
    return json.dumps(panel_payload, indent=2, ensure_ascii=False)


def _build_instructor_client():
    import instructor
    from openai import OpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    openai_client = OpenAI(base_url=base_url, api_key=api_key)
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)


def _generate_with_instructor_ollama(
    script: ScriptCheckpoint,
    art_template: dict[str, str],
    model: str,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> StyledScriptPayload:
    client = _build_instructor_client()

    system_prompt = render_prompt_template(
        STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )

    user_prompt = render_prompt_template(
        STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        art_direction=_format_art_direction(art_template),
        panels_context=_format_panels_for_prompt(script),
    )

    return client.chat.completions.create(
        model=model,
        temperature=0.7,
        response_model=StyledScriptPayload,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )


def _validate_styled_panels(
    styled_panels: list[StyledPanelRewrite],
    source_panels: list[Panel],
) -> None:
    expected_indices = [panel.index for panel in source_panels]
    received_indices = [panel.index for panel in styled_panels]

    if received_indices != expected_indices:
        raise ValueError(
            "Styled panels must be returned exactly once and in source order: "
            f"expected={expected_indices}, received={received_indices}."
        )



def _find_unchanged_panel_indices(
    styled_panels: list[StyledPanelRewrite],
    source_panels: list[Panel],
) -> list[int]:
    unchanged_indices: list[int] = []
    for source, styled in zip(source_panels, styled_panels):
        if (
            styled.setting.strip() == source.setting.strip()
            and styled.visual_action.strip() == source.visual_action.strip()
        ):
            unchanged_indices.append(source.index)
    return unchanged_indices


def _normalize_panels_from_source(
    styled_panels: list[StyledPanelRewrite],
    source_panels: list[Panel],
) -> list[Panel]:
    """Rebuild panels, preserving source structure and only replacing styled prose."""
    normalized: list[Panel] = []
    for idx, (source, styled) in enumerate(zip(source_panels, styled_panels), start=1):
        normalized.append(
            Panel(
                index=idx,
                panel_scale=source.panel_scale,
                panel_shape=source.panel_shape,
                setting=styled.setting,
                visual_action=styled.visual_action,
                dialogue_overlay=source.dialogue_overlay,
                held_items_before=source.held_items_before,
                held_items_after=source.held_items_after,
                caption=source.caption,
                voiceover=source.voiceover,
                chyron=source.chyron,
                sound_effects=source.sound_effects,
            )
        )
    return normalized


def integrate_style(
    script_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    art_style_template_path: Path = Path("campaigns/<campaign>/art_direction_template.json"),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_5_styled_script.json"),
    model: str = "qwen2.5:7b",
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    generator: StyleGenerator | None = None,
) -> ScriptCheckpoint:
    script = ScriptCheckpoint.model_validate_json(
        script_checkpoint_path.read_text(encoding="utf-8")
    )
    art_template = _load_art_template(art_style_template_path)
    generation_errors: list[str] = []

    if generator is not None:
        payload = generator(script, art_template, model)
    else:
        payload = _generate_with_instructor_ollama(
            script,
            art_template,
            model,
            system_prompt_path=system_prompt_path,
            user_prompt_path=user_prompt_path,
        )

    try:
        _validate_styled_panels(payload.panels, script.panels)
        styled_panels = payload.panels
    except ValueError as exc:
        generation_errors.append(
            f"Styled panel validation failed: {exc}. Accepting validation failure by using source panel prose."
        )
        styled_panels = [
            StyledPanelRewrite(
                index=panel.index,
                setting=panel.setting,
                visual_action=panel.visual_action,
            )
            for panel in script.panels
        ]

    unchanged_indices = _find_unchanged_panel_indices(styled_panels, script.panels)
    if unchanged_indices:
        generation_errors.append(
            "Style integration left panels unchanged: "
            f"{unchanged_indices}. Accepting unchanged panels."
        )

    panels = _normalize_panels_from_source(styled_panels, script.panels)

    checkpoint = ScriptCheckpoint(
        url=script.url,
        title=script.title,
        author=script.author,
        model=model,
        panel_count=len(panels),
        panels=panels,
        generation_errors=generation_errors,
        scripted_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return checkpoint


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Integrate art style into a comic script checkpoint (Phase 3.5)."
    )
    parser.add_argument(
        "--script-input",
        required=True,
        help="Input script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_script.json)",
    )
    parser.add_argument(
        "--art-style-template",
        required=True,
        help="Art direction template JSON path (e.g. campaigns/<campaign>/art_direction_template.json)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output styled script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_5_styled_script.json)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model name",
    )

    args = parser.parse_args()
    checkpoint = integrate_style(
        script_checkpoint_path=Path(args.script_input),
        art_style_template_path=Path(args.art_style_template),
        output_path=Path(args.output),
        model=args.model,
    )
    print(json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _run_cli()
