"""Discovery and resolution for named art direction styles.

Bundled styles live under ``prompts/art_direction/*.json``.
Campaign-local styles live under ``campaigns/<campaign>/art_direction/*.json``.

Selector identity uses a scoped id: ``bundled:<stem>`` or ``campaign:<stem>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prompt_templates import DEFAULT_PROMPTS_DIR

ART_DIRECTION_DIRNAME = "art_direction"
DEFAULT_ART_STYLE_STEM = "brutalist"
# Version snapshot filename (immutability contract; not a library style name).
ART_DIRECTION_TEMPLATE_FILENAME = "art_direction_template.json"

ArtStyleSource = Literal["bundled", "campaign"]


@dataclass(frozen=True)
class ArtStyleOption:
    id: str
    stem: str
    label: str
    path: Path
    source: ArtStyleSource


def bundled_art_direction_dir() -> Path:
    return DEFAULT_PROMPTS_DIR / ART_DIRECTION_DIRNAME


def campaign_art_direction_dir(campaigns_root: Path, campaign: str) -> Path:
    return campaigns_root / campaign / ART_DIRECTION_DIRNAME


def default_art_direction_template_path() -> Path:
    """Path to the bundled default style JSON (``brutalist.json``)."""
    return bundled_art_direction_dir() / f"{DEFAULT_ART_STYLE_STEM}.json"


def style_id(source: ArtStyleSource, stem: str) -> str:
    return f"{source}:{stem}"


def parse_style_id(style_id_value: str) -> tuple[ArtStyleSource, str]:
    if ":" not in style_id_value:
        raise ValueError(
            f"Invalid art style id {style_id_value!r}; "
            "expected 'bundled:<stem>' or 'campaign:<stem>'"
        )
    source, stem = style_id_value.split(":", 1)
    if source not in {"bundled", "campaign"} or not stem:
        raise ValueError(
            f"Invalid art style id {style_id_value!r}; "
            "expected 'bundled:<stem>' or 'campaign:<stem>'"
        )
    return source, stem  # type: ignore[return-value]


def _scan_styles(directory: Path, source: ArtStyleSource) -> list[ArtStyleOption]:
    if not directory.exists():
        return []
    options: list[ArtStyleOption] = []
    for path in sorted(directory.glob("*.json")):
        stem = path.stem
        if stem.startswith("_"):
            continue
        label = f"{stem} (campaign)" if source == "campaign" else f"{stem} (bundled)"
        options.append(
            ArtStyleOption(
                id=style_id(source, stem),
                stem=stem,
                label=label,
                path=path,
                source=source,
            )
        )
    return options


def list_art_styles(campaigns_root: Path, campaign: str) -> list[ArtStyleOption]:
    """Return bundled and campaign-local styles sorted together by label."""
    styles = _scan_styles(bundled_art_direction_dir(), "bundled") + _scan_styles(
        campaign_art_direction_dir(campaigns_root, campaign), "campaign"
    )
    return sorted(styles, key=lambda s: s.label.lower())


def resolve_art_style(
    campaigns_root: Path,
    campaign: str,
    style_id_value: str,
) -> ArtStyleOption:
    """Resolve a style id to a concrete option; fail if missing."""
    source, stem = parse_style_id(style_id_value)
    if source == "bundled":
        path = bundled_art_direction_dir() / f"{stem}.json"
    else:
        path = campaign_art_direction_dir(campaigns_root, campaign) / f"{stem}.json"

    if not path.exists():
        raise ValueError(
            f"Unknown art style {style_id_value!r}: file not found at {path}"
        )
    if stem.startswith("_"):
        raise ValueError(f"Unknown art style {style_id_value!r}: private styles are excluded")

    label = f"{stem} (campaign)" if source == "campaign" else f"{stem} (bundled)"
    return ArtStyleOption(
        id=style_id(source, stem),
        stem=stem,
        label=label,
        path=path,
        source=source,
    )


def default_art_style(campaigns_root: Path, campaign: str) -> ArtStyleOption:
    """Prefer first campaign-local style if any; else bundled default (brutalist)."""
    campaign_styles = _scan_styles(
        campaign_art_direction_dir(campaigns_root, campaign), "campaign"
    )
    if campaign_styles:
        return sorted(campaign_styles, key=lambda s: s.label.lower())[0]

    styles = list_art_styles(campaigns_root, campaign)
    for option in styles:
        if option.id == style_id("bundled", DEFAULT_ART_STYLE_STEM):
            return option
    if styles:
        return styles[0]
    raise FileNotFoundError(
        f"No art styles found under {bundled_art_direction_dir()} "
        f"or campaign {campaign!r}"
    )
