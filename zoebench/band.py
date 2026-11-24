"""Reassemble a photographed old zoetrope band into numbered picture frames.

Each band photo is marked with a rectangle around the visible strip and two
recognised frame boundaries.  The boundary numbers place the strip on the old
band's own frame grid, so overlapping photos (even taken in reverse order)
agree without guessing.  A frame is only offered from a photo that contains
its *complete* interval; anything clipped by the marked strip edge stays an
explicit gap instead of being stitched together from two partial views.

When several photos capture a frame completely, the per-frame source is chosen
by a small dynamic programme that balances reproduced line clarity against the
brightness step between neighbouring frames around the loop.
"""

from datetime import datetime, timezone
from math import hypot
from statistics import fmean

import numpy as np
from PIL import Image, ImageDraw

from image_lab.validation import Invalid

from . import model, raster
from .layout import TEAL
from .store import identity

MAX_CAPTURES = 6
# Completeness is judged on the marked strip: a frame interval that fits
# inside it is whole even when one boundary lies exactly on the strip edge
# (the operator recognised that boundary). Only intervals crossing the edge
# are partial. The tolerance absorbs float rounding.
EDGE_GUARD = 1e-9
MIN_FRAME_PIXELS = 8
# Weights for the per-frame source choice.  Clarity is normalised per frame;
# luminance is linear 0..1, so a 0.1 luminance step costs 0.35.
CLARITY_WEIGHT = 1.0
CONTINUITY_WEIGHT = 3.5
MARGIN_WEIGHT = .15
STRIP_EDGE = 2400


# ------------------------------------------------------------- draft state

def state(draft):
    """Return this draft's band record, creating the default in memory."""
    band = draft.get("band")
    if not isinstance(band, dict):
        band = {}
    band.setdefault("frame_count", 8)
    capture_list = band.get("captures")
    if not isinstance(capture_list, list):
        capture_list = []
    band["captures"] = capture_list
    draft["band"] = band
    return band


def calibrated(registration):
    return (
        isinstance(registration, dict)
        and isinstance(registration.get("quad"), list)
        and len(registration["quad"]) == 4
        and isinstance(registration.get("boundaries"), list)
        and len(registration["boundaries"]) == 2
    )


# ---------------------------------------------------------------- geometry

def bilinear(quad, u, v):
    top_left, top_right, bottom_right, bottom_left = quad
    top = (top_left[0] + (top_right[0] - top_left[0]) * u,
           top_left[1] + (top_right[1] - top_left[1]) * u)
    bottom = (bottom_left[0] + (bottom_right[0] - bottom_left[0]) * u,
              bottom_left[1] + (bottom_right[1] - bottom_left[1]) * u)
    return [top[0] + (bottom[0] - top[0]) * v, top[1] + (bottom[1] - top[1]) * v]


def frame_polygon(quad, start, end):
    """The frame's four corners in normalized original-photo coordinates."""
    return [
        bilinear(quad, start, 0), bilinear(quad, end, 0),
        bilinear(quad, end, 1), bilinear(quad, start, 1),
    ]


def _frame_scale(registration):
    first, second = registration["boundaries"]
    return (second["frame"] - first["frame"]) / (second["position"] - first["position"])


def world_at(registration, position):
    """World coordinate of a strip position; the start of frame k is world k-1."""
    first = registration["boundaries"][0]
    return (first["frame"] - 1) + (position - first["position"]) * _frame_scale(registration)


def position_at(registration, world):
    first = registration["boundaries"][0]
    return first["position"] + (world - (first["frame"] - 1)) / _frame_scale(registration)


def capture_world_span(registration):
    first, second = world_at(registration, 0), world_at(registration, 1)
    return min(first, second), max(first, second)


def frame_span(registration, number):
    """Normalised strip interval covered by old-band frame ``number``."""
    low = position_at(registration, number - 1)
    high = position_at(registration, number)
    return (min(low, high), max(low, high))


