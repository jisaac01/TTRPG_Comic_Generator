import sys
from pathlib import Path

from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from image_stitcher import stitch_panel_images
from scriptwriter import Page, Panel, ScriptCheckpoint


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


def test_stitch_panel_images_prefers_single_column_for_splash_panels(tmp_path: Path) -> None:
    first = tmp_path / "panel_1.png"
    second = tmp_path / "panel_2.png"

    _write_panel_image(first, (255, 128, 0))
    _write_panel_image(second, (0, 128, 255))

    script_checkpoint = ScriptCheckpoint(
        url="https://example.test/story",
        title="Splash page",
        author="GM",
        model="test-model",
        panel_count=2,
        total_pages=1,
        pages=[
            Page(
                page_number=1,
                panel_count=2,
                panels=[
                    Panel(
                        index=1,
                        page_number=1,
                        panel_scale="splash",
                        panel_shape="wide",
                        setting="Splash panel",
                        visual_action="A huge reveal.",
                    ),
                    Panel(
                        index=2,
                        page_number=1,
                        panel_scale="medium",
                        panel_shape="standard",
                        setting="Follow-up panel",
                        visual_action="The impact settles.",
                    ),
                ],
            )
        ],
        scripted_at="2026-06-14T00:00:00+00:00",
    )

    output_path = tmp_path / "splash_page.png"

    stitch_panel_images([first, second], output_path, script_checkpoint=script_checkpoint)

    page = Image.open(output_path).convert("RGB")

    assert page.size[0] < page.size[1]
