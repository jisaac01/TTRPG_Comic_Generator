"""Utilities for generating and saving images with Gemini native image models."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from image_stitcher import stitch_panel_images
from llm_client import require_gemini_api_key

IMAGES_SUBDIR_NAME = "images"
GEMINI_GENERATE_CONTENT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
IMAGE_GENERATE_TIMEOUT_SECONDS = 180
_PROMPT_NAME_RE = re.compile(r"04_page_(\d+)(?:_panel_(\d+))?_prompt\.txt$")
_PANEL_IMAGE_RE = re.compile(r"05_page_(\d+)_panel_(\d+)\.png$")
_VERSION_DIR_RE = re.compile(r"v\d{3}")


@dataclass
class GenerateImagesResult:
    images_dir: Path
    generated_paths: list[Path] = field(default_factory=list)
    stitched_paths: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _inline_image_b64(part: dict) -> str | None:
    inline = part.get("inlineData") or part.get("inline_data") or {}
    if not isinstance(inline, dict):
        return None
    mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
    data = inline.get("data")
    if data and (not mime or mime.startswith("image/")):
        return str(data)
    return None


def _image_bytes_from_generate_content(payload: dict) -> bytes:
    candidates = payload.get("candidates") or []
    parts: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        if isinstance(content, dict):
            parts.extend(content.get("parts") or [])
    images = [b64 for part in parts if isinstance(part, dict) and (b64 := _inline_image_b64(part))]
    if not images:
        raise ValueError("No image payload returned from image generation response")
    return base64.b64decode(images[-1])


def request_gemini_generate_content(
    model: str,
    prompt: str,
    aspect_ratio: str,
    *,
    urlopen: Callable[..., object] | None = None,
) -> dict:
    api_key = require_gemini_api_key()
    url = GEMINI_GENERATE_CONTENT_URL.format(model=urllib.parse.quote(model, safe=".-_"))
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(request, timeout=IMAGE_GENERATE_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise OSError(
            f"Gemini generateContent failed ({exc.code}) for {model}: {error_body}"
        ) from exc
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("Gemini generateContent returned a non-object JSON payload")
    return parsed


class ImageGenerator:
    def __init__(
        self,
        model: str,
        *,
        aspect_ratio: str = "3:2",
        request_fn: Callable[[str, str, str], dict] | None = None,
    ) -> None:
        self.model = model
        self.aspect_ratio = aspect_ratio
        self._request_fn = request_fn or request_gemini_generate_content

    def generate_image(self, prompt: str) -> bytes:
        payload = self._request_fn(self.model, prompt, self.aspect_ratio)
        return _image_bytes_from_generate_content(payload)

    def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path


def _images_root(version_dir: Path) -> Path:
    return version_dir / IMAGES_SUBDIR_NAME


def _image_version_dirs(version_dir: Path) -> list[Path]:
    root = _images_root(version_dir)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and _VERSION_DIR_RE.fullmatch(path.name)),
        key=lambda path: int(path.name[1:]),
    )


def latest_images_dir(version_dir: Path) -> Path | None:
    dirs = _image_version_dirs(version_dir)
    return dirs[-1] if dirs else None


def next_images_dir(version_dir: Path) -> Path:
    existing = _image_version_dirs(version_dir)
    if not existing:
        name = "v001"
    else:
        last_num = int(existing[-1].name[1:])
        name = f"v{last_num + 1:03d}"
    dest = _images_root(version_dir) / name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def prompt_image_filename(prompt_name: str) -> str | None:
    match = _PROMPT_NAME_RE.fullmatch(prompt_name)
    if match is None:
        return None
    page_number = match.group(1)
    panel_number = match.group(2)
    if panel_number is not None:
        return f"05_page_{page_number}_panel_{panel_number}.png"
    return f"05_page_{page_number}.png"


def prompt_sort_key(path: Path) -> tuple[int, int]:
    match = _PROMPT_NAME_RE.fullmatch(path.name)
    if match is None:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2) or 0))


def panel_image_sort_key(path: Path) -> tuple[int, int]:
    match = _PANEL_IMAGE_RE.fullmatch(path.name)
    if match is None:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2)))


def list_panel_images(images_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in images_dir.glob("05_page_*_panel_*.png")
            if _PANEL_IMAGE_RE.fullmatch(path.name)
        ),
        key=panel_image_sort_key,
    )


def next_image_output_path(images_dir: Path, filename: str) -> Path:
    """Return canonical path, or stem_vN.suffix if that file already exists."""
    canonical = images_dir / filename
    if not canonical.exists():
        return canonical
    stem = canonical.stem
    suffix = canonical.suffix
    pattern = re.compile(rf"{re.escape(stem)}_v(\d+)$")
    versions = []
    for path in images_dir.iterdir():
        if not path.is_file() or path.suffix != suffix:
            continue
        match = pattern.fullmatch(path.stem)
        if match:
            versions.append(int(match.group(1)))
    next_n = max(versions, default=0) + 1
    return images_dir / f"{stem}_v{next_n}{suffix}"


def stitch_images_dir(
    images_dir: Path, *, aspect_ratio: str = "3:2"
) -> tuple[list[Path], list[str]]:
    panel_paths = list_panel_images(images_dir)
    if not panel_paths:
        return [], []

    pages = sorted({panel_image_sort_key(path)[0] for path in panel_paths})
    stitched_paths: list[Path] = []
    errors: list[str] = []
    for page_number in pages:
        page_paths = [
            path for path in panel_paths if panel_image_sort_key(path)[0] == page_number
        ]
        output_path = images_dir / f"06_page_{page_number}.png"
        try:
            stitch_panel_images(page_paths, output_path, aspect_ratio=aspect_ratio)
            stitched_paths.append(output_path)
        except Exception as exc:
            errors.append(f"stitching: page {page_number}: {exc}")
    return stitched_paths, errors


def generate_prompt_images(
    version_dir: Path,
    prompt_paths: list[Path],
    *,
    model: str,
    aspect_ratio: str = "3:2",
    stitch: bool = False,
    generator: ImageGenerator | None = None,
    new_folder: bool = True,
) -> GenerateImagesResult:
    if not prompt_paths:
        raise ValueError("No prompt files available to generate images")

    if new_folder:
        images_dir = next_images_dir(version_dir)
    else:
        images_dir = latest_images_dir(version_dir) or next_images_dir(version_dir)

    image_generator = generator or ImageGenerator(model=model, aspect_ratio=aspect_ratio)
    if isinstance(image_generator, ImageGenerator):
        image_generator.aspect_ratio = aspect_ratio
    generated_paths: list[Path] = []
    errors: list[str] = []

    for prompt_path in sorted(prompt_paths, key=prompt_sort_key):
        filename = prompt_image_filename(prompt_path.name)
        if filename is None:
            errors.append(
                f"image_generation: unable to determine page number for {prompt_path.name}"
            )
            continue
        match = _PROMPT_NAME_RE.fullmatch(prompt_path.name)
        page_number = match.group(1) if match else prompt_path.name
        output_path = (
            images_dir / filename
            if new_folder
            else next_image_output_path(images_dir, filename)
        )
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
            image_bytes = image_generator.generate_image(prompt_text)
            image_generator.save_image(image_bytes, output_path)
            generated_paths.append(output_path)
        except Exception as exc:
            errors.append(f"image_generation: page {page_number}: {exc}")

    stitched_paths: list[Path] = []
    if stitch:
        stitched_paths, stitch_errors = stitch_images_dir(
            images_dir, aspect_ratio=aspect_ratio
        )
        errors.extend(stitch_errors)

    return GenerateImagesResult(
        images_dir=images_dir,
        generated_paths=generated_paths,
        stitched_paths=stitched_paths,
        errors=errors,
    )


def append_image_generation_record(
    version_dir: Path,
    *,
    model: str,
    images_dir: Path,
    files: list[Path],
    source: str,
    errors: list[str] | None = None,
) -> dict:
    """Append an image-generation record to the version's run_status.json."""
    status_path = version_dir / "run_status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"run_status.json not found in {version_dir}")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        raise ValueError(f"run_status.json must be a JSON object: {status_path}")

    generations = status.get("image_generations")
    if generations is None:
        generations = []
    elif not isinstance(generations, list):
        raise ValueError(f"image_generations must be a list: {status_path}")

    try:
        relative_dir = images_dir.relative_to(version_dir).as_posix()
    except ValueError:
        relative_dir = images_dir.as_posix()

    generations.append(
        {
            "model": model,
            "images_dir": relative_dir,
            "files": [path.name for path in files],
            "source": source,
            "errors": list(errors or []),
        }
    )
    status["image_generations"] = generations

    run_config = status.get("run_config")
    if not isinstance(run_config, dict):
        run_config = {}
        status["run_config"] = run_config
    run_config["image_generation_model"] = model

    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status
