from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from pydantic import BaseModel, Field

DEFAULT_STORY_SELECTOR = "div.mt-3 div.text-left.text-sm"


class RawTextCheckpoint(BaseModel):
    """Validated checkpoint payload for scraped story content."""

    url: str
    title: str | None = None
    author: str | None = None
    content: str = Field(min_length=1)
    source_selector: str
    scraped_at: str


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


async def _safe_text_content(page, selector: str) -> str | None:
    try:
        value = await page.locator(selector).first.text_content(timeout=3000)
    except Exception:
        return None

    if value is None:
        return None

    normalized = " ".join(value.split())
    return normalized or None


def save_checkpoint(checkpoint: RawTextCheckpoint, checkpoint_path: Path) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def scrape_scrybequill(
    url: str,
    checkpoint_path: Path = Path("checkpoints/01_raw_text.json"),
    story_selector: str = DEFAULT_STORY_SELECTOR,
    title_selector: str = "h1",
    author_selector: str = ".author",
    timeout_ms: int = 45000,
) -> RawTextCheckpoint:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Some SPAs keep long-lived requests (analytics/websockets), making
        # networkidle unreliable. Wait for the content node instead.
        resolved_source_selector = story_selector
        try:
            await page.wait_for_selector(story_selector, timeout=timeout_ms)
            story_html = await page.inner_html(story_selector)
            cleaned_content = clean_html(story_html)
        except PlaywrightTimeoutError:
            # Fallback for selector drift: parse the rendered body text by headings.
            body_text = await page.inner_text("body")
            recap_text = extract_text_recap(body_text)
            if recap_text is None:
                raise
            cleaned_content = recap_text
            resolved_source_selector = "body::Text Recap"

        title = await _safe_text_content(page, title_selector)
        author = await _safe_text_content(page, author_selector)

        await browser.close()

    checkpoint = RawTextCheckpoint(
        url=url,
        title=title,
        author=author,
        content=cleaned_content,
        source_selector=resolved_source_selector,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )
    save_checkpoint(checkpoint, checkpoint_path)
    return checkpoint


async def _run_cli() -> None:
    parser = argparse.ArgumentParser(description="Scrape story text into a JSON checkpoint.")
    parser.add_argument("url", help="ScrybeQuill story URL")
    parser.add_argument(
        "--checkpoint",
        default="checkpoints/01_raw_text.json",
        help="Path to checkpoint JSON output",
    )
    parser.add_argument(
        "--selector",
        default=DEFAULT_STORY_SELECTOR,
        help="CSS selector that contains story content",
    )

    args = parser.parse_args()

    checkpoint = await scrape_scrybequill(
        url=args.url,
        checkpoint_path=Path(args.checkpoint),
        story_selector=args.selector,
    )
    print(f"Saved {len(checkpoint.content)} characters to {args.checkpoint}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_cli())
