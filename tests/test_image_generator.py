from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from image_generator import (
    ImageGenerator,
    append_image_generation_record,
    generate_prompt_images,
    latest_images_dir,
    next_images_dir,
)


def _generate_content_payload(image_bytes: bytes, *, extra_parts: list[dict] | None = None) -> dict:
    parts = list(extra_parts or [])
    parts.append(
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        }
    )
    return {"candidates": [{"content": {"parts": parts}}]}


def test_image_generator_generates_and_saves_image_bytes(tmp_path) -> None:
    request_fn = MagicMock(return_value=_generate_content_payload(b"fake-image-bytes"))
    generator = ImageGenerator(
        "gemini-3.1-flash-lite-image",
        aspect_ratio="3:2",
        request_fn=request_fn,
    )

    image_bytes = generator.generate_image("A glowing dragon over a castle")

    assert image_bytes == b"fake-image-bytes"
    request_fn.assert_called_once_with(
        "gemini-3.1-flash-lite-image",
        "A glowing dragon over a castle",
        "3:2",
    )

    output_path = tmp_path / "generated" / "page_001.png"
    saved_path = generator.save_image(image_bytes, output_path)

    assert saved_path == output_path
    assert output_path.read_bytes() == b"fake-image-bytes"


def test_image_generator_posts_generate_content_not_predict(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps(_generate_content_payload(b"png-bytes")).encode("utf-8")

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        captured["api_key"] = request.get_header("X-goog-api-key") or request.get_header(
            "x-goog-api-key"
        )
        return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr("image_generator.urllib.request.urlopen", fake_urlopen)

    generator = ImageGenerator("gemini-3.1-flash-lite-image", aspect_ratio="3:2")
    image_bytes = generator.generate_image("a marsh at dusk")

    assert image_bytes == b"png-bytes"
    assert captured["method"] == "POST"
    assert captured["api_key"] == "secret-key"
    assert "models/gemini-3.1-flash-lite-image:generateContent" in str(captured["url"])
    assert "openai" not in str(captured["url"])
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["contents"][0]["parts"][0]["text"] == "a marsh at dusk"
    assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "3:2"


def test_image_generator_uses_the_last_inline_image_part() -> None:
    thought = base64.b64encode(b"thought").decode("ascii")
    request_fn = MagicMock(
        return_value=_generate_content_payload(
            b"final-image",
            extra_parts=[
                {"text": "sketching layout"},
                {"inlineData": {"mimeType": "image/png", "data": thought}},
            ],
        )
    )
    generator = ImageGenerator("gemini-3.1-flash-lite-image", request_fn=request_fn)

    assert generator.generate_image("panel one") == b"final-image"


def test_image_generator_raises_when_response_has_no_image_payload() -> None:
    generator = ImageGenerator(
        "gemini-3.1-flash-lite-image",
        request_fn=lambda *_args: {"candidates": [{"content": {"parts": [{"text": "nope"}]}}]},
    )

    with pytest.raises(ValueError, match="No image payload returned"):
        generator.generate_image("A blank scene")


def test_image_generator_includes_http_error_body(monkeypatch) -> None:
    class FakeHTTPError(OSError):
        def __init__(self) -> None:
            super().__init__("HTTP Error 404")
            self.code = 404

        def read(self) -> bytes:
            return json.dumps(
                {
                    "error": {
                        "code": 404,
                        "message": "models/gemini-3.1-flash-lite-image is not found for API version v1main, or is not supported for predict.",
                    }
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        raise FakeHTTPError()

    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr("image_generator.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("image_generator.urllib.error.HTTPError", FakeHTTPError)

    generator = ImageGenerator("gemini-3.1-flash-lite-image")
    with pytest.raises(OSError, match="generateContent") as exc_info:
        generator.generate_image("a marsh at dusk")
    assert "404" in str(exc_info.value)


def test_save_image_overwrites_the_given_path(tmp_path: Path) -> None:
    output_path = tmp_path / "05_page_1.png"
    output_path.write_bytes(b"old")

    ImageGenerator(model="gemini-test").save_image(b"new", output_path)

    assert output_path.read_bytes() == b"new"
    assert list(tmp_path.glob("*.png")) == [output_path]


def test_next_images_dir_creates_incrementing_version_folders(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()

    first = next_images_dir(version_dir)
    second = next_images_dir(version_dir)

    assert first == version_dir / "images" / "v001"
    assert second == version_dir / "images" / "v002"
    assert first.is_dir()
    assert second.is_dir()
    assert latest_images_dir(version_dir) == second


class _FakeGenerator:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_image(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return f"img:{prompt}".encode()

    def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path


def test_generate_prompt_images_writes_into_a_new_images_folder(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    prompt = version_dir / "04_page_1_prompt.txt"
    prompt.write_text("a marsh at dusk", encoding="utf-8")
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [prompt],
        model="gemini-test",
        generator=generator,
    )

    image_path = version_dir / "images" / "v001" / "05_page_1.png"
    assert result.images_dir == version_dir / "images" / "v001"
    assert result.generated_paths == [image_path]
    assert image_path.read_bytes() == b"img:a marsh at dusk"
    assert generator.prompts == ["a marsh at dusk"]


def test_generate_prompt_images_second_run_uses_the_next_folder(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    prompt = version_dir / "04_page_1_prompt.txt"
    prompt.write_text("first", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(version_dir, [prompt], model="gemini-test", generator=generator)
    prompt.write_text("second", encoding="utf-8")
    result = generate_prompt_images(
        version_dir, [prompt], model="gemini-test", generator=generator
    )

    first = version_dir / "images" / "v001" / "05_page_1.png"
    second = version_dir / "images" / "v002" / "05_page_1.png"
    assert first.read_bytes() == b"img:first"
    assert second.read_bytes() == b"img:second"
    assert result.images_dir == version_dir / "images" / "v002"


def test_generate_prompt_images_one_prompt_writes_only_that_image(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
    )

    assert (version_dir / "images" / "v001" / "05_page_2.png").exists()
    assert result.generated_paths == [version_dir / "images" / "v001" / "05_page_2.png"]
    assert generator.prompts == ["two"]


def test_generate_prompt_images_new_folder_leaves_failed_pages_missing(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=generator
    )

    class _FailPageTwo(_FakeGenerator):
        def generate_image(self, prompt: str) -> bytes:
            if prompt == "two":
                raise ValueError("No image data returned from image generation response")
            return super().generate_image(prompt)

    result = generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=_FailPageTwo()
    )

    latest = version_dir / "images" / "v002"
    assert result.images_dir == latest
    assert (latest / "05_page_1.png").read_bytes() == b"img:one"
    assert result.errors == [
        "image_generation: page 2: No image data returned from image generation response"
    ]
    png_names = sorted(path.name for path in latest.glob("*.png"))
    assert png_names == ["05_page_1.png"]


def test_generate_prompt_images_single_regen_adds_suffix_in_latest_folder(
    tmp_path: Path,
) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=generator
    )
    page_two.write_text("two-b", encoding="utf-8")
    result = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
        new_folder=False,
    )
    second = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
        new_folder=False,
    )

    images_dir = version_dir / "images" / "v001"
    assert result.images_dir == images_dir
    assert second.images_dir == images_dir
    assert (images_dir / "05_page_1.png").read_bytes() == b"img:one"
    assert (images_dir / "05_page_2.png").read_bytes() == b"img:two"
    assert (images_dir / "05_page_2_v1.png").read_bytes() == b"img:two-b"
    assert second.generated_paths == [images_dir / "05_page_2_v2.png"]
    assert (images_dir / "05_page_2_v2.png").exists()
    assert latest_images_dir(version_dir) == images_dir


def test_generate_prompt_images_panel_mode_stitches_into_the_same_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    panel_one = version_dir / "04_page_1_panel_1_prompt.txt"
    panel_two = version_dir / "04_page_1_panel_2_prompt.txt"
    panel_one.write_text("p1", encoding="utf-8")
    panel_two.write_text("p2", encoding="utf-8")
    stitched: list[Path] = []

    def _fake_stitch(image_paths: list[Path], output_path: Path, **_kwargs: object) -> Path:
        output_path.write_bytes(b"stitched")
        stitched.append(output_path)
        return output_path

    monkeypatch.setattr("image_generator.stitch_panel_images", _fake_stitch)
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [panel_one, panel_two],
        model="gemini-test",
        stitch=True,
        generator=generator,
    )

    images_dir = version_dir / "images" / "v001"
    assert (images_dir / "05_page_1_panel_1.png").exists()
    assert (images_dir / "05_page_1_panel_2.png").exists()
    assert result.stitched_paths == [images_dir / "06_page_1.png"]
    assert stitched == [images_dir / "06_page_1.png"]
    assert (images_dir / "06_page_1.png").read_bytes() == b"stitched"


def test_stitch_images_dir_uses_canonical_panel_files_not_regen_suffixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from image_generator import stitch_images_dir

    images_dir = tmp_path / "images" / "v001"
    images_dir.mkdir(parents=True)
    (images_dir / "05_page_1_panel_1.png").write_bytes(b"p1")
    (images_dir / "05_page_1_panel_2.png").write_bytes(b"p2")
    (images_dir / "05_page_1_panel_1_v1.png").write_bytes(b"retry")
    received: list[list[str]] = []

    def _fake_stitch(image_paths: list[Path], output_path: Path, **_kwargs: object) -> Path:
        received.append([path.name for path in image_paths])
        output_path.write_bytes(b"stitched")
        return output_path

    monkeypatch.setattr("image_generator.stitch_panel_images", _fake_stitch)

    stitch_images_dir(images_dir)

    assert received == [["05_page_1_panel_1.png", "05_page_1_panel_2.png"]]


def test_append_image_generation_record_writes_model_and_keeps_prior_generations(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    status_path = version_dir / "run_status.json"
    status_path.write_text(
        json.dumps({"status": "ok", "run_config": {"generate_images": False}}),
        encoding="utf-8",
    )
    first_dir = version_dir / "images" / "v001"
    first_dir.mkdir(parents=True)
    first_file = first_dir / "05_page_1.png"
    first_file.write_bytes(b"one")
    second_dir = version_dir / "images" / "v002"
    second_dir.mkdir(parents=True)
    second_file = second_dir / "05_page_1.png"
    second_file.write_bytes(b"two")

    append_image_generation_record(
        version_dir,
        model="gemini-3.1-flash-image",
        images_dir=first_dir,
        files=[first_file],
        source="generate_all",
    )
    append_image_generation_record(
        version_dir,
        model="gemini-2.5-flash-image",
        images_dir=second_dir,
        files=[second_file],
        source="test_image",
    )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_config"]["image_generation_model"] == "gemini-2.5-flash-image"
    assert status["image_generations"] == [
        {
            "model": "gemini-3.1-flash-image",
            "images_dir": "images/v001",
            "files": ["05_page_1.png"],
            "source": "generate_all",
            "errors": [],
        },
        {
            "model": "gemini-2.5-flash-image",
            "images_dir": "images/v002",
            "files": ["05_page_1.png"],
            "source": "test_image",
            "errors": [],
        },
    ]


def test_append_image_generation_record_writes_errors_when_no_files(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    status_path = version_dir / "run_status.json"
    status_path.write_text(
        json.dumps({"status": "ok", "run_config": {"generate_images": False}}),
        encoding="utf-8",
    )
    images_dir = version_dir / "images" / "v001"
    images_dir.mkdir(parents=True)
    errors = [
        "image_generation: page 1: model gemini-3.1-flash-lite-image is not found",
    ]

    append_image_generation_record(
        version_dir,
        model="gemini-3.1-flash-lite-image",
        images_dir=images_dir,
        files=[],
        source="generate_all",
        errors=errors,
    )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_config"]["image_generation_model"] == "gemini-3.1-flash-lite-image"
    assert status["image_generations"] == [
        {
            "model": "gemini-3.1-flash-lite-image",
            "images_dir": "images/v001",
            "files": [],
            "source": "generate_all",
            "errors": errors,
        }
    ]


def test_append_image_generation_record_requires_run_status(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    images_dir = version_dir / "images" / "v001"
    images_dir.mkdir(parents=True)
    image_path = images_dir / "05_page_1.png"
    image_path.write_bytes(b"img")

    with pytest.raises(FileNotFoundError, match="run_status.json"):
        append_image_generation_record(
            version_dir,
            model="gemini-3.1-flash-image",
            images_dir=images_dir,
            files=[image_path],
            source="generate_all",
        )
