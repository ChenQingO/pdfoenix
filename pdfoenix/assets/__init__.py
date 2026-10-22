"""Object asset layer: figure crops and on-demand verification page renders."""
from pdfoenix.assets.crop import (
    CropError,
    CropSummary,
    crop_figure,
    crop_figures,
    detect_crop_box,
    render_verify_pages,
)

__all__ = [
    "CropError",
    "CropSummary",
    "crop_figure",
    "crop_figures",
    "detect_crop_box",
    "render_verify_pages",
]
