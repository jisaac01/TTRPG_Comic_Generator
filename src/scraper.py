from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from pydantic import BaseModel, Field

DEFAULT_STORY_SELECTOR = "div.mt-3 div.text-left.text-sm"

RecapVersion = Literal["short", "standard", "alternate", "long"]

# ---------------------------------------------------------------------------
# Structured-section models
# ---------------------------------------------------------------------------


class ScrapedQuote(BaseModel):
    """A memorable quote with optional attribution."""

    text: str
    attribution: str | None = None


class ScrapedEntity(BaseModel):
    """A named entity (NPC, item, location, etc.) with optional description."""

    name: str
    description: str | None = None


RECAP_VERSION_ALIASES: dict[str, RecapVersion] = {
    "short": "short",
    "standard": "standard",
    "alternate": "alternate",
    "alt": "alternate",
    "long": "long",
}

RECAP_LABELS: dict[RecapVersion, str] = {
    "short": "Short Recap",
    "standard": "Standard Recap",
    "alternate": "Alternate Recap",
    "long": "Long Recap",
}


class RawTextCheckpoint(BaseModel):
    """Validated checkpoint payload for scraped story content."""

    url: str
    title: str | None = None
    author: str | None = None
    content: str = Field(min_length=1)
    recap_variants: dict[str, str] = Field(default_factory=dict)
    selected_recap: str | None = None
    source_selector: str
    scraped_at: str
    # Structured sections extracted directly from the page
    quotes: list[ScrapedQuote] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    npcs: list[ScrapedEntity] = Field(default_factory=list)
    items: list[ScrapedEntity] = Field(default_factory=list)
    locations: list[ScrapedEntity] = Field(default_factory=list)
    factions: list[ScrapedEntity] = Field(default_factory=list)
    quests: list[ScrapedEntity] = Field(default_factory=list)
    player_characters: list[ScrapedEntity] = Field(default_factory=list)


def normalize_recap_version(value: str) -> RecapVersion:
    key = value.strip().lower()
    if key not in RECAP_VERSION_ALIASES:
        allowed = ", ".join(sorted(RECAP_VERSION_ALIASES))
        raise ValueError(f"Unsupported recap version: {value}. Expected one of: {allowed}")
    return RECAP_VERSION_ALIASES[key]


