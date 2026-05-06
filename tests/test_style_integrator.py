import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import style_integrator
from scriptwriter import Panel, ScriptCheckpoint


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ART_TEMPLATE = {
    "base_style": "Scratchy MS Paint kid drawing",
    "color_palette": "Primary colours, lots of scribble",
    "layout_and_composition": "Wobbly borders and uneven panels",
    "lettering_and_dialog": "Wiggly hand-lettered text",
}

_ART_TEMPLATE_JSON = json.dumps(_ART_TEMPLATE)


def _write_script_checkpoint(tmp_path: Path) -> Path:
    checkpoint = ScriptCheckpoint(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model="qwen2.5:7b",
        panel_count=2,
        panels=[
            Panel(
                index=1,
                panel_scale="large",
                panel_shape="wide",
                setting="Marsh edge at dusk",
                visual_action="A shark flops on the ground while Del watches.",
                dialogue_overlay=["Del: What is that?"],
                held_items_before={"Del": []},
                held_items_after={"Del": []},
            ),
            Panel(
                index=2,
                panel_scale="medium",
                panel_shape="standard",
                setting="Narrow path between reeds",
                visual_action="Vendetta prods the shark with a stick.",
                dialogue_overlay=["Vendetta: It's still alive."],
                held_items_before={"Vendetta": []},
                held_items_after={"Vendetta": ["stick"]},
            ),
        ],
        scripted_at="2026-05-04T00:00:00+00:00",
    )
    path = tmp_path / "03_script.json"
    path.write_text(checkpoint.model_dump_json(), encoding="utf-8")
    return path


def _write_art_template(tmp_path: Path) -> Path:
    path = tmp_path / "art_direction_template.json"
    path.write_text(_ART_TEMPLATE_JSON, encoding="utf-8")
    return path


