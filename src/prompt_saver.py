"""Utilities for saving interpolated prompts to version directories for inspection."""

from __future__ import annotations

from pathlib import Path

from entities import WorldStateCheckpoint
from prompt_templates import (
    ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME,
    ENTITIES_CONTINUITY_USER_PROMPT_FILENAME,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
    render_prompt_template,
    resolve_prompt_template_path,
)
from scriptwriter import ScriptCheckpoint, WorldStateInput
from story_architect import StoryBibleCheckpoint


PROMPTS_SUBDIR_NAME = "prompts"


def _ensure_prompts_dir(version_dir: Path) -> Path:
    """Create the prompts subdirectory in a version directory if it doesn't exist."""
    prompts_dir = version_dir / PROMPTS_SUBDIR_NAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    return prompts_dir


def _save_prompt_template(
    prompts_dir: Path,
    template_path: Path | None,
    template_filename: str,
) -> None:
    """Copy the original template file to the prompts directory."""
    if template_path is None or not template_path.exists():
        return
    
    target_path = prompts_dir / template_filename
    template_path.read_bytes()  # Verify it exists
    target_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")


def _render_prompt_template_checked(
    template_filename: str,
    template_path: Path | None,
    **values: str | int,
) -> str:
    """Render prompt templates with explicit missing-variable diagnostics."""
    try:
        return render_prompt_template(
            template_filename,
            template_path=template_path,
            **values,
        )
    except KeyError as exc:
        missing = exc.args[0] if exc.args else "unknown"
        template_file = resolve_prompt_template_path(
            name=template_filename,
            template_path=template_path,
        )
        raise ValueError(
            f"Prompt template variable mismatch in {template_file}: "
            f"missing placeholder {{{missing}}}."
        ) from exc


def _final_prompt_filename(template_filename: str) -> str:
    """Build the audit-trail FINAL name for a template file (e.g. foo.txt → foo_FINAL.txt)."""
    stem = template_filename.removesuffix(".txt")
    return f"{stem}_FINAL.txt"


