"""Utilities for saving interpolated prompts to version directories for inspection."""

from __future__ import annotations

from pathlib import Path

from entities import WorldStateCheckpoint
from prompt_templates import (
    PAGE_PROMPT_TEMPLATE_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scriptwriter import ScriptCheckpoint, WorldStateInput
from master_beater import StoryBibleCheckpoint


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


def prepare_beater_prompts(
    version_dir: Path,
    content: str,
    world: WorldStateCheckpoint,
    scene_count: int,
    raw_quotes: list[dict[str, str | None]] | None = None,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> tuple[str, str]:
    """Prepare and save master beater prompts before model call.
    
    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    from master_beater import _format_entities_for_prompt, _format_quotes_for_prompt

    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original templates
    _save_prompt_template(prompts_dir, system_prompt_path, MASTER_BEATER_SYSTEM_PROMPT_FILENAME)
    _save_prompt_template(prompts_dir, user_prompt_path, MASTER_BEATER_USER_PROMPT_FILENAME)

    template_vars = {
        "title": world.title or "Untitled story",
        "panel_count": scene_count,
        "scene_count": scene_count,
        "entities_context": _format_entities_for_prompt(world),
        "reference_quotes": _format_quotes_for_prompt(raw_quotes),
        "story_text": content,
    }

    # Render prompts
    system_prompt = render_prompt_template(
        MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
        **template_vars,
    )
    user_prompt = render_prompt_template(
        MASTER_BEATER_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        **template_vars,
    )

    # Save interpolated versions
    (prompts_dir / f"{MASTER_BEATER_SYSTEM_PROMPT_FILENAME.replace('.txt', '')}_FINAL.txt").write_text(
        system_prompt, encoding="utf-8"
    )
    (prompts_dir / f"{MASTER_BEATER_USER_PROMPT_FILENAME.replace('.txt', '')}_FINAL.txt").write_text(
        user_prompt, encoding="utf-8"
    )

    return system_prompt, user_prompt


def prepare_scriptwriter_prompts(
    version_dir: Path,
    world: WorldStateInput,
    story_bible: StoryBibleCheckpoint,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    *,
    output_suffix: str,
) -> tuple[str, str]:
    """Prepare and save scriptwriter prompts before model call.
    
    Returns tuple of (system_prompt, user_prompt) ready to send to model.
    """
    from scriptwriter import _format_entities_for_prompt, _format_story_bible_for_prompt

    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original templates
    _save_prompt_template(prompts_dir, system_prompt_path, SCRIPTWRITER_SYSTEM_PROMPT_FILENAME)
    _save_prompt_template(prompts_dir, user_prompt_path, SCRIPTWRITER_USER_PROMPT_FILENAME)

    title = world.title or "Untitled story"
    entities_context = _format_entities_for_prompt(world)

    system_prompt = render_prompt_template(
        SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )
    user_prompt = render_prompt_template(
        SCRIPTWRITER_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        title=title,
        panel_count=story_bible.scene_count,
        entities_context=entities_context,
        story_bible=_format_story_bible_for_prompt(story_bible),
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
    output_suffix: str,
) -> str:
    """Prepare and save page prompt template before generation.
    
    Returns the interpolated prompt text ready to use.
    """
    from prompter import (
        _format_art_direction,
        _format_character_details,
        _format_panel_block,
    )

    prompts_dir = _ensure_prompts_dir(version_dir)
    
    # Save original template
    _save_prompt_template(prompts_dir, template_path, PAGE_PROMPT_TEMPLATE_FILENAME)

    character_details = _format_character_details(world, script)
    panel_block = _format_panel_block(script)

    prompt_text = render_prompt_template(
        PAGE_PROMPT_TEMPLATE_FILENAME,
        template_path=template_path,
        art_direction=_format_art_direction(art_template),
        character_details=character_details,
        panel_count=script.panel_count,
        panel_block=panel_block,
    )

    final_filename_stem = (
        f"{PAGE_PROMPT_TEMPLATE_FILENAME.replace('.txt', '')}_FINAL_{output_suffix}"
    )

    (prompts_dir / f"{final_filename_stem}.txt").write_text(prompt_text, encoding="utf-8")

    return prompt_text