def _styled_payload() -> style_integrator.StyledScriptPayload:
    return style_integrator.StyledScriptPayload(
        panels=[
            style_integrator.StyledPanelRewrite(
                index=1,
                setting="A scribbly marsh edge at wobbly dusk",
                visual_action="A scribbly shark flops on the ground while a stick-figure Del watches.",
            ),
            style_integrator.StyledPanelRewrite(
                index=2,
                setting="A crooked path between lumpy reeds",
                visual_action="A stick-figure Vendetta prods the shark with a wobbly stick.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# integrate_style: basic behaviour
# ---------------------------------------------------------------------------


def test_integrate_style_writes_checkpoint_and_preserves_structure(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    checkpoint = style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        generator=lambda _script, _art, _model: _styled_payload(),
    )

    assert output_path.exists()
    assert checkpoint.panel_count == 2
    assert len(checkpoint.panels) == 2
    assert [p.index for p in checkpoint.panels] == [1, 2]


def test_integrate_style_rewrites_setting_and_visual_action(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    checkpoint = style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        generator=lambda _script, _art, _model: _styled_payload(),
    )

    assert "scribbly" in checkpoint.panels[0].visual_action
    assert "scribbly" in checkpoint.panels[0].setting
    assert "crooked" in checkpoint.panels[1].setting


def test_integrate_style_preserves_structural_fields(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    checkpoint = style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        generator=lambda _script, _art, _model: _styled_payload(),
    )

    p0 = checkpoint.panels[0]
    assert p0.panel_scale == "large"
    assert p0.panel_shape == "wide"
    assert p0.dialogue_overlay == ["Del: What is that?"]
    assert p0.held_items_before == {"Del": []}
    assert p0.held_items_after == {"Del": []}

    p1 = checkpoint.panels[1]
    assert p1.held_items_after == {"Vendetta": ["stick"]}


def test_integrate_style_url_and_metadata_preserved(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    checkpoint = style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        generator=lambda _script, _art, _model: _styled_payload(),
    )

    assert checkpoint.url == "https://example.test/story"
    assert checkpoint.title == "Swamp Trouble"
    assert checkpoint.author == "GM"


def test_integrate_style_checkpoint_is_valid_json_on_disk(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        generator=lambda _script, _art, _model: _styled_payload(),
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["panel_count"] == 2
    assert payload["panels"][0]["setting"] == "A scribbly marsh edge at wobbly dusk"


# ---------------------------------------------------------------------------
# integrate_style: generator receives correct art template
# ---------------------------------------------------------------------------


def test_integrate_style_passes_art_template_to_generator(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    received: dict = {}

    def capturing_generator(script, art, model):
        received["art"] = art
        received["model"] = model
        return _styled_payload()

    style_integrator.integrate_style(
        script_checkpoint_path=script_path,
        art_style_template_path=art_path,
        output_path=output_path,
        model="llama3.1:8b",
        generator=capturing_generator,
    )

    assert received["art"]["base_style"] == "Scratchy MS Paint kid drawing"
    assert received["model"] == "llama3.1:8b"


# ---------------------------------------------------------------------------
# integrate_style: panel validation
# ---------------------------------------------------------------------------


def test_integrate_style_raises_if_generator_returns_extra_panels(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    extra_payload = style_integrator.StyledScriptPayload(panels=_styled_payload().panels + [
        style_integrator.StyledPanelRewrite(
            index=99,
            setting="Extra scene",
            visual_action="Extra action",
        )
    ])

    with pytest.raises(ValueError, match=r"expected=\[1, 2\], received=\[1, 2, 99\]"):
        style_integrator.integrate_style(
            script_checkpoint_path=script_path,
            art_style_template_path=art_path,
            output_path=output_path,
            max_generation_attempts=1,
            generator=lambda _s, _a, _m: extra_payload,
        )


def test_integrate_style_raises_if_generator_returns_fewer_panels(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    short_payload = style_integrator.StyledScriptPayload(panels=_styled_payload().panels[:1])

    with pytest.raises(ValueError, match=r"expected=\[1, 2\], received=\[1\]"):
        style_integrator.integrate_style(
            script_checkpoint_path=script_path,
            art_style_template_path=art_path,
            output_path=output_path,
            max_generation_attempts=1,
            generator=lambda _s, _a, _m: short_payload,
        )


def test_integrate_style_raises_partial_failure_and_writes_checkpoint_if_a_panel_is_unchanged(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    art_path = _write_art_template(tmp_path)
    output_path = tmp_path / "03_5_styled_script.json"

    unchanged_payload = style_integrator.StyledScriptPayload(
        panels=[
            style_integrator.StyledPanelRewrite(
                index=1,
                setting="Marsh edge at dusk",
                visual_action="A shark flops on the ground while Del watches.",
            ),
            style_integrator.StyledPanelRewrite(
                index=2,
                setting="A crooked path between lumpy reeds",
                visual_action="A stick-figure Vendetta prods the shark with a wobbly stick.",
            ),
        ]
    )

    with pytest.raises(
        style_integrator.StyleIntegrationPartialFailure,
        match=r"left panels unchanged: \[1\]",
    ) as exc_info:
        style_integrator.integrate_style(
            script_checkpoint_path=script_path,
            art_style_template_path=art_path,
            output_path=output_path,
            max_generation_attempts=1,
            generator=lambda _s, _a, _m: unchanged_payload,
        )

    assert output_path.exists()
    assert exc_info.value.checkpoint.panel_count == 2
    assert exc_info.value.checkpoint.panels[0].setting == "Marsh edge at dusk"


# ---------------------------------------------------------------------------
# integrate_style: missing art template raises error
# ---------------------------------------------------------------------------


def test_integrate_style_raises_if_art_template_missing(tmp_path):
    script_path = _write_script_checkpoint(tmp_path)
    missing_art = tmp_path / "nonexistent.json"
    output_path = tmp_path / "03_5_styled_script.json"

    with pytest.raises(FileNotFoundError):
        style_integrator.integrate_style(
            script_checkpoint_path=script_path,
            art_style_template_path=missing_art,
            output_path=output_path,
            generator=lambda _s, _a, _m: _styled_payload(),
        )
