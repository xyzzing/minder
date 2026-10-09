"""Adaptive-presentation gates for the console stylesheet (issue #8):
dark scheme, reduced-transparency, increased contrast, reduced motion,
press feedback and the translucent header layer must all be defined as
token-driven media blocks, not per-rule one-offs."""
from pathlib import Path

from minder_web import app as app_module

CSS_PATH = Path(app_module.__file__).parent / "static" / "app.css"

REQUIRED_BLOCKS = (
    "prefers-color-scheme: dark",
    "prefers-reduced-transparency: reduce",
    "prefers-contrast: more",
    "prefers-reduced-motion: reduce",
)


def _css():
    return CSS_PATH.read_text()


def test_adaptive_media_blocks_exist():
    css = _css()
    for block in REQUIRED_BLOCKS:
        assert block in css, f"stylesheet lacks an @{block} block"


def test_dark_scheme_redefines_color_tokens():
    css = _css()
    start = css.index("prefers-color-scheme: dark")
    section = css[start:]
    for token in ("--bg:", "--ink:", "--card:", "--line:"):
        assert token in section, \
            f"dark scheme does not redefine {token}"


def test_press_feedback_on_interactive_elements():
    css = _css()
    assert ":active" in css, "no :active press feedback"
    active_start = css.index(":active")
    rule = css[active_start:css.index("}", active_start)]
    assert "transform" in rule, ":active rule must move the element"


def test_header_is_a_translucent_layer():
    css = _css()
    header = css[css.index("header {"):css.index("}", css.index("header {"))]
    assert "position: sticky" in header, "header must stay in view"
    assert "backdrop-filter" in header, "header must blur content under it"


def test_motion_uses_named_easing_tokens():
    """One motion language: the curves are tokens, so every transition
    in the sheet can be re-tuned in one place (issue #12)."""
    css = _css()
    for token in ("--ease-out:", "--ease-in-out:", "--dur-fast:",
                  "--dur-ui:"):
        assert token in css, f"stylesheet has no {token} token"
    assert "cubic-bezier(0.23, 1, 0.32, 1)" in css, \
        "the ease-out curve must be the named curve, not ease"
    assert "transition: all" not in css, \
        "transition: all animates properties nobody chose"


def test_numeric_columns_align():
    """Token counts and durations are read by scanning a column; digits
    must line up (issue #12)."""
    assert "font-variant-numeric: tabular-nums" in _css()


def test_hover_effects_are_gated_to_pointing_devices():
    css = _css()
    assert "(hover: hover) and (pointer: fine)" in css, \
        "hover rules must not stick on touch screens"


def test_stylesheet_avoids_the_rejected_patterns():
    """The console is an information surface: the decoration issue #12
    rejects must not reappear as polish."""
    css = _css()
    assert css.count("backdrop-filter:") <= 6, \
        "frosted glass on every surface reads as decoration"
    for banned in ("linear-gradient(135deg", "background-clip: text"):
        assert banned not in css, f"stylesheet uses {banned}"


def test_stylesheet_is_served():
    from fastapi.testclient import TestClient
    from minder_web.app import app
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