def frames_in_capture(registration, frame_count):
    """How each old-band frame sits inside this marked strip."""
    rows = []
    for number in range(1, frame_count + 1):
        start, end = frame_span(registration, number)
        raw_low, raw_high = position_at(registration, number - 1), position_at(registration, number)
        width = max(end - start, 1e-9)
        overlap = min(end, 1.) - max(start, 0.)
        rows.append({
            "frame": number,
            "start": round(start, 6),
            "end": round(end, 6),
            "margin": round(min(start, 1. - end), 6),
            "overlap": round(min(1., max(0., overlap / width)), 6),
            "clip": _clip(raw_low, raw_high),
            "complete": start >= -EDGE_GUARD and end <= 1. + EDGE_GUARD,
        })
    return rows


def _clip(raw_low, raw_high):
    low, high = min(raw_low, raw_high), max(raw_low, raw_high)
    if low < 0 and high > 1:
        return "both"
    if low < 0:
        return "start"
    if high > 1:
        return "end"
    return None


def rectified(photo, quad):
    """Flatten the marked strip into a straight, upright image."""
    width, height = photo.size
    points = [(x * width, y * height) for x, y in quad]
    top = hypot(points[1][0] - points[0][0], points[1][1] - points[0][1])
    bottom = hypot(points[2][0] - points[3][0], points[2][1] - points[3][1])
    left = hypot(points[3][0] - points[0][0], points[3][1] - points[0][1])
    right = hypot(points[2][0] - points[1][0], points[2][1] - points[1][1])
    output_width = max(8, round(max(top, bottom)))
    output_height = max(8, round(max(left, right)))
    factor = min(1., STRIP_EDGE / max(output_width, output_height))
    if factor < 1:
        output_width = max(8, round(output_width * factor))
        output_height = max(8, round(output_height * factor))
    # Pillow's QUAD transform expects top-left, bottom-left, bottom-right, top-right.
    corners = [points[0], points[3], points[2], points[1]]
    data = [coordinate for point in corners for coordinate in point]
    return photo.convert("RGBA").transform(
        (output_width, output_height), Image.Transform.QUAD, data=data,
        resample=Image.Resampling.BICUBIC,
    )


def crop_frame(strip, start, end):
    width, height = strip.size
    left = max(0, min(width - 1, round(start * width)))
    right = max(left + 1, min(width, round(end * width)))
    return strip.crop((left, 0, right, height))


def take_thumbnail(draft, choice, library, limit=260):
    """A small preview of one candidate take, for source comparison."""
    band = state(draft)
    capture = next((row for row in band["captures"] if row["id"] == choice["capture"]), None)
    if capture is None or not calibrated(capture.get("registration")):
        raise Invalid("That band photo is no longer marked in this drum.")
    photo = raster.decode(library.blob(capture["blob"]).read_bytes())
    crop = crop_frame(rectified(photo, capture["registration"]["quad"]),
                      choice["start"], choice["end"])
    crop.thumbnail((limit, limit))
    return crop.convert("RGB")


def overlay(library, source, limit=1400):
    """The original band photo with this frame's take outlined and labelled."""
    photo = raster.decode(library.blob(source["source_blob"]).read_bytes())
    width, height = photo.size
    scale = min(1., limit / max(width, height))
    if scale < 1:
        photo = photo.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                             Image.Resampling.LANCZOS)
    canvas = photo.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    polygon = [(round(x * canvas.width), round(y * canvas.height)) for x, y in source["quad"]]
    draw.polygon(polygon, outline=TEAL)
    draw.line(polygon + [polygon[0]], fill=TEAL, width=3)
    label = f"Frame {source['slot']:02}"
    left = min(point[0] for point in polygon)
    top = min(point[1] for point in polygon)
    box = draw.textbbox((0, 0), label)
    draw.rectangle((left, max(0, top - (box[3] - box[1]) - 8),
                    left + (box[2] - box[0]) + 8, max(0, top - (box[3] - box[1]) - 8) + (box[3] - box[1]) + 6),
                   fill=TEAL)
    draw.text((left + 4, max(0, top - (box[3] - box[1]) - 6)), label, fill="white")
    return canvas


