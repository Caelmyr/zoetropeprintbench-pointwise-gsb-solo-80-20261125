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


def placement_geometry(source_size, transform, size, physical, calibration=None):
    """Geometry of the placed negative in both pixels and millimetres.

    Returns the effective pixel scale, drawn pixel size, canvas offset and the
    physical (mm) rectangle the source negative covers inside the print cell.
    The calibration layer is a base millimetre translation; the picture
    transform's own offset stays additive on top of it.
    """
    width, height = size
    source_w, source_h = source_size
    user_scale = float(transform.get("scale", 1))
    fit = min(width / source_w, height / source_h)
    scale = fit * user_scale
    drawn_size = (max(1, round(source_w * scale)), max(1, round(source_h * scale)))
    cal = calibration or {}
    shift_x = float(transform.get("shift_x", 0)) + float(cal.get("shift_x", 0))
    shift_y = float(transform.get("shift_y", 0)) + float(cal.get("shift_y", 0))
    sx = shift_x * width / physical[0]
    sy = shift_y * height / physical[1]
    offset = (round((width - drawn_size[0]) / 2 + sx), round((height - drawn_size[1]) / 2 + sy))
    mm_per_source_px = min(physical[0] / source_w, physical[1] / source_h)
    effective_mm_per_px = mm_per_source_px * user_scale
    drawn_mm = (source_w * effective_mm_per_px, source_h * effective_mm_per_px)
    origin_mm = ((physical[0] - drawn_mm[0]) / 2 + shift_x,
                 (physical[1] - drawn_mm[1]) / 2 + shift_y)
    return {
        "scale": scale, "fit": fit, "drawn_size": drawn_size, "offset": offset,
        "mm_per_source_px": mm_per_source_px, "effective_mm_per_px": effective_mm_per_px,
        "drawn_mm": drawn_mm, "origin_mm": origin_mm,
        "shift_x_mm": shift_x, "shift_y_mm": shift_y,
    }


def place(source, transform, size, physical, paper=None, calibration=None):
    artwork = matte(source, transform["matte"])
    if transform["flip"]:
        artwork = ImageOps.mirror(artwork)

    width, height = size
    geo = placement_geometry(artwork.size, transform, size, physical, calibration)
    drawn_size = geo["drawn_size"]
    placed = artwork.convert("RGBa").resize(drawn_size, Image.Resampling.LANCZOS).convert("RGBA")
    offset = geo["offset"]
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
    width, height = canvas_size
    x, y = offset
    if not total:
        base = {"ink_fraction": 0, "cut_fraction": 0, "quiet_fraction": 0, "ink_cut_fraction": 0}
    else:
        margin_x, margin_y = quiet_xy

        def retained(left, top, right, bottom):
            x0, x1 = max(0, left - x), min(alpha.shape[1], right - x)
            y0, y1 = max(0, top - y), min(alpha.shape[0], bottom - y)
            return float(alpha[y0:y1, x0:x1].sum()) if x0 < x1 and y0 < y1 else 0

        inside = retained(0, 0, width, height)
        quiet = retained(margin_x, margin_y, width - margin_x, height - margin_y)
        base = {
            "ink_fraction": round(inside / (width * height), 5),
            "cut_fraction": round(1 - inside / total, 5),
            "quiet_fraction": round((inside - quiet) / total, 5),
            "ink_cut_fraction": round(ink_loss_fraction(placed, offset, canvas_size), 5),
        }
    base.update(source_coverage(placed.size, offset, canvas_size))
    base.update(cut_by_edge(alpha, offset, canvas_size))
    base.update(ink_cut_by_edge(placed, offset, canvas_size))
    return base


def artwork_weights(placed):
    """Pigment weight: alpha for cut-paper PNGs, darkness for opaque photos."""
    rgba = np.asarray(placed).astype(np.float32)
    alpha = rgba[:, :, 3] / 255
    transparent = float((alpha < .04).mean())
    if transparent > .02:
        return alpha
    rgb = rgba[:, :, :3] / 255
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    luminance = linear @ np.array([.2126, .7152, .0722], dtype=np.float32)
    # Ignore bright paper stock; ramp up for mid and dark pigments.
    pigment = np.clip((.82 - luminance) / .55, 0, 1)
    return alpha * pigment


