"""The image pipeline, exercised on SYNTHETIC pages.

No photograph is committed here. Every fixture is drawn in code, so the failure of a
test says which property broke rather than which picture changed, and no real page
ever ends up inside the repository.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from hardcopy.prepare import despeckle, fit_page, flatten, ink_bbox, prepare_page

PAGE_W, PAGE_H = 1200, 1600


def synthetic_photo(*, shadow: bool = True, corner_wedge: bool = False,
                    thumb: bool = False) -> np.ndarray:
    """A dark surface, a lighter page on it, thin printed rules, optional junk."""
    image = np.full((PAGE_H + 200, PAGE_W + 200, 3), 70, np.uint8)   # dark surface
    page = np.full((PAGE_H, PAGE_W, 3), 235, np.uint8)               # off-white paper

    for y in range(200, PAGE_H - 200, 120):                          # thin printed rules
        cv2.line(page, (120, y), (PAGE_W - 120, y), (20, 20, 20), 3)
    cv2.putText(page, "TITLE", (300, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (10, 10, 10), 6)

    image[100 : 100 + PAGE_H, 100 : 100 + PAGE_W] = page

    if shadow:
        # A left-to-right ramp, far stronger than the ink/paper difference.
        ramp = np.linspace(0.45, 1.0, image.shape[1], dtype=np.float32)
        image = np.clip(image * ramp[None, :, None], 0, 255).astype(np.uint8)
    if corner_wedge:
        cv2.fillPoly(image, [np.array([[0, image.shape[0]],
                                       [340, image.shape[0]],
                                       [0, image.shape[0] - 90]])], (5, 5, 5))
    if thumb:
        # Measured on a real photo: a thumb holding a lit page comes out at 0.98x the
        # brightness of the paper, not as a shadow. Only its outline is dark. Drawing
        # it dark would test a situation that does not occur.
        cv2.ellipse(image, (40, 500), (90, 150), 0, 0, 360, (230, 230, 230), -1)
        cv2.ellipse(image, (40, 500), (90, 150), 0, 0, 360, (150, 150, 150), 3)
    return image


def synthetic_normalised(*, wedge: bool = False) -> np.ndarray:
    """A page as it looks AFTER flatten: white, thin dark rules, optional edge wedge.

    despeckle is unit-tested against this rather than against a photo, because
    flatten legitimately erases a large low-frequency mass -- dividing a wide dark
    blob by its own local average returns white. Feeding despeckle through flatten
    would therefore test the fixture's scale, not despeckle's rule.
    """
    norm = np.full((1400, 1100), 255, np.uint8)
    for y in range(200, 1200, 120):
        cv2.line(norm, (120, y), (980, y), 20, 3)
    if wedge:
        cv2.fillPoly(norm, [np.array([[0, 1400], [340, 1400], [0, 1310]])], 5)
    return norm


def test_flatten_makes_shadowed_paper_read_white():
    gray = cv2.cvtColor(synthetic_photo(), cv2.COLOR_BGR2GRAY)
    # The shadow is real: the lit and dark sides of the raw page differ a lot.
    left, right = gray[400, 150], gray[400, PAGE_W]
    assert int(right) - int(left) > 60

    norm = flatten(gray)
    # After flattening both sides of the paper are white together.
    assert norm[360, 150] > 200
    assert norm[360, PAGE_W] > 200
    # and the printed rules are still dark.
    assert norm[gray.shape[0] // 2, PAGE_W // 2].min() < 200


def test_flatten_keeps_thin_rules():
    """A hard threshold eats a three-pixel rule. Division must not."""
    gray = cv2.cvtColor(synthetic_photo(), cv2.COLOR_BGR2GRAY)
    norm = flatten(gray)
    assert (norm < 110).sum() > 0, "every printed mark was erased"


def test_ink_bbox_ignores_a_mark_inside_the_border_band():
    """The dark page edge lives in that band. It must not define the crop."""
    clean = synthetic_normalised()
    marked = clean.copy()
    cv2.rectangle(marked, (2, 300), (12, 900), 0, -1)
    assert ink_bbox(marked) == ink_bbox(clean)


def test_ink_bbox_ignores_a_faint_thumb_outline():
    """A thumb holding the page shows up as a thin outline, not as ink."""
    clean = synthetic_normalised()
    smudged = clean.copy()
    cv2.ellipse(smudged, (40, 700), (60, 220), 0, 0, 360, 90, 1)
    assert ink_bbox(smudged) == ink_bbox(clean)


def test_ink_bbox_does_include_real_ink_near_the_edge():
    """The counterpart: enough ink in a column DOES move the crop."""
    clean = synthetic_normalised()
    marked = clean.copy()
    cv2.rectangle(marked, (40, 200), (70, 1200), 20, -1)
    assert ink_bbox(marked)[0] < ink_bbox(clean)[0]


def test_ink_bbox_is_inside_the_frame():
    norm = flatten(cv2.cvtColor(synthetic_photo(), cv2.COLOR_BGR2GRAY))
    x0, y0, x1, y1 = ink_bbox(norm)
    assert 0 <= x0 < x1 <= norm.shape[1]
    assert 0 <= y0 < y1 <= norm.shape[0]
    assert (x1 - x0) < norm.shape[1], "the crop kept the whole frame"


def test_despeckle_removes_a_solid_edge_wedge():
    with_wedge = synthetic_normalised(wedge=True)
    without = synthetic_normalised()
    cleaned = despeckle(with_wedge)
    assert (cleaned < 110).sum() < (with_wedge < 110).sum(), "the wedge survived"
    # and what is left is exactly the printed rules
    assert (cleaned < 110).sum() == (without < 110).sum()


def test_despeckle_keeps_thin_printed_rules():
    norm = synthetic_normalised()
    assert (despeckle(norm) < 110).sum() == (norm < 110).sum()


def test_despeckle_keeps_a_solid_mass_away_from_the_border():
    """A dense blob in the middle of a page is printed. It stays."""
    norm = np.full((900, 900), 255, np.uint8)
    cv2.rectangle(norm, (350, 350), (550, 550), 0, -1)
    assert (despeckle(norm) < 110).sum() == (norm < 110).sum()


@pytest.mark.parametrize(
    ("page", "dpi", "expected"),
    [("letter", 300, (2550, 3300)), ("letter", 150, (1275, 1650)), ("a4", 300, (2481, 3507))],
)
def test_fit_page_emits_exact_page_pixels(page, dpi, expected):
    out = fit_page(np.full((400, 300), 128, np.uint8), dpi=dpi, page=page)
    assert (out.shape[1], out.shape[0]) == expected


def test_fit_page_keeps_aspect_ratio_and_centres():
    out = fit_page(np.zeros((400, 200), np.uint8), dpi=100, page="letter", margin_inches=0.5)
    dark = np.argwhere(out < 128)
    (top, left), (bottom, right) = dark.min(0), dark.max(0)
    assert (bottom - top) / (right - left) == pytest.approx(2.0, rel=0.02)
    assert left == pytest.approx(out.shape[1] - 1 - right, abs=2)
    assert top == pytest.approx(out.shape[0] - 1 - bottom, abs=2)


def test_prepare_page_end_to_end():
    out = prepare_page(synthetic_photo(corner_wedge=True, thumb=True), dpi=150)
    assert (out.shape[1], out.shape[0]) == (1275, 1650)
    assert out.dtype == np.uint8
    assert (out < 110).sum() > 0, "the printed rules did not survive the pipeline"
    # The margin is genuinely blank.
    assert out[:20, :].min() == 255
    assert out[-20:, :].min() == 255


def test_prepare_page_accepts_a_grey_image():
    grey = cv2.cvtColor(synthetic_photo(), cv2.COLOR_BGR2GRAY)
    assert prepare_page(grey, dpi=150).shape == (1650, 1275)