# ------------------------------------------------------------- validation

def registration(value):
    """Validate a marked strip and its two named boundaries."""
    if not isinstance(value, dict):
        raise Invalid("Mark the band region and both frame boundaries first.")
    quad = value.get("quad")
    if not isinstance(quad, list) or len(quad) != 4:
        raise Invalid("The band region needs four corners.")
    points = []
    for point in quad:
        if not isinstance(point, list) or len(point) != 2:
            raise Invalid("Each band region corner needs two coordinates.")
        points.append([model.number(point[0], "band region coordinate", 0, 1),
                       model.number(point[1], "band region coordinate", 0, 1)])
    area = 0.
    for index in range(4):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % 4]
        area += x1 * y2 - x2 * y1
    if abs(area) / 2 < .0002:
        raise Invalid("Draw a wider band region around the visible strip.")
    if min(hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]),
           hypot(points[3][0] - points[0][0], points[3][1] - points[0][1])) < .01:
        raise Invalid("Draw a wider band region around the visible strip.")

    boundaries = value.get("boundaries")
    if not isinstance(boundaries, list) or len(boundaries) != 2:
        raise Invalid("Name two frame boundaries inside the strip.")
    rows = []
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            raise Invalid("Each boundary needs its old-band frame number and position.")
        rows.append({
            "frame": model.integer(boundary.get("frame"), "boundary frame number", 1, model.MAX_IMAGES),
            "position": model.number(boundary.get("position"), "boundary position", 0, 1),
        })
    if rows[0]["frame"] == rows[1]["frame"]:
        raise Invalid("Name two different frame boundaries on the old band.")
    if abs(rows[0]["position"] - rows[1]["position"]) < .02:
        raise Invalid("Move the two boundary lines apart.")
    # The pair is kept in the order the operator named it, so a strip that
    # reads right-to-left in the photo still maps to the old band correctly.
    return {"quad": points, "boundaries": rows}


def validate_frame_count(value):
    return model.integer(value, "frames on the old band", 2, model.MAX_IMAGES)


# ------------------------------------------------------------- measurement

def clarity_and_tone(crop):
    """Line clarity on a common width, plus linear mean luminance."""
    sample = crop.convert("RGB")
    if sample.width > 150:
        height = max(1, round(sample.height * 150 / sample.width))
        sample = sample.resize((150, height), Image.Resampling.LANCZOS)
    gray = raster.luminance(sample).astype(np.float64)
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0., float(gray.mean()) if gray.size else 0.
    laplacian = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1]
                 + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(laplacian.var()), float(gray.mean())


# ---------------------------------------------------------------- planning

def _adjacent(first, second, frame_count):
    return second == first + 1 or (first == frame_count and second == 1)


def _jumps(order, chosen, frame_count):
    values = []
    for first, second in zip(order, order[1:] + order[:1]):
        if first in chosen and second in chosen and _adjacent(first, second, frame_count):
            values.append(abs(chosen[first]["luminance"] - chosen[second]["luminance"]))
    return values


