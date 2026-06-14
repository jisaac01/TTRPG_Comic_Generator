import sys
from pathlib import Path

from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from image_stitcher import _grid_size, stitch_panel_images


def _write_panel_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (24, 12), color).save(path)


def test_stitch_panel_images_creates_a_page_image(tmp_path: Path) -> None:
    first = tmp_path / "panel_1.png"
    second = tmp_path / "panel_2.png"
    third = tmp_path / "panel_3.png"

    _write_panel_image(first, (255, 0, 0))
    _write_panel_image(second, (0, 255, 0))
    _write_panel_image(third, (0, 0, 255))

    output_path = tmp_path / "stitched_page.png"

    stitched = stitch_panel_images([first, second, third], output_path)

    assert stitched == output_path
    assert output_path.exists()

    page = Image.open(output_path).convert("RGB")
    assert page.size[0] > 0
    assert page.size[1] > 0
    assert page.getpixel((24, 24)) == (255, 0, 0)


def test_stitch_panel_images_versions_existing_output(tmp_path: Path) -> None:
    first = tmp_path / "panel_1.png"
    second = tmp_path / "panel_2.png"
    output_path = tmp_path / "06_page_1.png"

    _write_panel_image(first, (10, 20, 30))
    _write_panel_image(second, (40, 50, 60))
    output_path.write_bytes(b"old-output")

    stitch_panel_images([first, second], output_path)

    assert output_path.exists()
    assert (tmp_path / "06_page_1_v1.png").exists()
    assert (tmp_path / "06_page_1_v1.png").read_bytes() == b"old-output"


def test_grid_size_prefers_landscape_layout_for_six_panels() -> None:
    assert _grid_size(6, aspect_ratio="3:2") == (3, 2)


def test_grid_size_prefers_portrait_layout_for_six_panels() -> None:
    assert _grid_size(6, aspect_ratio="4:3") == (2, 3)


def test_grid_size_handles_three_panels_on_square_pages() -> None:
    cols, rows = _grid_size(3, aspect_ratio="1:1")
    assert cols * rows >= 3
    assert cols <= rows + 1


def test_stitch_panel_images_uses_portrait_grid_for_six_panels(tmp_path: Path) -> None:
    panel_paths = []
    for index, color in enumerate(
        (
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
        ),
        start=1,
    ):
        path = tmp_path / f"panel_{index}.png"
        _write_panel_image(path, color)
        panel_paths.append(path)

    output_path = tmp_path / "portrait_page.png"
    stitch_panel_images(panel_paths, output_path, aspect_ratio="4:3")

    page = Image.open(output_path).convert("RGB")
    assert page.size[0] < page.size[1]


def test_stitch_panel_images_uses_landscape_grid_for_six_panels(tmp_path: Path) -> None:
    panel_paths = []
    for index, color in enumerate(
        (
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
        ),
        start=1,
    ):
        path = tmp_path / f"panel_{index}.png"
        _write_panel_image(path, color)
        panel_paths.append(path)

    output_path = tmp_path / "landscape_page.png"
    stitch_panel_images(panel_paths, output_path, aspect_ratio="3:2")

    page = Image.open(output_path).convert("RGB")
    assert page.size[0] > page.size[1]



