"""Pillow thumbnail masking, facecam compositing, and text overlay adapter."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from src.domain.constants import THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH

DEFAULT_CONTRAST_FACTOR: float = 1.25
DEFAULT_SATURATION_FACTOR: float = 1.35
DEFAULT_FONT_SIZE: int = 96
DEFAULT_STROKE_WIDTH: int = 6
JPEG_QUALITY: int = 92
TEXT_Y_RATIO: float = 0.78
MASK_FILL_RGB: tuple[int, int, int] = (128, 128, 128)
FACECAM_MARGIN_PX: int = 20
FACECAM_OUTER_BORDER_PX: int = 8
FACECAM_INNER_BORDER_PX: int = 3
FACECAM_OUTER_COLOR: str = "#FFD600"
FACECAM_INNER_COLOR: str = "#FFFFFF"


def _region_to_box(
    width: int,
    height: int,
    region: tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    rx, ry, rw, rh = region
    x0 = max(0, int(round(width * rx)))
    y0 = max(0, int(round(height * ry)))
    x1 = min(width, int(round(width * (rx + rw))))
    y1 = min(height, int(round(height * (ry + rh))))
    return (x0, y0, x1, y1)


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

    def mask_region(
        self,
        source_image_path: str,
        region: tuple[float, float, float, float],
        output_image_path: str,
    ) -> str:
        """Cover normalized (x, y, w, h) region with solid neutral gray and save PNG."""
        with Image.open(source_image_path) as raw_img:
            image = raw_img.convert("RGB")

        box = _region_to_box(*image.size, region)
        draw = ImageDraw.Draw(image)
        draw.rectangle(box, fill=MASK_FILL_RGB)

        target = Path(output_image_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        return str(target)

    def generate_thumbnail(
        self,
        frame_image_path: str,
        text: str,
        font_path: str,
        output_thumbnail_path: str,
        *,
        face_source_path: str | None = None,
        face_region: tuple[float, float, float, float] | None = None,
    ) -> str:
        """Normalize to 1280x720, optionally composite real facecam, overlay text, save JPEG."""
        with Image.open(frame_image_path) as raw_img:
            image = raw_img.convert("RGB")

        if image.size != (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT):
            image = image.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.Resampling.LANCZOS)

        if face_source_path is not None and face_region is not None:
            self._composite_facecam(image, face_source_path, face_region)
        else:
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
        image.save(target, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return str(target)

    @staticmethod
    def _composite_facecam(
        canvas: Image.Image,
        face_source_path: str,
        face_region: tuple[float, float, float, float],
    ) -> None:
        with Image.open(face_source_path) as source_img:
            source_rgb = source_img.convert("RGB")

        crop_box = _region_to_box(*source_rgb.size, face_region)
        face_crop = source_rgb.crop(crop_box)
        target_box = _region_to_box(*canvas.size, face_region)
        target_w = max(1, target_box[2] - target_box[0])
        target_h = max(1, target_box[3] - target_box[1])
        resized_face = face_crop.resize((target_w, target_h), Image.Resampling.LANCZOS)

        paste_x = FACECAM_MARGIN_PX
        paste_y = FACECAM_MARGIN_PX
        draw = ImageDraw.Draw(canvas)
        outer_pad = FACECAM_OUTER_BORDER_PX
        inner_pad = FACECAM_INNER_BORDER_PX
        draw.rectangle(
            (
                paste_x - outer_pad,
                paste_y - outer_pad,
                paste_x + target_w + outer_pad,
                paste_y + target_h + outer_pad,
            ),
            fill=FACECAM_OUTER_COLOR,
        )
        draw.rectangle(
            (
                paste_x - inner_pad,
                paste_y - inner_pad,
                paste_x + target_w + inner_pad,
                paste_y + target_h + inner_pad,
            ),
            fill=FACECAM_INNER_COLOR,
        )
        canvas.paste(resized_face, (paste_x, paste_y))