def _solve(order, candidates, frame_count):
    """Exact cyclic pick of one candidate per present frame."""
    if not order:
        return {}
    if len(order) == 1:
        return {order[0]: min(candidates[order[0]], key=lambda row: row["unary"])}
    best_total, best = float("inf"), None
    for first_index, first in enumerate(candidates[order[0]]):
        costs = [[float("inf")] * len(candidates[number]) for number in order]
        backs = [[-1] * len(candidates[number]) for number in order]
        costs[0][first_index] = first["unary"]
        for position in range(1, len(order)):
            earlier_number, number = order[position - 1], order[position]
            linked = _adjacent(earlier_number, number, frame_count)
            for index, row in enumerate(candidates[number]):
                for earlier_index, earlier in enumerate(candidates[earlier_number]):
                    if costs[position - 1][earlier_index] == float("inf"):
                        continue
                    value = costs[position - 1][earlier_index] + row["unary"]
                    if linked:
                        value += CONTINUITY_WEIGHT * abs(earlier["luminance"] - row["luminance"])
                    if value < costs[position][index]:
                        costs[position][index] = value
                        backs[position][index] = earlier_index
        for last_index, last in enumerate(candidates[order[-1]]):
            value = costs[-1][last_index]
            if _adjacent(order[-1], order[0], frame_count):
                value += CONTINUITY_WEIGHT * abs(last["luminance"] - first["luminance"])
            if value < best_total:
                chosen, index = {}, last_index
                for position in range(len(order) - 1, -1, -1):
                    chosen[order[position]] = candidates[order[position]][index]
                    index = backs[position][index]
                best_total, best = value, chosen
    return best


def _alternative(order, candidates):
    """Per-frame sharpest pick, ignoring loop brightness continuity."""
    return {
        number: min(candidates[number], key=lambda row: (-row["relative_clarity"], -row["margin"]))
        for number in order
    }


def _summarise(order, chosen, frame_count):
    if not chosen:
        return {"mean_clarity": None, "mean_relative_clarity": None,
                "max_neighbour_jump": None, "mean_neighbour_jump": None, "neighbour_pairs": 0}
    jumps = _jumps(order, chosen, frame_count)
    return {
        "mean_clarity": round(fmean(row["clarity"] for row in chosen.values()), 6),
        "mean_relative_clarity": round(fmean(row["relative_clarity"] for row in chosen.values()), 6),
        "mean_neighbour_jump": round(fmean(jumps), 6) if jumps else None,
        "max_neighbour_jump": round(max(jumps), 6) if jumps else None,
        "neighbour_pairs": len(jumps),
    }


def _overlap_note(number, captures):
    seen = [f"photo {capture['index']}" for capture in captures]
    if not seen:
        return f"Frame {number} is not visible in any band photo."
    names = ", ".join(seen)
    return (f"Frame {number} is only partly visible in {names}; no photo captures the whole frame "
            "inside the marked strip, so it stays an empty beat.")


