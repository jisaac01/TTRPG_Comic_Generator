import json
from pathlib import Path

import pytest

import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
import scraper


class _FakeLocator:
    def __init__(self, value):
        self._value = value

    @property
    def first(self):
        return self

    async def text_content(self, timeout=None):
        return self._value


class _FakePage:
    def __init__(self, html_by_selector, text_by_selector):
        self._html_by_selector = html_by_selector
        self._text_by_selector = text_by_selector

    async def goto(self, url, wait_until=None, timeout=None):
        self._url = url

    async def wait_for_load_state(self, state):
        self._state = state

    async def wait_for_selector(self, selector, timeout=None):
        if selector not in self._html_by_selector:
            raise ValueError(f"Unknown selector: {selector}")

    async def inner_html(self, selector):
        return self._html_by_selector[selector]

    async def inner_text(self, selector):
        if selector == "body":
            html = "\n".join(self._html_by_selector.values())
            return scraper.clean_html(html)
        raise ValueError(f"Unknown selector for inner_text: {selector}")

    def locator(self, selector):
        return _FakeLocator(self._text_by_selector.get(selector))


class _FakeBrowser:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page

    async def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, headless=True):
        return self._browser


class _FakePlaywrightContext:
    def __init__(self, page):
        self.chromium = _FakeChromium(_FakeBrowser(page))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_clean_html_removes_script_tags_and_markup():
    raw = """
    <div class='story-content'>
        <p>Chapter <strong>One</strong></p>
        <script>alert('xss')</script>
        <style>.bad { color: red; }</style>
        <p>The hero arrives &amp; watches.</p>
    </div>
    """

    cleaned = scraper.clean_html(raw)

    assert "alert" not in cleaned
    assert "<script" not in cleaned
    assert "Chapter One" in cleaned
    assert "The hero arrives & watches." in cleaned


def test_extract_text_recap_from_rendered_text():
    body_text = """
    FLAIL - Example

    Text Recap
    First paragraph of recap.
    Second paragraph of recap.

    Audio Recap
    Placeholder content.
    """

    recap = scraper.extract_text_recap(body_text)

    assert recap == "First paragraph of recap.\nSecond paragraph of recap."


@pytest.mark.asyncio
async def test_scrape_scrybequill_writes_checkpoint_and_cleans_html(monkeypatch, tmp_path):
    page = _FakePage(
        html_by_selector={
            ".story-content": """
                <article>
                    <h2>Ignored heading inside body</h2>
                    <p>First line.</p>
                    <script>window.inject='bad';</script>
                    <p>Second line.</p>
                </article>
            """
        },
        text_by_selector={"h1": "Test Story", ".author": "GM Quinn"},
    )

    monkeypatch.setattr(
        scraper,
        "async_playwright",
        lambda: _FakePlaywrightContext(page),
    )

    checkpoint_path = tmp_path / "01_raw_text.json"
    result = await scraper.scrape_scrybequill(
        url="https://scrybequill.example/story/123",
        checkpoint_path=checkpoint_path,
    )

    assert result.title == "Test Story"
    assert result.author == "GM Quinn"
    assert "window.inject" not in result.content
    assert "First line." in result.content
    assert checkpoint_path.exists()

    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["url"] == "https://scrybequill.example/story/123"
    assert payload["title"] == "Test Story"
    assert payload["author"] == "GM Quinn"
    assert "window.inject" not in payload["content"]
