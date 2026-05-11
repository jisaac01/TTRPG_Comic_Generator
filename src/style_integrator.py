from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from llm_client import build_instructor_client
from model_defaults import DEFAULT_MODEL
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
from scriptwriter import Page, Panel, ScriptCheckpoint


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
                "narrative_overlays_and_text_direction": panel.narrative_overlays_and_text_direction,
            }
        )
    return json.dumps(panel_payload, indent=2, ensure_ascii=False)


def _build_instructor_client(model: str):
    return build_instructor_client(model)


def _generate_with_instructor_ollama(
    script: ScriptCheckpoint,
    art_template: dict[str, str],
    model: str,
    system_prompt_text: str,
    user_prompt_text: str,
) -> StyledScriptPayload:
    client = _build_instructor_client(model)
    system_prompt = system_prompt_text
    user_prompt = user_prompt_text

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
    for source, styled in zip(source_panels, styled_panels):
        normalized.append(
            Panel(
                index=source.index,
                page_number=source.page_number,
                panel_scale=source.panel_scale,
                panel_shape=source.panel_shape,
                setting=styled.setting,
                visual_action=styled.visual_action,
                dialogue_overlay=source.dialogue_overlay,
                held_items_before=source.held_items_before,
                held_items_after=source.held_items_after,
                narrative_overlays_and_text_direction=source.narrative_overlays_and_text_direction,
            )
        )
    return normalized


def _rebuild_pages_from_panels(source_script: ScriptCheckpoint, panels: list[Panel]) -> list[Page]:
    panels_by_page = {
        page.page_number: [panel for panel in panels if panel.page_number == page.page_number]
        for page in source_script.pages
    }
    return [
        Page(
            page_number=page.page_number,
            panel_count=len(panels_by_page[page.page_number]),
            panels=panels_by_page[page.page_number],
        )
        for page in source_script.pages
    ]


def integrate_style(
    script_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    art_style_template_path: Path = Path("campaigns/<campaign>/art_direction_template.json"),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_5_styled_script.json"),
    *,
    system_prompt_text: str,
    user_prompt_text: str,
    model: str = DEFAULT_MODEL,
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
            system_prompt_text=system_prompt_text,
            user_prompt_text=user_prompt_text,
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
    pages = _rebuild_pages_from_panels(script, panels)

    checkpoint = ScriptCheckpoint(
        url=script.url,
        title=script.title,
        author=script.author,
        model=model,
        panel_count=len(panels),
        total_pages=script.total_pages,
        pages=pages,
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
        default=DEFAULT_MODEL,
        help="Ollama model name",
    )

    args = parser.parse_args()
    script = ScriptCheckpoint.model_validate_json(Path(args.script_input).read_text(encoding="utf-8"))
    art_template = _load_art_template(Path(args.art_style_template))
    system_prompt_text = render_prompt_template(
        STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    )
    user_prompt_text = render_prompt_template(
        STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
        art_direction=_format_art_direction(art_template),
        panels_context=_format_panels_for_prompt(script),
    )

    checkpoint = integrate_style(
        script_checkpoint_path=Path(args.script_input),
        art_style_template_path=Path(args.art_style_template),
        output_path=Path(args.output),
        model=args.model,
        system_prompt_text=system_prompt_text,
        user_prompt_text=user_prompt_text,
    )
    print(json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _run_cli()