def evaluate(draft, library):
    """Recommend one source per old-band frame; uncaptured frames stay gaps."""
    band = state(draft)
    frame_count = band["frame_count"]
    captures = band["captures"]
    candidates = {number: [] for number in range(1, frame_count + 1)}
    capture_rows = []

    for index, capture in enumerate(captures, 1):
        row = {
            "id": capture["id"], "index": index, "photo": capture["filename"], "title": capture.get("title", ""),
            "ready": False, "world_from": None, "world_to": None, "frames": [], "note": "",
        }
        registration_value = capture.get("registration")
        if not calibrated(registration_value):
            row["note"] = "Mark the band region and both frame boundaries to use this photo."
            capture_rows.append(row)
            continue
        photo = raster.decode(library.blob(capture["blob"]).read_bytes())
        strip = rectified(photo, registration_value["quad"])
        span = capture_world_span(registration_value)
        frames = frames_in_capture(registration_value, frame_count)
        clipped = []
        for frame in frames:
            crop = None
            if frame["complete"]:
                crop = crop_frame(strip, frame["start"], frame["end"])
                if crop.width >= MIN_FRAME_PIXELS:
                    clarity, tone = clarity_and_tone(crop)
                    candidates[frame["frame"]].append({
                        "capture": capture["id"], "capture_index": index, "photo": capture["filename"],
                        "clarity": round(clarity, 6), "luminance": round(tone, 6),
                        "margin": frame["margin"], "pixel_width": crop.width,
                        "start": frame["start"], "end": frame["end"],
                        "quad": frame_polygon(registration_value["quad"], frame["start"], frame["end"]),
                    })
                    row_frame = {**frame, "offered": True, "reason": None}
                else:
                    row_frame = {**frame, "complete": False, "offered": False, "reason": "too few pixels"}
            else:
                row_frame = {**frame, "offered": False, "reason": _reason(frame)}
            if frame["overlap"] > 0 and not row_frame["offered"]:
                clipped.append(frame["frame"])
            row["frames"].append(row_frame)
        row.update({
            "ready": True,
            "world_from": round(span[0], 3), "world_to": round(span[1], 3),
            "note": ("Frames " + ", ".join(str(number) for number in clipped) + " touch the strip edge.")
            if clipped else "",
        })
        capture_rows.append(row)

    order = [number for number in range(1, frame_count + 1) if candidates[number]]
    for number in order:
        best = max(row["clarity"] for row in candidates[number])
        for row in candidates[number]:
            row["relative_clarity"] = round(row["clarity"] / best, 6) if best > 0 else 0.
            # A take whose frame boundary sits right against the marked edge is
            # slightly riskier: the outermost line may be shaved by the drawing.
            edge_risk = max(0., .01 - row["margin"]) / .01
            row["unary"] = round(-CLARITY_WEIGHT * row["relative_clarity"]
                                 + MARGIN_WEIGHT * edge_risk, 6)

    chosen = _solve(order, candidates, frame_count)
    alternative = _alternative(order, candidates)

    slots, missing, partial_map = [], [], {}
    for row in capture_rows:
        for frame in row["frames"]:
            if frame["overlap"] > 0 and not frame.get("offered"):
                partial_map.setdefault(frame["frame"], []).append(row)
    for number in range(1, frame_count + 1):
        source = chosen.get(number)
        if source is not None:
            slots.append({
                "frame": number, "status": "taken",
                "source": _public_choice(source),
                "alternatives": [_public_choice(row) for row in candidates[number]],
                "note": "",
            })
        else:
            missing.append(number)
            slots.append({
                "frame": number, "status": "missing", "source": None, "alternatives": [],
                "note": _overlap_note(number, partial_map.get(number, [])),
            })

    recommended = _summarise(order, chosen, frame_count)
    sharpest = _summarise(order, alternative, frame_count)
    summary = {
        "frames": frame_count, "taken": len(order), "missing": len(missing),
        "missing_frames": missing, "ready_captures": sum(1 for row in capture_rows if row["ready"]),
        "clarity_weight": CLARITY_WEIGHT, "continuity_weight": CONTINUITY_WEIGHT,
        "edge_guard": EDGE_GUARD,
        **{key: value for key, value in recommended.items()},
        "alternative_mean_relative_clarity": sharpest["mean_relative_clarity"],
        "alternative_max_neighbour_jump": sharpest["max_neighbour_jump"],
        "recommendation_differs_from_sharpest": chosen != alternative,
        "note": ("Sources balance line clarity with the brightness step between neighbouring frames "
                 "around the loop. Frames clipped by a photo edge are left as gaps."),
    }
    return {"frame_count": frame_count, "captures": capture_rows, "slots": slots, "summary": summary}


def _reason(frame):
    if frame["clip"] == "start":
        return "The frame crosses the start of the marked strip."
    if frame["clip"] == "end":
        return "The frame crosses the end of the marked strip."
    if frame["clip"] == "both":
        return "The marked strip falls inside this frame."
    return "The frame reaches the marked strip edge."


def _box(polygon):
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]


# ---------------------------------------------------------------- adoption

def _public_choice(row):
    return {
        "capture": row["capture"], "capture_index": row["capture_index"], "photo": row["photo"],
        "clarity": row["clarity"], "relative_clarity": row["relative_clarity"],
        "luminance": row["luminance"], "margin": row["margin"], "pixel_width": row["pixel_width"],
        "quad": row["quad"], "box": _box(row["quad"]), "start": row["start"], "end": row["end"],
    }