def ink_loss_fraction(placed, offset, canvas_size):
    """Share of visible pigment weight falling outside the print cell."""
    weights = artwork_weights(placed)
    total = float(weights.sum())
    if total <= 1e-6:
        return 0.0
    width, height = canvas_size
    x, y = offset
    rows, cols = weights.shape
    yy, xx = np.nonzero(weights > 1e-4)
    if not len(xx):
        return 0.0
    canvas_x = xx + x
    canvas_y = yy + y
    outside = (canvas_x < 0) | (canvas_x >= width) | (canvas_y < 0) | (canvas_y >= height)
    return float(weights[yy[outside], xx[outside]].sum() / total)


def source_coverage(placed_size, offset, canvas_size):
    """How much of the print cell the original negative rectangle reaches.

    ``uncovered_fraction`` is cell area with no source photograph behind it
    after calibration / offsets / mirroring / scaling — the edge the original
    photo did not capture, as opposed to ink clipped by the print boundary.
    """
    width, height = canvas_size
    placed_w, placed_h = placed_size
    x, y = offset
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + placed_w), min(height, y + placed_h)
    covered = max(0, x1 - x0) * max(0, y1 - y0)
    cell_area = width * height
    uncovered_px = cell_area - covered
    left = max(0, x)
    right = max(0, width - (x + placed_w))
    top = max(0, y)
    bottom = max(0, height - (y + placed_h))
    return {
        "uncovered_fraction": round(max(0, uncovered_px) / cell_area, 5),
        "uncovered_left_px": int(left), "uncovered_right_px": int(right),
        "uncovered_top_px": int(top), "uncovered_bottom_px": int(bottom),
    }


def cut_by_edge(alpha, offset, canvas_size):
    """Share of source ink lost beyond each print-cell edge."""
    total = float(alpha.sum())
    width, height = canvas_size
    if total <= 0:
        return {"cut_left_fraction": 0, "cut_right_fraction": 0,
                "cut_top_fraction": 0, "cut_bottom_fraction": 0}
    x, y = offset
    rows, cols = alpha.shape
    yy, xx = np.nonzero(alpha)
    weights = alpha[yy, xx]
    canvas_x = xx + x
    canvas_y = yy + y
    left = float(weights[canvas_x < 0].sum())
    right = float(weights[canvas_x >= width].sum())
    top = float(weights[canvas_y < 0].sum())
    bottom = float(weights[canvas_y >= height].sum())
    return {
        "cut_left_fraction": round(left / total, 5),
        "cut_right_fraction": round(right / total, 5),
        "cut_top_fraction": round(top / total, 5),
        "cut_bottom_fraction": round(bottom / total, 5),
    }


def ink_cut_by_edge(placed, offset, canvas_size):
    """Pigment-aware share of real artwork lost beyond each print edge."""
    weights = artwork_weights(placed)
    total = float(weights.sum())
    if total <= 1e-6:
        return {"ink_cut_left_fraction": 0, "ink_cut_right_fraction": 0,
                "ink_cut_top_fraction": 0, "ink_cut_bottom_fraction": 0}
    width, height = canvas_size
    x, y = offset
    yy, xx = np.nonzero(weights > 1e-4)
    if not len(xx):
        return {"ink_cut_left_fraction": 0, "ink_cut_right_fraction": 0,
                "ink_cut_top_fraction": 0, "ink_cut_bottom_fraction": 0}
    wx = weights[yy, xx]
    canvas_x = xx + x
    canvas_y = yy + y
    left = float(wx[canvas_x < 0].sum())
    right = float(wx[canvas_x >= width].sum())
    top = float(wx[canvas_y < 0].sum())
    bottom = float(wx[canvas_y >= height].sum())
    return {
        "ink_cut_left_fraction": round(left / total, 5),
        "ink_cut_right_fraction": round(right / total, 5),
        "ink_cut_top_fraction": round(top / total, 5),
        "ink_cut_bottom_fraction": round(bottom / total, 5),
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
