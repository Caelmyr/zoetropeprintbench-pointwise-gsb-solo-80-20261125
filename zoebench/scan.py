"""Read apparent open slots from a photographed, flat-mounted strip."""

import numpy as np
from PIL import Image, ImageDraw

from image_lab.validation import Invalid

from . import raster
from .layout import ORANGE, TEAL


def light_runs(profile, cutoff):
    above = profile >= cutoff
    boundaries = np.flatnonzero(np.diff(np.pad(above.astype(np.int8), 1)))
    return [(int(left), int(right)) for left, right in zip(boundaries[::2], boundaries[1::2])]


def _points(crop, source):
    x0 = round(crop[0] * source.width)
    y0 = round(crop[1] * source.height)
    x1 = round(crop[2] * source.width)
    y1 = round(crop[3] * source.height)
    if min(x1 - x0, y1 - y0) < 4:
        raise Invalid("The scan selection is too small.")
    return x0, y0, x1, y1


def extract(source, registration, bright_threshold):
    x0, y0, x1, y1 = _points(registration["crop"], source)
    crop = source.crop((x0, y0, x1, y1))
    prepared = raster.on_stock(crop, "black")
    luma = raster.luminance(prepared)
    profile = np.quantile(luma, .48, axis=0)
    mm_per_pixel = registration["scan_span_mm"] / crop.width
    origin = registration["origin_mm"]
    intervals = light_runs(profile, bright_threshold)
    annotated = source.convert("RGB")
    scale = min(1, 1500 / max(annotated.size))
    if scale < 1:
        annotated = annotated.resize(tuple(max(1, round(dim * scale)) for dim in annotated.size), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(annotated)
    draw.rectangle(tuple(round(q * scale) for q in (x0, y0, x1 - 1, y1 - 1)), outline=TEAL, width=2)
    readings = []
    for left, right in intervals:
        if right - left < 2:
            continue
        center = (left + right) / 2
        partial = left == 0 or right == crop.width
        start_mm = left * mm_per_pixel + origin
        end_mm = right * mm_per_pixel + origin
        color = ORANGE if partial else TEAL
        index = len(readings) + 1
        draw.rectangle(
            (round((x0 + left) * scale), round(y0 * scale), round((x0 + right - 1) * scale), round((y1 - 1) * scale)),
            outline=color, width=2,
        )
        draw.text((round((x0 + center) * scale), max(0, round(y0 * scale) - 12)), str(index), fill=color, anchor="mt")
        readings.append({
            "slot": index, "start_mm": round(start_mm, 4), "end_mm": round(end_mm, 4),
            "center_mm": round((start_mm + end_mm) / 2, 4), "width_mm": round(end_mm - start_mm, 4),
            "partial": partial, "original_x": round(x0 + center, 3),
        })

    complete = [row for row in readings if not row["partial"]]
    summary = {
        "supported_slots": len(complete),
        "partial_slots": len(readings) - len(complete),
        "minimum_measured_width_mm": min((row["width_mm"] for row in complete), default=None),
        "maximum_measured_width_mm": max((row["width_mm"] for row in complete), default=None),
        "scan_sample_mm_per_px": round(mm_per_pixel, 5),
        "threshold": bright_threshold,
    }
    return annotated, summary, readings