def apply_overrides(plan, requested):
    """Replace recommended sources with the operator's explicit picks."""
    if not isinstance(requested, dict):
        raise Invalid("Frame sources must map old-band numbers to band photos.")
    slots = {slot["frame"]: slot for slot in plan["slots"]}
    for number, capture_key in requested.items():
        if not isinstance(number, str) or not number.isdigit():
            raise Invalid("Frame sources must name old-band frame numbers.")
        slot = slots.get(int(number))
        if slot is None:
            raise Invalid(f"Old-band frame {number} is outside this sequence.")
        if capture_key is None:
            if slot["status"] == "taken":
                slot.update({"status": "missing", "source": None, "note": "Left blank by request."})
            continue
        match = next((row for row in slot["alternatives"] if row["capture"] == capture_key), None)
        if match is None:
            raise Invalid(f"Frame {number} has no complete take in that band photo.")
        slot.update({"status": "taken", "source": dict(match), "note": ""})
    return plan


def adopt(draft, plan, frames):
    """Record exactly which source each numbered slot was taken from."""
    band = state(draft)
    band["frame_count"] = plan["frame_count"]
    band["adopted"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": plan["summary"],
        "slots": [
            {"frame": slot["frame"], "status": slot["status"],
             "capture": slot["source"]["capture"] if slot["source"] else None,
             "photo": slot["source"]["photo"] if slot["source"] else None}
            for slot in plan["slots"]
        ],
    }
    band["adopted"]["missing_frames"] = [row["frame"] for row in band["adopted"]["slots"]
                                         if row["status"] != "taken"]
    return band["adopted"]


def frames_from_plan(draft, plan, library):
    """Crop each recommended frame, leaving uncaptured beats as gaps."""
    band = state(draft)
    frame_count = plan["frame_count"]
    if frame_count > model.MAX_IMAGES:
        raise Invalid(f"An old band can hold at most {model.MAX_IMAGES} frames in this drum.")
    captures = {capture["id"]: capture for capture in band["captures"]}
    pieces, strips = {}, {}

    def strip_for(capture_key):
        if capture_key not in strips:
            capture = captures.get(capture_key)
            if capture is None:
                raise Invalid("A chosen band photo is no longer in this drum.")
            photo = raster.decode(library.blob(capture["blob"]).read_bytes())
            strips[capture_key] = rectified(photo, capture["registration"]["quad"])
        return strips[capture_key]

    for slot in plan["slots"]:
        if slot["status"] != "taken":
            continue
        source = slot["source"]
        pieces[slot["frame"]] = crop_frame(
            strip_for(source["capture"]), source["start"], source["end"])
    if not pieces:
        raise Invalid("No old-band frame is completely captured by a marked photo.")

    frames = []
    for slot in plan["slots"]:
        number = slot["frame"]
        if slot["status"] != "taken":
            frames.append({
                "id": identity(), "title": f"Old band {number:02} · not captured",
                "filename": "", "blob": None, "width": 0, "height": 0,
                "transform": model.frame_transform({}), "missing": True,
                "band": {"slot": number, "missing": True,
                         "frame_count": frame_count, "note": slot["note"]},
            })
            continue
        piece = pieces[number]
        source = slot["source"]
        capture = captures[source["capture"]]
        frames.append({
            "id": identity(), "title": f"Old band {number:02}",
            "filename": f"old-band-{number:02}.png", "blob": library.keep(raster.png(piece)),
            "width": piece.width, "height": piece.height,
            "transform": model.frame_transform({}),
            "band": {
                "slot": number, "missing": False, "frame_count": frame_count,
                "capture": source["capture"], "capture_index": source["capture_index"],
                "photo": source["photo"], "source_blob": capture["blob"],
                "photo_size": [capture["width"], capture["height"]],
                "quad": source["quad"], "box": source["box"],
                "clarity": source["clarity"], "luminance": source["luminance"],
            },
        })
    return frames
