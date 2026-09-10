"""Turn a photograph of a printed page into something worth putting on paper.

A phone photo of a page is not a scan. It carries the shadow of the person holding
the phone, the tone of whatever paper stock the page is on, the dark surface behind
it, and usually a thumb. Printing it directly wastes toner on all four.

The pipeline is four steps, in order:

1. ``flatten``   -- divide the image by its own blurred self, so illumination goes away
2. ``ink_bbox``  -- find what is actually printed, and crop to it
3. ``despeckle`` -- delete solid masses at the crop edge that are page, not ink
4. ``fit_page``  -- lay that on a page-size canvas at a real dot density

Step 1 is a division rather than a threshold on purpose. A threshold has to pick one
brightness for the whole image, and a photographed page does not have one: the lit
corner and the shadowed corner differ by more than ink differs from paper. Dividing by
a local background removes the gradient first, so a single stretch then works
everywhere. A threshold also destroys thin marks -- a staff line three pixels wide
survives division and disappears under a hard cut.
"""

from __future__ import annotations

import cv2
import numpy as np

LETTER_INCHES = (8.5, 11.0)
A4_INCHES = (8.27, 11.69)

PAGE_SIZES = {
    "letter": LETTER_INCHES,
    "a4": A4_INCHES,
}


def flatten(gray: np.ndarray) -> np.ndarray:
    """Remove the illumination gradient, so paper reads white and ink reads black.

    The blur kernel is a twelfth of the short side. It must be much wider than any
    glyph and much narrower than the shadow, or the division subtracts the notation
    along with the shading.
    """
    kernel = max(31, (min(gray.shape) // 12) | 1)
    background = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    normalised = cv2.divide(gray, background, scale=255)
    # 235 rather than 255: paper after division sits near white but not at it, and
    # stretching to the true maximum leaves the page grey.
    low, high = float(np.percentile(normalised, 2)), 235.0
    stretched = (normalised.astype(np.float32) - low) * 255.0 / (high - low)
    return np.clip(stretched, 0, 255).astype(np.uint8)


def despeckle(
    norm: np.ndarray,
    *,
    threshold: int = 110,
    min_area: int = 5000,
    min_dimension: int = 40,
    max_fill: float = 0.35,
) -> np.ndarray:
    """Whiten solid masses at the border that are page edge or background, not ink.

    Printed marks are thin. A beam is tens of pixels deep, a rule is three. A page
    corner photographed against a dark surface is a solid wedge, tens of pixels in
    BOTH directions, and it fills most of its bounding box. Those three properties
    together -- large, thick, and dense -- separate it from anything printed, and a
    measured page put notation at 0.20 fill against 0.50 for the wedge.

    Only blobs touching the border are considered. A dense mass in the middle of a
    page is a printed solid, and deleting it would be censorship.
    """
    ink = (norm < threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    height, width = norm.shape
    out = norm.copy()
    for index in range(1, count):
        x, y, box_w, box_h, area = stats[index]
        if area < min_area or min(box_w, box_h) < min_dimension:
            continue
        if area / float(box_w * box_h) < max_fill:
            continue
        if not (x == 0 or y == 0 or x + box_w == width or y + box_h == height):
            continue
        out[labels == index] = 255
    return out


def ink_bbox(
    norm: np.ndarray,
    *,
    threshold: int = 110,
    border: float = 0.015,
    min_fraction: float = 0.004,
) -> tuple[int, int, int, int]:
    """Bounding box of the printed area, as ``(x0, y0, x1, y1)``.

    An outer band is ignored so the dark page edge cannot define the box, and a row
    or column has to carry a minimum amount of ink to count, so a thumb, a smudge or
    a dust speck does not stretch the crop to the whole frame.
    """
    height, width = norm.shape
    ink = (norm < threshold).astype(np.uint8)
    band_y, band_x = int(height * border), int(width * border)
    if band_y:
        ink[:band_y, :] = 0
        ink[-band_y:, :] = 0
    if band_x:
        ink[:, :band_x] = 0
        ink[:, -band_x:] = 0
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    columns = np.flatnonzero(ink.sum(0) > height * min_fraction)
    rows = np.flatnonzero(ink.sum(1) > width * min_fraction)
    if columns.size == 0 or rows.size == 0:
        return 0, 0, width, height
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def fit_page(
    gray: np.ndarray,
    *,
    dpi: int = 300,
    page: str | tuple[float, float] = "letter",
    margin_inches: float = 0.3,
) -> np.ndarray:
    """Scale the image to fill a page at ``dpi``, centred, keeping its aspect ratio."""
    inches = PAGE_SIZES[page] if isinstance(page, str) else page
    page_w, page_h = int(inches[0] * dpi), int(inches[1] * dpi)
    margin = int(margin_inches * dpi)
    available_w, available_h = page_w - 2 * margin, page_h - 2 * margin

    height, width = gray.shape
    scale = min(available_w / width, available_h / height)
    scaled_w, scaled_h = max(1, int(width * scale)), max(1, int(height * scale))
    resized = cv2.resize(
        gray,
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )

    canvas = np.full((page_h, page_w), 255, np.uint8)
    top, left = (page_h - scaled_h) // 2, (page_w - scaled_w) // 2
    canvas[top : top + scaled_h, left : left + scaled_w] = resized
    return canvas


def prepare_page(
    image: np.ndarray,
    *,
    dpi: int = 300,
    page: str | tuple[float, float] = "letter",
    margin_inches: float = 0.3,
    pad_fraction: float = 0.01,
) -> np.ndarray:
    """Run the whole pipeline. Takes a colour or grey photo, returns a page-size image."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    norm = flatten(gray)
    x0, y0, x1, y1 = ink_bbox(norm)
    pad = int(pad_fraction * max(norm.shape))
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(norm.shape[1], x1 + pad), min(norm.shape[0], y1 + pad)
    return fit_page(
        despeckle(norm[y0:y1, x0:x1]),
        dpi=dpi,
        page=page,
        margin_inches=margin_inches,
    )
