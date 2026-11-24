"""Landmark-guided translation suggestions for camera-jittered cut-paper frames.

The user marks fixed scenery (thin moon, ground line) on one reference frame.
Only those rectangles are matched in the other negatives, so the moving bird
can never drive the estimate.  Frames whose marks cannot be matched reliably
are reported as unresolved instead of being guessed from whole-image motion.
"""

from math import hypot

import cv2
import numpy as np

from . import layout, raster
from .model import RELIABLE_STATUSES

SCORE_MIN = .5
MARGIN_MIN = .09
AGREEMENT_FRACTION = .02
AGREEMENT_FLOOR_PX = 4.0


def structure_signal(image):
    """Edge + dark-ink signal, robust to paper tone and transparent boundaries."""
    rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255
    rgb = rgba[:, :, :3].astype(np.float32)
    paper = np.full_like(rgb, 247)
    composite = rgb * alpha + paper * (1 - alpha)
    gray = cv2.cvtColor(composite.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    balanced = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    gx = cv2.Sobel(balanced, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(balanced, cv2.CV_32F, 0, 1, ksize=3)
    edges = cv2.normalize(cv2.magnitude(gx, gy), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    dark = cv2.normalize(255 - balanced, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return cv2.addWeighted(edges, .65, dark, .35, 0)


def _refine_peak(response, x, y):
    height, width = response.shape
    dx = dy = 0.0
    if 0 < x < width - 1:
        left, center, right = float(response[y, x - 1]), float(response[y, x]), float(response[y, x + 1])
        denom = left - 2 * center + right
        if abs(denom) > 1e-9:
            dx = max(-1.0, min(1.0, .5 * (left - right) / denom))
    if 0 < y < height - 1:
        top, center, bottom = float(response[y - 1, x]), float(response[y, x]), float(response[y + 1, x])
        denom = top - 2 * center + bottom
        if abs(denom) > 1e-9:
            dy = max(-1.0, min(1.0, .5 * (top - bottom) / denom))
    return x + dx, y + dy


def match_region(ref_signal, target_signal, box, max_shift_px):
    """Return normalized displacement of one mark from reference to target."""
    rh, rw = ref_signal.shape
    th, tw = target_signal.shape
    x0 = int(round(box[0] * rw))
    y0 = int(round(box[1] * rh))
    x1 = int(round(box[2] * rw))
    y1 = int(round(box[3] * rh))
    template = ref_signal[y0:y1, x0:x1]
    if template.shape[0] < 5 or template.shape[1] < 5 or float(template.std()) < 2.5:
        return None

    nominal_x0 = int(round(box[0] * tw))
    nominal_y0 = int(round(box[1] * th))
    nominal_x1 = int(round(box[2] * tw))
    nominal_y1 = int(round(box[3] * th))
    bound = max(2, int(np.ceil(max_shift_px)))
    sx0 = max(0, nominal_x0 - bound)
    sy0 = max(0, nominal_y0 - bound)
    sx1 = min(tw, nominal_x1 + bound)
    sy1 = min(th, nominal_y1 + bound)
    roi = target_signal[sy0:sy1, sx0:sx1]
    if roi.shape[0] < template.shape[0] + 2 or roi.shape[1] < template.shape[1] + 2:
        return None

    response = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, max_location = cv2.minMaxLoc(response)
    px, py = max_location
    height, width = template.shape
    cv2.rectangle(
        response,
        (max(0, px - width // 3), max(0, py - height // 3)),
        (min(response.shape[1] - 1, px + width // 3), min(response.shape[0] - 1, py + height // 3)),
        -1, -1,
    )
    second = float(response.max()) if response.size else -1.0
    fx, fy = _refine_peak(response, px, py)
    match_x = sx0 + fx
    match_y = sy0 + fy
    return {
        "dx": match_x / tw - box[0],
        "dy": match_y / th - box[1],
        "score": float(max_value),
        "margin": float(max_value - second),
        "second": second,
    }


def cell_box_to_source(box, transform, source_size, cell_width_mm, cell_height_mm):
    """Inverse-map a print-cell normalized rectangle into source-negative space."""
    sw, sh = source_size
    scale = min(cell_width_mm / sw, cell_height_mm / sh) * float(transform["scale"])
    drawn_w, drawn_h = sw * scale, sh * scale
    origin_x = (cell_width_mm - drawn_w) / 2 + float(transform["shift_x"])
    origin_y = (cell_height_mm - drawn_h) / 2 + float(transform["shift_y"])

    def invert(nx, ny):
        xmm, ymm = nx * cell_width_mm, ny * cell_height_mm
        source_x = (xmm - origin_x) / scale
        source_y = (ymm - origin_y) / scale
        if transform["flip"]:
            source_x = sw - source_x
        return source_x / sw, source_y / sh

    a = invert(box[0], box[1])
    b = invert(box[2], box[3])
    x1, x2 = sorted((a[0], b[0]))
    y1, y2 = sorted((a[1], b[1]))
    return [max(0, x1), max(0, y1), min(1, x2), min(1, y2)]


def _confidence(score, margin, spread_px, agree_px):
    sharpness = .65 + .35 * min(1.0, max(0.0, margin) / .3)
    agreement = .75 + .25 * max(0.0, 1 - spread_px / agree_px)
    return round(min(1.0, max(0.0, score)) * sharpness * agreement, 3)


def _coverage_for_shift(image, frame, track, cell, shift_x, shift_y):
    size = layout.screen_size(track, cell)
    calibration = {"shift_x": shift_x, "shift_y": shift_y}
    _canvas, placed, offset = layout.paint_cell(
        image, frame, track, cell, size[0], size[1], calibration=calibration,
    )
    action_margin = frame.get("_action_margin_mm", 1.5)
    margin_x = round(action_margin / cell.width * size[0])
    margin_y = round(action_margin / track.height * size[1])
    return raster.mask_visibility(placed, offset, size, (margin_x, margin_y))


def suggest_plan(draft, load_source, reference_index, regions, space, max_shift_mm, cut_tolerance):
    """Compute one translation proposal per frame.

    ``load_source(frame)`` returns a decoded RGBA PIL image.  ``regions`` are
    normalized rectangles; ``space`` is either "cell" (print framed) or
    "source" (negative coordinates).
    """
    settings = draft["settings"]
    frames = draft["frames"]
    count = len(frames)
    track = layout.uniform_track(count, settings)
    ref_frame = frames[reference_index]
    ref_image = load_source(ref_frame)
    ref_signal = structure_signal(ref_image)

    resolved_regions = []
    reference_blank = []
    rh, rw = ref_signal.shape
    for region in regions:
        box = region["box"] if isinstance(region, dict) else region
        if space == "cell":
            box = cell_box_to_source(box, ref_frame["transform"], ref_image.size,
                                     track.cells[reference_index].width, track.height)
        resolved_regions.append(box)
        x0, y0 = int(round(box[0] * rw)), int(round(box[1] * rh))
        x1, y1 = int(round(box[2] * rw)), int(round(box[3] * rh))
        template = ref_signal[y0:y1, x0:x1]
        reference_blank.append(template.size < 25 or float(template.std()) < 2.5)

    rows = []
    unresolved_indexes = []
    for index, (frame, cell) in enumerate(zip(frames, track.cells, strict=True)):
        if index == reference_index:
            sw, sh = ref_image.size
            ref_coverage = _coverage_for_shift(
                ref_image, {**frame, "_action_margin_mm": settings["action_margin"]},
                track, cell, 0, 0,
            )
            rows.append({
                "frame": frame["id"], "index": index, "title": frame["title"],
                "status": "reference", "reliable": True, "apply": True,
                "shift_x_mm": 0.0, "shift_y_mm": 0.0,
                "alignment_shift_x_mm": 0.0, "alignment_shift_y_mm": 0.0,
                "measured_x_mm": 0.0, "measured_y_mm": 0.0,
                "dx": 0.0, "dy": 0.0, "shift_x_px": 0.0, "shift_y_px": 0.0,
                "confidence": 1.0, "score": 1.0, "reason": None, "edge_limited": False,
                "regions": [],
                "coverage": ref_coverage,
            })
            continue

        image = load_source(frame)
        sw, sh = image.size
        f_mm_per_px = min(cell.width / sw, track.height / sh) * float(frame["transform"]["scale"])
        bound_px = max_shift_mm / max(f_mm_per_px, 1e-9)
        agree_px = max(AGREEMENT_FLOOR_PX, AGREEMENT_FRACTION * min(sw, sh))
        target_signal = structure_signal(image)
        matches = []
        region_readings = []
        for region_index, box in enumerate(resolved_regions):
            reading = match_region(ref_signal, target_signal, box, bound_px)
            if reading is None:
                region_readings.append({"score": None, "matched": False,
                                        "reference_blank": reference_blank[region_index]})
                continue
            good = reading["score"] >= SCORE_MIN and reading["margin"] >= MARGIN_MIN
            px_shift = reading["dx"] * sw
            py_shift = reading["dy"] * sh
            region_readings.append({
                "score": round(reading["score"], 4), "margin": round(reading["margin"], 4),
                "shift_x_px": round(px_shift, 2), "shift_y_px": round(py_shift, 2),
                "matched": good,
            })
            if good:
                matches.append(reading)

        if not matches:
            reason = "blank_reference" if all(reference_blank) else "weak_reference"
            unresolved_indexes.append(index)
            rows.append(_unresolved_row(frame, index, region_readings, reason))
            continue

        dxs = sorted(item["dx"] for item in matches)
        dys = sorted(item["dy"] for item in matches)
        mid = len(matches) // 2
        dx = dxs[mid] if len(dxs) % 2 else .5 * (dxs[mid - 1] + dxs[mid])
        dy = dys[mid] if len(dys) % 2 else .5 * (dys[mid - 1] + dys[mid])

        shifts_px = [(item["dx"] * sw, item["dy"] * sh) for item in matches]
        if len(shifts_px) >= 2:
            spread = max(hypot(a[0] - b[0], a[1] - b[1])
                         for i, a in enumerate(shifts_px) for b in shifts_px[i + 1:])
        else:
            spread = 0.0
        if len(matches) >= 2 and spread > agree_px:
            unresolved_indexes.append(index)
            rows.append(_unresolved_row(frame, index, region_readings, "ambiguous",
                                        score=max(item["score"] for item in matches)))
            continue

        best_score = max(item["score"] for item in matches)
        best_margin = max(item["margin"] for item in matches)
        confidence = _confidence(best_score, best_margin, spread, agree_px)

        measured_x_mm = dx * sw * f_mm_per_px
        measured_y_mm = dy * sh * f_mm_per_px
        shift_x = -measured_x_mm
        shift_y = -measured_y_mm

        limited = False
        reason = None
        if abs(shift_x) > max_shift_mm or abs(shift_y) > max_shift_mm:
            shift_x = max(-max_shift_mm, min(max_shift_mm, shift_x))
            shift_y = max(-max_shift_mm, min(max_shift_mm, shift_y))
            limited = True
            reason = "max_shift"

        alignment_x, alignment_y = round(shift_x, 3), round(shift_y, 3)
        safe_x, safe_y, edge_limited, coverage = _edge_guard(
            image, frame, track, cell, settings, shift_x, shift_y, cut_tolerance,
        )
        if edge_limited and reason is None:
            reason = "edge_protected"
        status = "edge-limited" if edge_limited else ("limited" if limited else "aligned")

        rows.append({
            "frame": frame["id"], "index": index, "title": frame["title"],
            "status": status, "reliable": True, "apply": True,
            "shift_x_mm": round(safe_x, 3), "shift_y_mm": round(safe_y, 3),
            "alignment_shift_x_mm": alignment_x, "alignment_shift_y_mm": alignment_y,
            "measured_x_mm": round(measured_x_mm, 3), "measured_y_mm": round(measured_y_mm, 3),
            "dx": round(dx, 6), "dy": round(dy, 6),
            "shift_x_px": round(-dx * sw, 2), "shift_y_px": round(-dy * sh, 2),
            "confidence": confidence, "score": round(best_score, 4), "reason": reason,
            "edge_limited": edge_limited, "regions": region_readings,
            "coverage": coverage,
        })

    reliable = [row["index"] for row in rows if row["status"] in RELIABLE_STATUSES]
    for row in rows:
        row["frame_id"] = row["frame"]
        row["shift_x"] = row["shift_x_mm"]
        row["shift_y"] = row["shift_y_mm"]
        row["suggested_shift_x_mm"] = row["shift_x_mm"]
        row["suggested_shift_y_mm"] = row["shift_y_mm"]
    plan = {
        "reference_frame": ref_frame["id"],
        "reference_index": reference_index,
        "space": space,
        "regions": regions,
        "max_shift_mm": max_shift_mm,
        "cut_tolerance": cut_tolerance,
        "frames": rows,
        "summary": {
            "total": count,
            "reliable": len(reliable),
            "aligned": sum(row["status"] == "aligned" for row in rows),
            "limited": sum(row["status"] == "limited" for row in rows),
            "edge_limited": sum(row["status"] == "edge-limited" for row in rows),
            "reference": 1,
            "unresolved": len(unresolved_indexes),
            "unresolved_frames": unresolved_indexes,
        },
    }
    return plan


def _unresolved_row(frame, index, readings, reason, score=None):
    return {
        "frame": frame["id"], "index": index, "title": frame["title"],
        "status": "unresolved", "reliable": False, "apply": False,
        "shift_x_mm": 0.0, "shift_y_mm": 0.0,
        "alignment_shift_x_mm": 0.0, "alignment_shift_y_mm": 0.0,
        "measured_x_mm": None, "measured_y_mm": None,
        "dx": None, "dy": None, "shift_x_px": None, "shift_y_px": None,
        "confidence": 0.0, "score": None if score is None else round(score, 4),
        "reason": reason, "edge_limited": False, "regions": readings,
        "coverage": None,
    }


def _edge_guard(image, frame, track, cell, settings, shift_x, shift_y, tolerance):
    """Soften the correction only when it would cut too much real edge ink."""
    framed = {**frame, "_action_margin_mm": settings["action_margin"]}

    def evaluate(x, y):
        return _coverage_for_shift(image, framed, track, cell, x, y)

    def loss(stats):
        # Pigment-aware loss stays meaningful for opaque camera originals.
        return stats.get("ink_cut_fraction", stats["cut_fraction"])

    full_stats = evaluate(shift_x, shift_y)
    if loss(full_stats) <= tolerance:
        return shift_x, shift_y, False, full_stats

    stats_zero = evaluate(0, 0)
    for fraction in (.85, .7, .5, .35, .2, .1):
        stats = evaluate(shift_x * fraction, shift_y * fraction)
        if loss(stats) <= tolerance:
            return shift_x * fraction, shift_y * fraction, True, stats
    if loss(stats_zero) <= tolerance:
        return 0.0, 0.0, True, stats_zero
    # The frame was already clipping before calibration. Do not add motion.
    stats_zero["preexisting_cut"] = True
    return 0.0, 0.0, True, stats_zero
