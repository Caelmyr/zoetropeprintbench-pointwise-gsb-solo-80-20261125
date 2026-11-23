"""Import and transform frame artwork while preserving transparent boundaries."""

from io import BytesIO

import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_BYTES = 18 * 1024 * 1024
MAX_PIXELS = 12_000_000
INPUT_FORMATS = {"JPEG", "PNG", "TIFF", "WEBP"}
PAPER = {"white": (255, 255, 255), "ivory": (250, 245, 225), "black": (24, 26, 30)}


def decode(data):
    if not data or len(data) > MAX_BYTES:
        raise Invalid("Use a nonempty image of at most 18 MiB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in INPUT_FORMATS or getattr(image, "n_frames", 1) != 1:
                raise Invalid("Use a single PNG, JPEG, TIFF or WebP image.")
            width, height = image.size
            if min(width, height) < 8 or width * height > MAX_PIXELS:
                raise Invalid("Use an image between 8×8 pixels and 12 megapixels.")
            oriented = ImageOps.exif_transpose(image)
            rgba = oriented.convert("RGBA")
            profile = image.info.get("icc_profile")
            if profile:
                try:
                    rgb = ImageCms.profileToProfile(oriented.convert("RGB"), BytesIO(profile), ImageCms.createProfile("sRGB"))
                    rgba = rgb.convert("RGBA")
                    rgba.putalpha(oriented.convert("RGBA").getchannel("A"))
                except (OSError, ValueError, ImageCms.PyCMSError):
                    pass
            return rgba
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as error:
        raise Invalid("The source image could not be decoded.") from error


def png(image, dpi=None):
    stream = BytesIO()
    options = {"dpi": (dpi, dpi)} if dpi is not None else {}
    image.save(stream, format="PNG", **options)
    return stream.getvalue()


def matte(image, kind):
    if kind == "original":
        return image.copy()
    values = np.asarray(image, dtype=np.float32) / 255
    colors = values[:, :, :3]
    if kind == "white":
        strength = 1 - np.min(colors, axis=2)
    else:
        strength = np.max(colors, axis=2)
    mask = np.clip((strength - .025) / .19, 0, 1)
    if kind == "white":
        colors = np.clip((colors - 1 + mask[:, :, None]) / np.maximum(mask[:, :, None], .001), 0, 1)
    else:
        colors = np.clip(colors / np.maximum(mask[:, :, None], .001), 0, 1)
    values[:, :, :3] = colors
    values[:, :, 3] *= mask
    return Image.fromarray(np.round(values * 255).astype(np.uint8), "RGBA")


def place(source, transform, size, physical, paper=None):
    artwork = matte(source, transform["matte"])
    if transform["flip"]:
        artwork = ImageOps.mirror(artwork)

    width, height = size
    scale = min(width / artwork.width, height / artwork.height) * transform["scale"]
    drawn_size = (max(1, round(artwork.width * scale)), max(1, round(artwork.height * scale)))
    placed = artwork.convert("RGBa").resize(drawn_size, Image.Resampling.LANCZOS).convert("RGBA")
    sx = transform["shift_x"] * width / physical[0]
    sy = transform["shift_y"] * height / physical[1]
    offset = (round((width - placed.width) / 2 + sx), round((height - placed.height) / 2 + sy))
    pixel = 0 if paper is None else (*paper, 255)
    canvas = Image.new("RGBA", size, pixel)
    canvas.alpha_composite(placed, dest=offset)
    return canvas, placed, offset


def on_stock(image, stock):
    paper = Image.new("RGBA", image.size, (*PAPER[stock], 255))
    paper.alpha_composite(image)
    return paper.convert("RGB")


def contact_slices(image, rows, columns, requested, gutter):
    width, height = image.size
    cells = []
    for index in range(requested):
        row, column = divmod(index, columns)
        left = round(width * column / columns) + gutter
        right = round(width * (column + 1) / columns) - gutter
        top = round(height * row / rows) + gutter
        bottom = round(height * (row + 1) / rows) - gutter
        if min(right - left, bottom - top) < 8:
            raise Invalid("Contact-sheet cells need at least 8 pixels in each direction after trimming.")
        cells.append(image.crop((left, top, right, bottom)))
    return cells


def mask_visibility(placed, offset, canvas_size, quiet_xy):
    rgba = np.asarray(placed)
    alpha = rgba[:, :, 3].astype(np.float64) / 255
    total = float(alpha.sum())
    if not total:
        return {"ink_fraction": 0, "cut_fraction": 0, "quiet_fraction": 0}
    x, y = offset
    width, height = canvas_size
    margin_x, margin_y = quiet_xy

    def retained(left, top, right, bottom):
        x0, x1 = max(0, left - x), min(alpha.shape[1], right - x)
        y0, y1 = max(0, top - y), min(alpha.shape[0], bottom - y)
        return float(alpha[y0:y1, x0:x1].sum()) if x0 < x1 and y0 < y1 else 0

    inside = retained(0, 0, width, height)
    quiet = retained(margin_x, margin_y, width - margin_x, height - margin_y)
    return {
        "ink_fraction": round(inside / (width * height), 5),
        "cut_fraction": round(1 - inside / total, 5),
        "quiet_fraction": round((inside - quiet) / total, 5),
    }


def rgb_array(image):
    return np.asarray(image.convert("RGB"), dtype=np.float32) / 255


def linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def encode_linear(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)


def rgb_image(values):
    return Image.fromarray(np.round(np.clip(values, 0, 1) * 255).astype(np.uint8), "RGB")


def luminance(image):
    rgb = linear(rgb_array(image))
    return rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
