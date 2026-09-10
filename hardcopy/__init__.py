"""hardcopy -- take a photograph of a page and put it on paper."""

from hardcopy.prepare import (
    A4_INCHES,
    LETTER_INCHES,
    PAGE_SIZES,
    despeckle,
    fit_page,
    flatten,
    ink_bbox,
    prepare_page,
)

__all__ = [
    "A4_INCHES",
    "LETTER_INCHES",
    "PAGE_SIZES",
    "despeckle",
    "fit_page",
    "flatten",
    "ink_bbox",
    "prepare_page",
]
