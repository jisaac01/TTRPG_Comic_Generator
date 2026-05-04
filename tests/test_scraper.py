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

    async def click(self, selector, timeout=None):
        return None

    async def wait_for_timeout(self, timeout):
        return None

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


def test_extract_recap_variants_from_rendered_text():
    body_text = """
    Session Title

    Short Recap
    Short version line.

    Standard Recap
    Standard line one.
    Standard line two.

    Alternate Recap
    Alternate version line.

    Long Recap
    Long version line.

    Audio Recap
    Placeholder.
    """

    variants = scraper.extract_recap_variants(body_text)

    assert variants["short"] == "Short version line."
    assert variants["standard"] == "Standard line one.\nStandard line two."
    assert variants["alternate"] == "Alternate version line."
    assert variants["long"] == "Long version line."


def test_normalize_recap_version_aliases():
    assert scraper.normalize_recap_version("short") == "short"
    assert scraper.normalize_recap_version("standard") == "standard"
    assert scraper.normalize_recap_version("alternate") == "alternate"
    assert scraper.normalize_recap_version("alt") == "alternate"
    assert scraper.normalize_recap_version("long") == "long"


@pytest.mark.asyncio
async def test_scrape_scrybequill_writes_checkpoint_and_cleans_html(monkeypatch, tmp_path):
    page = _FakePage(
        html_by_selector={
            scraper.DEFAULT_STORY_SELECTOR: """
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
    assert payload["selected_recap"] == "standard"
    assert payload["recap_variants"]["standard"]


# ---------------------------------------------------------------------------
# Structured-section extraction helpers
# ---------------------------------------------------------------------------


def test_extract_quotes_from_body_parses_quotes_and_attribution():
    body_text = """
    Standard Recap
    Some recap text.

    Quotes
    Quote 1
    "You shall not pass."
    Gandalf, blocking the Balrog.
    Quote 2
    "Run, you fools."
    Gandalf, falling into shadow.

    Outline
    Chapter one.
    """

    quotes = scraper.extract_quotes_from_body(body_text)

    assert len(quotes) == 2
    assert quotes[0].text == "You shall not pass."
    assert "Gandalf" in quotes[0].attribution
    assert quotes[1].text == "Run, you fools."
    assert "falling" in quotes[1].attribution


def test_extract_quotes_returns_empty_when_section_missing():
    body_text = "Standard Recap\nSome text.\nOutline\nItem one."
    assert scraper.extract_quotes_from_body(body_text) == []


def test_extract_outline_from_body_parses_items():
    body_text = """
    Quotes
    Quote 1
    "A quote."
    Speaker.

    Outline
    The First Encounter
    A Narrow Escape
    Resolution

    Notes
    NPCs
    The Villain
    """

    outline = scraper.extract_outline_from_body(body_text)

    assert outline == ["The First Encounter", "A Narrow Escape", "Resolution"]


def test_extract_outline_returns_empty_when_section_missing():
    body_text = "Standard Recap\nSome text."
    assert scraper.extract_outline_from_body(body_text) == []


def test_extract_notes_categories_parses_npcs_and_locations():
    body_text = """
    Outline
    Item one.

    Notes
    NPCs
    A powerful witch who cursed the party for stealing her cat.
    Merelda, the Dreadmarsh Witch
    Petey
    Swamp Folk

    Items
    A sinister instrument that summons creatures on a fumble.
    Harp of the Abyss
    Spirit Lantern

    Locations
    A vast, twisted swamp filled with danger.
    The Dreadmarsh
    Fungal Forest

    Factions
    Swamp Folk

    Quests
    Find five ingredients to brew an elixir.
    The Cleansing Elixir

    Player Characters
    A human Druid with a carnivorous plant companion.
    Del
    Orion
    Vendetta

    Additional Links
    """

    cats = scraper.extract_notes_categories(body_text)

    # NPCs
    assert len(cats["npcs"]) == 3
    assert cats["npcs"][0].name == "Merelda, the Dreadmarsh Witch"
    assert "witch" in cats["npcs"][0].description.lower()
    assert cats["npcs"][1] == scraper.ScrapedEntity(name="Petey", description=None)
    assert cats["npcs"][2] == scraper.ScrapedEntity(name="Swamp Folk", description=None)

    # Items
    assert cats["items"][0].name == "Harp of the Abyss"
    assert cats["items"][0].description is not None
    assert cats["items"][1].name == "Spirit Lantern"

    # Locations
    assert cats["locations"][0].name == "The Dreadmarsh"
    assert cats["locations"][1] == scraper.ScrapedEntity(name="Fungal Forest", description=None)

    # Factions (name only)
    assert len(cats["factions"]) == 1
    assert cats["factions"][0].name == "Swamp Folk"

    # Quests
    assert cats["quests"][0].name == "The Cleansing Elixir"
    assert cats["quests"][0].description is not None

    # Player Characters
    assert cats["player_characters"][0].name == "Del"
    assert cats["player_characters"][0].description is not None
    assert cats["player_characters"][1].name == "Orion"
    assert cats["player_characters"][2].name == "Vendetta"


def test_extract_notes_categories_returns_empty_when_notes_missing():
    body_text = "Standard Recap\nSome text.\nAdditional Links"
    assert scraper.extract_notes_categories(body_text) == {}


def test_junk_image_lines_are_filtered_from_entities():
    """'Image' lines from Playwright inner_text should not appear as entity names."""
    body_text = """
    Notes
    NPCs
    Image
    Merelda, the Dreadmarsh Witch
    Image
    Image
    Petey
    Image

    Additional Links
    """
    cats = scraper.extract_notes_categories(body_text)
    names = [e.name for e in cats.get("npcs", [])]
    assert "Image" not in names
    assert "Merelda, the Dreadmarsh Witch" in names
    assert "Petey" in names


def test_footer_chrome_is_filtered_from_player_characters():
    """Footer/nav content that follows the last PC should not become entity names."""
    body_text = """
    Notes
    Player Characters
    A human Druid with a carnivorous plant companion.
    Del
    Orion
    Vendetta
    © 2025 Scrybe Quill Inc.
    Unhide Tutorials
    Terms
    Privacy
    Contact

    Additional Links
    Terms
    Privacy
    Contact
    """
    cats = scraper.extract_notes_categories(body_text)
    names = [e.name for e in cats.get("player_characters", [])]
    assert names == ["Del", "Orion", "Vendetta"]

