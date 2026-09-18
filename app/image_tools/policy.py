"""Separate upload retention limits from model input dimensions."""
from ..image_tool_models import ImageToolSettings

HARD_BYTES = 100 * 1024 * 1024
HARD_PIXELS = 80_000_000
MAX_EDGE = 16000


def upload_policy():
    settings = ImageToolSettings.query.get(1)
    return dict(max_bytes=settings.upload_max_mb * 1024 * 1024,
                max_pixels=settings.upload_max_megapixels * 1000000,
                max_edge=MAX_EDGE, processing_max_edge=settings.processing_max_edge)