def prepare_architect_prompts(
    version_dir: Path,
    content: str,
    world: WorldStateCheckpoint,
    scene_count: int,
    raw_quotes: list[dict[str, str | None]] | None = None,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> tuple[str, str]:
    """Prepare and save story architect prompts before model call.

    FINAL filenames follow the active template names (standard or vignette).

    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    from story_architect import (
        _format_entities_for_prompt,
        _format_quotes_for_prompt,
        _normalize_aliases_in_text,
    )

    prompts_dir = _ensure_prompts_dir(version_dir)
    system_filename = (
        system_prompt_path.name
        if system_prompt_path is not None
        else STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME
    )
    user_filename = (
        user_prompt_path.name
        if user_prompt_path is not None
        else STORY_ARCHITECT_USER_PROMPT_FILENAME
    )

    # Save original templates under their true names (incl. vignette).
    _save_prompt_template(prompts_dir, system_prompt_path, system_filename)
    _save_prompt_template(prompts_dir, user_prompt_path, user_filename)

    # Normalize STT/spelling aliases to canonical names in every free-text
    # slot the model sees (story, entity prose, quote attributions). These
    # rendered strings are also what the pipeline sends to the LLM.
    template_vars = {
        "title": world.title or "Untitled story",
        "panel_count": scene_count,
        "scene_count": scene_count,
        "entities_context": _normalize_aliases_in_text(
            _format_entities_for_prompt(world), world
        ),
        "reference_quotes": _normalize_aliases_in_text(
            _format_quotes_for_prompt(raw_quotes), world
        ),
        "story_text": _normalize_aliases_in_text(content, world),
    }

    # Render prompts
    system_prompt = _render_prompt_template_checked(
        system_filename,
        template_path=system_prompt_path,
        **template_vars,
    )
    user_prompt = _render_prompt_template_checked(
        user_filename,
        template_path=user_prompt_path,
        **template_vars,
    )

    # Save interpolated versions named after the active templates.
    (prompts_dir / _final_prompt_filename(system_filename)).write_text(
        system_prompt, encoding="utf-8"
    )
    (prompts_dir / _final_prompt_filename(user_filename)).write_text(
        user_prompt, encoding="utf-8"
    )

    return system_prompt, user_prompt


def prepare_entities_continuity_prompts(
    version_dir: Path,
    existing: WorldStateCheckpoint,
    incoming: WorldStateCheckpoint,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> tuple[str, str]:
    """Prepare and save entities continuity merge prompts before model call.

    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    import json

    prompts_dir = _ensure_prompts_dir(version_dir)

    _save_prompt_template(
        prompts_dir, system_prompt_path, ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME
    )
    _save_prompt_template(
        prompts_dir, user_prompt_path, ENTITIES_CONTINUITY_USER_PROMPT_FILENAME
    )

    system_prompt = _render_prompt_template_checked(
        ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )
    user_prompt = _render_prompt_template_checked(
        ENTITIES_CONTINUITY_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        existing_entities_json=json.dumps(
            existing.model_dump(mode="json"), indent=2, ensure_ascii=False
        ),
        incoming_entities_json=json.dumps(
            incoming.model_dump(mode="json"), indent=2, ensure_ascii=False
        ),
    )

    (
        prompts_dir / _final_prompt_filename(ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME)
    ).write_text(system_prompt, encoding="utf-8")
    (
        prompts_dir / _final_prompt_filename(ENTITIES_CONTINUITY_USER_PROMPT_FILENAME)
    ).write_text(user_prompt, encoding="utf-8")

    return system_prompt, user_prompt


def prepare_scriptwriter_prompts(
    version_dir: Path,
    world: WorldStateInput,
    story_bible: StoryBibleCheckpoint,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    *,
    page_number: int,
    output_suffix: str,
) -> tuple[str, str]:
    """Prepare and save scriptwriter prompts before model call.
    
    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    from story_architect import _normalize_aliases_in_text
    from scriptwriter import _format_entities_for_prompt, _format_story_bible_for_prompt

    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original templates
    _save_prompt_template(prompts_dir, system_prompt_path, SCRIPTWRITER_SYSTEM_PROMPT_FILENAME)
    _save_prompt_template(prompts_dir, user_prompt_path, SCRIPTWRITER_USER_PROMPT_FILENAME)

    title = world.title or "Untitled story"
    # Canonicalize aliases so entity prose / bible text cannot reintroduce STT names.
    entities_context = _normalize_aliases_in_text(
        _format_entities_for_prompt(world), world
    )
    story_architecture = _normalize_aliases_in_text(
        _format_story_bible_for_prompt(story_bible), world
    )
    first_page_panel_1_narration_directive = (
        "For page 1 only: Include a CAPTION narration entry in "
        "narrative_overlays_and_text_direction for panel index 1 to quickly bring readers up to speed. "
        "Do not apply this requirement to any other panel."
        if page_number == 1
        else ""
    )

    system_prompt = _render_prompt_template_checked(
        SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )
    user_prompt = _render_prompt_template_checked(
        SCRIPTWRITER_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        title=title,
        panel_count=story_bible.scene_count,
        entities_context=entities_context,
        story_architecture=story_architecture,
        first_page_panel_1_narration_directive=first_page_panel_1_narration_directive,
    )

    system_final_stem = (
        f"{SCRIPTWRITER_SYSTEM_PROMPT_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )
    user_final_stem = (
        f"{SCRIPTWRITER_USER_PROMPT_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )

    (prompts_dir / f"{system_final_stem}.txt").write_text(system_prompt, encoding="utf-8")
    (prompts_dir / f"{user_final_stem}.txt").write_text(user_prompt, encoding="utf-8")

    return system_prompt, user_prompt


def prepare_style_integrator_prompts(
    version_dir: Path,
    script: ScriptCheckpoint,
    art_template: dict[str, str],
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    *,
    output_suffix: str,
) -> tuple[str, str]:
    """Prepare and save style integrator prompts before model call.
    
    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    from prompter import _format_art_direction
    from style_integrator import _format_panels_for_prompt

    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original templates
    _save_prompt_template(prompts_dir, system_prompt_path, STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME)
    _save_prompt_template(prompts_dir, user_prompt_path, STYLE_INTEGRATOR_USER_PROMPT_FILENAME)

    system_prompt = _render_prompt_template_checked(
        STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )
    user_prompt = _render_prompt_template_checked(
        STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        art_direction=_format_art_direction(art_template),
        panels_context=_format_panels_for_prompt(script),
    )

    system_final_stem = (
        f"{STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )
    user_final_stem = (
        f"{STYLE_INTEGRATOR_USER_PROMPT_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )

    (prompts_dir / f"{system_final_stem}.txt").write_text(system_prompt, encoding="utf-8")
    (prompts_dir / f"{user_final_stem}.txt").write_text(user_prompt, encoding="utf-8")

    return system_prompt, user_prompt


def prepare_page_prompt_template(
    version_dir: Path,
    world: WorldStateCheckpoint,
    script: ScriptCheckpoint,
    art_template: dict[str, str],
    template_path: Path | None = None,
    *,
    aspect_ratio: str = "3:2",
    generation_mode: str = "page",
    output_suffix: str,
) -> str:
    """Prepare and save page prompt template before generation.
    
    Returns the interpolated prompt text ready to use.
    """
    from prompter import (
        _format_art_direction,
        _format_character_details,
        _format_output_goal,
        _format_page_elements_instruction,
        _format_panel_block,
        _resolve_page_number,
    )

    title = script.title or world.title or "Untitled story"
    page_number = _resolve_page_number(script)
    output_goal = _format_output_goal(generation_mode)
    page_elements_instruction = _format_page_elements_instruction(title, page_number, generation_mode)
    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original template
    _save_prompt_template(prompts_dir, template_path, PAGE_PROMPT_TEMPLATE_FILENAME)

    character_details = _format_character_details(world, script)
    panel_block = _format_panel_block(script)

    prompt_text = _render_prompt_template_checked(
        PAGE_PROMPT_TEMPLATE_FILENAME,
        template_path=template_path,
        title=title,
        art_direction=_format_art_direction(art_template),
        character_details=character_details,
        output_goal=output_goal,
        page_elements_instruction=page_elements_instruction,
        panel_count=script.panel_count,
        aspect_ratio=aspect_ratio,
        panel_block=panel_block,
    )

    final_filename_stem = (
        f"{PAGE_PROMPT_TEMPLATE_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )

    (prompts_dir / f"{final_filename_stem}.txt").write_text(prompt_text, encoding="utf-8")

    return prompt_text