def clean_html(raw_html: str) -> str:
    """Remove scripts/styles/tags and normalize whitespace."""

    without_scripts = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>",
        " ",
        raw_html,
        flags=re.IGNORECASE,
    )
    without_styles = re.sub(
        r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>",
        " ",
        without_scripts,
        flags=re.IGNORECASE,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_styles)
    unescaped = html.unescape(without_tags)

    lines = [" ".join(line.split()) for line in unescaped.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def extract_text_recap(body_text: str) -> str | None:
    """Extract the Text Recap section from rendered page text."""

    normalized = re.sub(r"\r\n?", "\n", body_text)
    match = re.search(
        (
            r"(?:^|\n)\s*Text\s+Recap\s*\n"
            r"(?P<content>.*?)"
            r"(?=\n\s*(?:Audio\s+Recap|Video\s+Recap|Quotes|Outline|Notes|Additional\s+Links)\b|\Z)"
        ),
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    lines = [" ".join(line.split()) for line in match.group("content").splitlines()]
    recap = "\n".join(line for line in lines if line).strip()
    return recap or None


def _extract_section_by_heading(body_text: str, headings: list[str]) -> str | None:
    """Extract section lines after a heading until the next known section heading."""

    heading_set = {heading.strip().lower() for heading in headings}
    stop_headings = {
        "short recap",
        "standard recap",
        "alternate recap",
        "alt recap",
        "long recap",
        "text recap",
        "audio recap",
        "video recap",
        "quotes",
        "outline",
        "notes",
        "additional links",
    }

    normalized = re.sub(r"\r\n?", "\n", body_text)
    lines = normalized.split("\n")

    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() in heading_set:
            start_idx = idx + 1
            break

    if start_idx is None:
        return None

    content_lines: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if stripped.lower() in stop_headings:
            break
        normalized_line = " ".join(stripped.split())
        if normalized_line:
            content_lines.append(normalized_line)

    if not content_lines:
        return None
    return "\n".join(content_lines)


def extract_recap_variants(body_text: str) -> dict[str, str]:
    """Extract recap variants from rendered body text headings when present."""

    variants: dict[str, str] = {}
    heading_map: dict[RecapVersion, list[str]] = {
        "short": ["Short Recap"],
        "standard": ["Standard Recap", "Text Recap"],
        "alternate": ["Alternate Recap", "Alt Recap"],
        "long": ["Long Recap"],
    }
    for key, headings in heading_map.items():
        value = _extract_section_by_heading(body_text, headings)
        if value:
            variants[key] = value
    return variants


# ---------------------------------------------------------------------------
# Structured-section extractors (Quotes, Outline, Notes categories)
# ---------------------------------------------------------------------------

# Text lines that carry no meaningful content (e.g. generic image alt text from
# Playwright's inner_text, or empty lines after stripping, or known page-chrome
# strings such as navigation links and footer text).
_JUNK_LINE_VALUES: frozenset[str] = frozenset(
    {
        "image",
        "[image]",
        "image:",
        # ScrybeQuill footer / navigation chrome
        "terms",
        "privacy",
        "contact",
        "unhide tutorials",
        "log in",
        "sign up",
        "+ new recap",
    }
)

# Known category headings inside the Notes section (lower-cased for matching).
# Also includes the page-level "Additional Links" heading so scanning stops
# before the footer even if it isn't separated by a blank line.
_NOTES_CATEGORY_STOP_SET: frozenset[str] = frozenset(
    [
        "npcs",
        "npc",
        "non-player characters",
        "items",
        "locations",
        "factions",
        "quests",
        "player characters",
        "pcs",
        "additional links",
    ]
)

# Mapping of output field names to the heading variants the page may use.
_NOTES_CATEGORIES: dict[str, list[str]] = {
    "npcs": ["NPCs", "NPC", "Non-Player Characters"],
    "items": ["Items"],
    "locations": ["Locations"],
    "factions": ["Factions"],
    "quests": ["Quests"],
    "player_characters": ["Player Characters", "PCs"],
}


def _clean_body_lines(text: str) -> list[str]:
    """Return non-empty, non-junk lines from raw body text."""
    result: list[str] = []
    for line in re.sub(r"\r\n?", "\n", text).split("\n"):
        stripped = " ".join(line.split())
        if not stripped:
            continue
        if stripped.lower() in _JUNK_LINE_VALUES:
            continue
        # Skip copyright / legal notices (e.g. "© 2025 Scrybe Quill Inc.")
        if stripped.startswith("©"):
            continue
        result.append(stripped)
    return result


def _is_description_line(text: str) -> bool:
    """Heuristic: return True when *text* looks like a description rather than a name.

    Descriptions are sentence-like — they tend to be longer, contain mid-sentence
    periods (``'. '``), or end with a period after a substantial word count.
    """
    if len(text) > 60:
        return True
    if re.search(r"\.\s+[A-Z]", text):
        return True
    if text.endswith(".") and len(text.split()) > 6:
        return True
    return False


def _parse_entities_from_lines(lines: list[str]) -> list[ScrapedEntity]:
    """Convert a flat list of lines into :class:`ScrapedEntity` objects.

    The ScrybeQuill Notes section interleaves optional description text with
    entity names: a description (if present) always precedes its entity's name.
    This function uses :func:`_is_description_line` to make that distinction.
    """
    entities: list[ScrapedEntity] = []
    pending_desc: str | None = None

    for line in lines:
        if _is_description_line(line):
            if pending_desc is not None:
                # Back-to-back descriptions — treat the previous one as a bare name.
                entities.append(ScrapedEntity(name=pending_desc))
            pending_desc = line
        else:
            entities.append(ScrapedEntity(name=line, description=pending_desc))
            pending_desc = None

    if pending_desc is not None:
        entities.append(ScrapedEntity(name=pending_desc))

    return entities


def _extract_notes_category_lines(notes_text: str, category_names: list[str]) -> list[str]:
    """Within *notes_text* (the raw Notes section body), find lines for *category_names*."""
    cat_set = {c.strip().lower() for c in category_names}
    lines = re.sub(r"\r\n?", "\n", notes_text).split("\n")

    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if " ".join(line.split()).lower() in cat_set:
            start_idx = idx + 1
            break

    if start_idx is None:
        return []

    content_lines: list[str] = []
    for line in lines[start_idx:]:
        stripped = " ".join(line.split())
        normalized = stripped.lower()
        if normalized in _NOTES_CATEGORY_STOP_SET and normalized not in cat_set:
            break
        if not stripped:
            continue
        if normalized in _JUNK_LINE_VALUES:
            continue
        if stripped.startswith("©"):
            continue
        content_lines.append(stripped)

    return content_lines


def extract_quotes_from_body(body_text: str) -> list[ScrapedQuote]:
    """Parse the ``Quotes`` section of rendered body text into :class:`ScrapedQuote` objects."""
    content = _extract_section_by_heading(body_text, ["Quotes"])
    if not content:
        return []

    lines = _clean_body_lines(content)
    quotes: list[ScrapedQuote] = []

    i = 0
    while i < len(lines):
        if re.match(r"^Quote\s+\d+$", lines[i], re.IGNORECASE):
            i += 1
            quote_text: str | None = None
            attribution_parts: list[str] = []

            while i < len(lines) and not re.match(r"^Quote\s+\d+$", lines[i], re.IGNORECASE):
                cur = lines[i]
                if quote_text is None:
                    # Strip surrounding typographic or ASCII double-quotes.
                    quote_text = cur.strip('"').strip("\u201c\u201d").strip()
                else:
                    attribution_parts.append(cur)
                i += 1

            if quote_text:
                quotes.append(
                    ScrapedQuote(
                        text=quote_text,
                        attribution=" ".join(attribution_parts) or None,
                    )
                )
        else:
            i += 1

    return quotes


def extract_outline_from_body(body_text: str) -> list[str]:
    """Parse the ``Outline`` section of rendered body text into a list of items."""
    content = _extract_section_by_heading(body_text, ["Outline"])
    if not content:
        return []
    return _clean_body_lines(content)


def extract_notes_categories(
    body_text: str,
) -> dict[str, list[ScrapedEntity]]:
    """Parse all Notes sub-categories from rendered body text.

    Returns a mapping of field name → entity list for categories that are
    present on the page (e.g. ``"npcs"``, ``"items"``, ``"locations"`` …).
    """
    notes_content = _extract_section_by_heading(body_text, ["Notes"])
    if not notes_content:
        return {}

    result: dict[str, list[ScrapedEntity]] = {}
    for field_name, headings in _NOTES_CATEGORIES.items():
        lines = _extract_notes_category_lines(notes_content, headings)
        if lines:
            result[field_name] = _parse_entities_from_lines(lines)

    return result


async def _safe_text_content(page, selector: str) -> str | None:
    try:
        value = await page.locator(selector).first.text_content(timeout=3000)
    except Exception:
        return None

    if value is None:
        return None

    normalized = " ".join(value.split())
    return normalized or None


async def _extract_story_from_selector(page, story_selector: str) -> str | None:
    try:
        story_html = await page.inner_html(story_selector)
    except Exception:
        return None

    cleaned = clean_html(story_html)
    return cleaned or None


async def _try_click_recap_control(page, label: str) -> bool:
    selectors = [
        f"button:has-text('{label}')",
        f"[role='tab']:has-text('{label}')",
        f"[aria-label='{label}']",
        f"text={label}",
    ]

    for selector in selectors:
        try:
            await page.click(selector, timeout=1500)
            try:
                await page.wait_for_timeout(250)
            except Exception:
                pass
            return True
        except Exception:
            continue

    return False


def save_checkpoint(checkpoint: RawTextCheckpoint, checkpoint_path: Path) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def scrape_scrybequill(
    url: str,
    checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/01_raw_text.json"),
    story_selector: str = DEFAULT_STORY_SELECTOR,
    recap_version: str = "standard",
    title_selector: str = "h1",
    author_selector: str = ".author",
    timeout_ms: int = 45000,
) -> RawTextCheckpoint:
    selected_recap = normalize_recap_version(recap_version)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Some SPAs keep long-lived requests (analytics/websockets), making
        # networkidle unreliable. Wait for the content node instead.
        resolved_source_selector = story_selector
        source_by_variant: dict[str, str] = {}
        recap_variants: dict[str, str] = {}
        body_text: str | None = None
        try:
            await page.wait_for_selector(story_selector, timeout=timeout_ms)
            story_content = await _extract_story_from_selector(page, story_selector)
            if story_content:
                recap_variants[selected_recap] = story_content
                source_by_variant[selected_recap] = story_selector
        except PlaywrightTimeoutError:
            pass

        try:
            body_text = await page.inner_text("body")
        except Exception:
            body_text = None

        if body_text:
            parsed = extract_recap_variants(body_text)
            for key, value in parsed.items():
                recap_variants.setdefault(key, value)
                source_by_variant.setdefault(key, "body::heading")

        # If variants are behind tabs/buttons, click through recap controls to capture each.
        for key, label in RECAP_LABELS.items():
            if key in recap_variants:
                continue

            clicked = await _try_click_recap_control(page, label)
            if not clicked:
                continue

            story_content = await _extract_story_from_selector(page, story_selector)
            if story_content:
                recap_variants[key] = story_content
                source_by_variant[key] = story_selector
                continue

            try:
                body_after_click = await page.inner_text("body")
            except Exception:
                body_after_click = None
            if body_after_click:
                parsed_after_click = extract_recap_variants(body_after_click)
                if key in parsed_after_click:
                    recap_variants[key] = parsed_after_click[key]
                    source_by_variant[key] = "body::heading"

        cleaned_content = recap_variants.get(selected_recap)
        if cleaned_content is None:
            if body_text:
                recap_text = extract_text_recap(body_text)
                if recap_text is not None:
                    cleaned_content = recap_text
                    recap_variants.setdefault("standard", recap_text)
                    source_by_variant.setdefault("standard", "body::Text Recap")

        if cleaned_content is None and recap_variants:
            fallback_order = ["standard", "short", "alternate", "long"]
            for key in fallback_order:
                if key in recap_variants:
                    cleaned_content = recap_variants[key]
                    selected_recap = key
                    break

        if cleaned_content is None:
            raise PlaywrightTimeoutError(
                f"Unable to extract recap content for selector '{story_selector}'"
            )

        resolved_source_selector = source_by_variant.get(selected_recap, resolved_source_selector)

        title = await _safe_text_content(page, title_selector)
        author = await _safe_text_content(page, author_selector)

        await browser.close()

    # Extract structured sections from the already-captured body text.
    scraped_quotes: list[ScrapedQuote] = []
    scraped_outline: list[str] = []
    scraped_notes: dict[str, list[ScrapedEntity]] = {}
    if body_text:
        scraped_quotes = extract_quotes_from_body(body_text)
        scraped_outline = extract_outline_from_body(body_text)
        scraped_notes = extract_notes_categories(body_text)

    checkpoint = RawTextCheckpoint(
        url=url,
        title=title,
        author=author,
        content=cleaned_content,
        recap_variants=recap_variants,
        selected_recap=selected_recap,
        source_selector=resolved_source_selector,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        quotes=scraped_quotes,
        outline=scraped_outline,
        npcs=scraped_notes.get("npcs", []),
        items=scraped_notes.get("items", []),
        locations=scraped_notes.get("locations", []),
        factions=scraped_notes.get("factions", []),
        quests=scraped_notes.get("quests", []),
        player_characters=scraped_notes.get("player_characters", []),
    )
    save_checkpoint(checkpoint, checkpoint_path)
    return checkpoint


async def _run_cli() -> None:
    parser = argparse.ArgumentParser(description="Scrape story text into a JSON checkpoint.")
    parser.add_argument("url", help="ScrybeQuill story URL")
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Path to checkpoint JSON output. When using the pipeline, this is managed automatically under campaigns/<campaign>/<episode>/<version>/.",
    )
    parser.add_argument(
        "--selector",
        default=DEFAULT_STORY_SELECTOR,
        help="CSS selector that contains story content",
    )
    parser.add_argument(
        "--recap-version",
        choices=["short", "standard", "alternate", "alt", "long"],
        default="standard",
        help="Recap variant to select for the content field",
    )

    args = parser.parse_args()

    if args.checkpoint is None:
        parser.error("--checkpoint is required when running scraper.py directly.")

    checkpoint = await scrape_scrybequill(
        url=args.url,
        checkpoint_path=Path(args.checkpoint),
        story_selector=args.selector,
        recap_version=args.recap_version,
    )
    print(f"Saved {len(checkpoint.content)} characters to {args.checkpoint}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_cli())
