"""Pillow thumbnail generation adapter."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

DEFAULT_CONTRAST_FACTOR: float = 1.25
DEFAULT_SATURATION_FACTOR: float = 1.35
DEFAULT_FONT_SIZE: int = 96
DEFAULT_STROKE_WIDTH: int = 6
JPEG_QUALITY: int = 95
TEXT_Y_RATIO: float = 0.78


class PillowThumbnailAdapter:
    """ThumbnailGeneratorPort implementation using Pillow."""

    def __init__(
        self,
        contrast_factor: float = DEFAULT_CONTRAST_FACTOR,
        saturation_factor: float = DEFAULT_SATURATION_FACTOR,
        font_size: int = DEFAULT_FONT_SIZE,
        stroke_width: int = DEFAULT_STROKE_WIDTH,
    ) -> None:
        self._contrast_factor = contrast_factor
        self._saturation_factor = saturation_factor
        self._font_size = font_size
        self._stroke_width = stroke_width

    def generate_thumbnail(
        self,
        frame_image_path: str,
        text: str,
        font_path: str,
        output_thumbnail_path: str,
    ) -> str:
        """Enhance contrast/saturation, overlay .ttf text with black border, and save JPEG."""
        with Image.open(frame_image_path) as raw_img:
            image = raw_img.convert("RGB")

        image = ImageEnhance.Contrast(image).enhance(self._contrast_factor)
        image = ImageEnhance.Color(image).enhance(self._saturation_factor)

        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(font_path, size=self._font_size)
        width, height = image.size
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=self._stroke_width)
        text_width = bbox[2] - bbox[0]
        x_pos = max(0, (width - text_width) // 2)
        y_pos = int(height * TEXT_Y_RATIO)

        draw.text(
            (x_pos, y_pos),
            text,
            font=font,
            fill="white",
            stroke_width=self._stroke_width,
            stroke_fill="black",
        )

        target = Path(output_thumbnail_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=JPEG_QUALITY)
        return str(target)
