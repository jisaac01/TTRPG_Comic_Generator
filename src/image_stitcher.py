from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from scriptwriter import ScriptCheckpoint


def _grid_size(panel_count: int) -> tuple[int, int]:
    cols = max(1, math.ceil(math.sqrt(panel_count)))
    rows = math.ceil(panel_count / cols)
    return cols, rows


def _should_force_single_column(script_checkpoint: ScriptCheckpoint | None) -> bool:
    if script_checkpoint is None:
        return False

    return any(panel.panel_scale == "splash" for panel in script_checkpoint.panels)


def stitch_panel_images(
    image_paths: list[Path],
    output_path: Path,
    script_checkpoint: ScriptCheckpoint | None = None,
    gutter: int = 24,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Create one comic page from panel images.

    The layout starts with a simple grid and can be biased toward a single
    column when a splash panel is present in the script metadata.
    """
    if not image_paths:
        raise ValueError("No images were provided to stitch.")

    images = [Image.open(path).convert("RGB") for path in image_paths]
    try:
        frame_width, frame_height = images[0].size
        cols, rows = _grid_size(len(images))
        if _should_force_single_column(script_checkpoint):
            cols = 1
            rows = len(images)

        page_width = frame_width * cols + gutter * (cols + 1)
        page_height = frame_height * rows + gutter * (rows + 1)

        page = Image.new("RGB", (page_width, page_height), bg_color)

        for index, image in enumerate(images):
            row = index // cols
            col = index % cols
            x = gutter + col * (frame_width + gutter)
            y = gutter + row * (frame_height + gutter)
            page.paste(image, (x, y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        page.save(output_path, format="PNG")
    finally:
        for image in images:
            image.close()

    return output_path
