"""Reusable paper stencils for one ribbon cell.

The module turns the ink of a placed frame into cut openings, plans minimum
width paper ties ("bridges"/" ribs") that join every enclosed paper island to
the outside sheet, checks an already cut stencil against a changed placement,
and renders millimetre-accurate cut and brushing proofs.

All public geometry is reported in millimetres relative to the selected cell
(top-left origin).  Pixel entry points take an explicit pixels-per-mm value so
the planner can be tested on small synthetic masks.
"""

from math import ceil, hypot, pi

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from image_lab.validation import Invalid

from . import raster

STRUCT4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)

# Proof colours (RGB).
CUT_DARK = (38, 46, 43)
PAPER_WHITE = (255, 255, 255)
BRIDGE_TEAL = (21, 110, 90)
PROTECT_BLUE = (53, 97, 153)
BLOCK_ORANGE = (218, 92, 48)
NEVER_INK = (214, 226, 231)
PAINT_DEFAULT = (178, 49, 38)

MIN_BRIDGE_MM = 0.5
MAX_BRIDGE_MM = 12.0
DEFAULT_BRIDGE_MM = 2.0
DEFAULT_MARGIN_MM = 10.0
FIT_TOLERANCE_MM = 0.35
SCALE_TOLERANCE = 0.012


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def bridge_width(value=DEFAULT_BRIDGE_MM):
    try:
        width = float(value)
    except (TypeError, ValueError) as error:
        raise Invalid("Enter the minimum tie width in millimetres.") from error
    if not MIN_BRIDGE_MM <= width <= MAX_BRIDGE_MM:
        raise Invalid(f"The minimum tie width must be between {MIN_BRIDGE_MM:g} and {MAX_BRIDGE_MM:g} mm.")
    return width


def _first(data, *names, default=None):
    for name in names:
        if isinstance(data, dict) and name in data and data[name] is not None:
            return data[name]
    return default


def parse_marks(value):
    """Accept click points, [x, y] pairs or polylines in cell millimetres."""
    marks = []
    if not value:
        return marks
    if isinstance(value, dict):
        value = value.get("points") or value.get("marks") or []
    if not isinstance(value, (list, tuple)):
        raise Invalid("Protected line marks must be a list of points.")
    for item in value:
        if isinstance(item, dict):
            if "points" in item:
                marks.append([(_mm(p[0]), _mm(p[1])) for p in item["points"]])
            else:
                marks.append((_mm(_first(item, "x_mm", "x", "u"), "x"),
                              _mm(_first(item, "y_mm", "y", "v"), "y")))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            if isinstance(item[0], (list, tuple)):
                marks.append([(_mm(p[0]), _mm(p[1])) for p in item])
            else:
                marks.append((_mm(item[0]), _mm(item[1])))
    return marks


def _mm(value, label="coordinate"):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise Invalid(f"Enter a millimetre {label}.") from error
    if not np.isfinite(number) or abs(number) > 2000:
        raise Invalid(f"The {label} mark is outside the sheet.")
    return number


def mark_points(marks):
    """Flatten point/polyline marks to sample points."""
    points = []
    for mark in marks:
        if mark and isinstance(mark[0], (tuple, list)):
            points.extend(mark)
        else:
            points.append(mark)
    return points


# ---------------------------------------------------------------------------
# Ink classification and physical placement
# ---------------------------------------------------------------------------

def canvas_for_image(image, transform, width_px, height_px, cell_width_mm, cell_height_mm):
    """Place source art exactly as the ribbon does, on transparent stock."""
    canvas, _placed, offset = raster.place(
        image, transform, (width_px, height_px), (cell_width_mm, cell_height_mm), None,
    )
    return canvas, offset


def ink_mask(canvas, threshold=0.5, mode="auto"):
    """Binary ink mask for a placed RGBA cell canvas.

    Auto uses alpha when the artwork itself is transparent; otherwise dark or
    chromatic pixels on opaque scans/photos are treated as ink.  A high alpha
    coverage guard prevents an opaque placed rectangle being read as solid ink.
    """
    rgba = np.asarray(canvas.convert("RGBA"))
    alpha = rgba[:, :, 3].astype(np.float32) / 255
    use_alpha = mode == "alpha" or (mode == "auto" and (alpha < 0.98).mean() > 0.02)
    if use_alpha:
        cut = alpha >= float(threshold)
        if mode == "alpha" or cut.mean() <= 0.82:
            return cut
    rgb = rgba[:, :, :3].astype(np.float32) / 255
    # Opaque artwork: treat dark or strongly coloured pixels as ink.
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    return (luminance < 0.80) | ((chroma > 0.22) & (luminance < 0.92))


def resolve_ink_mode(source, mode="auto"):
    """Decide alpha vs luminance from the *source* pixels, before placement."""
    if mode in {"alpha", "dark"}:
        return mode
    if source is not None:
        source_alpha = np.asarray(source.convert("RGBA"))[:, :, 3]
        if (source_alpha < 250).mean() > 0.02:
            return "alpha"
    return "dark"


def placement_signature(transform, placed_size, offset, cell_width_mm, cell_height_mm, dpi,
                        cell_start_mm=0.0, circumference=None):
    ppm = dpi / 25.4
    ox, oy = offset
    pw, ph = placed_size
    return {
        "transform": dict(transform),
        "scale": float(transform["scale"]),
        "flip": bool(transform["flip"]),
        "shift_x": float(transform["shift_x"]),
        "shift_y": float(transform["shift_y"]),
        "placed_x_mm": ox / ppm,
        "placed_y_mm": oy / ppm,
        "placed_w_mm": pw / ppm,
        "placed_h_mm": ph / ppm,
        "cell_width_mm": float(cell_width_mm),
        "cell_height_mm": float(cell_height_mm),
        "cell_start_mm": float(cell_start_mm),
        "circumference_mm": circumference,
        "dpi": int(dpi),
        "pixels_per_mm": ppm,
    }


def compare_placements(cut, current):
    """Decide how a physically cut stencil can meet a changed placement."""
    tol = FIT_TOLERANCE_MM
    dx = current["placed_x_mm"] - cut["placed_x_mm"]
    dy = current["placed_y_mm"] - cut["placed_y_mm"]
    ratio_w = current["placed_w_mm"] / max(cut["placed_w_mm"], 1e-9)
    ratio_h = current["placed_h_mm"] / max(cut["placed_h_mm"], 1e-9)
    scale_changed = abs(ratio_w - 1) > SCALE_TOLERANCE or abs(ratio_h - 1) > SCALE_TOLERANCE
    mirror_changed = bool(cut["flip"]) != bool(current["flip"])
    cell_resized = (abs(current["cell_width_mm"] - cut["cell_width_mm"]) > tol or
                    abs(current["cell_height_mm"] - cut["cell_height_mm"]) > tol)
    shift_changed = hypot(dx, dy) > tol
    reasons = []
    if scale_changed:
        reasons.append("scale")
    if cell_resized:
        reasons.append("cell-size")
    if mirror_changed:
        reasons.append("mirror")
    if shift_changed:
        reasons.append("offset")

    result = {
        "matches": False, "compatible": False, "fits": False,
        "usable": False, "can_reuse": False, "reusable": False,
        "method": "no", "verdict": "incompatible", "reasons": reasons,
        "scale_changed": scale_changed, "mirror_changed": mirror_changed,
        "cell_resized": cell_resized, "shift_changed": shift_changed,
        "delta_x_mm": round(dx, 3), "delta_y_mm": round(dy, 3),
        "offset_x_mm": round(dx, 3), "offset_y_mm": round(dy, 3),
        "offset_mm": round(hypot(dx, dy), 3),
        "scale_ratio_x": round(ratio_w, 4), "scale_ratio_y": round(ratio_h, 4),
        "scale_factor": round((ratio_w + ratio_h) / 2, 4),
        "mirrored": mirror_changed, "scaled": scale_changed,
        "tolerance_mm": tol,
    }

    if scale_changed or cell_resized:
        result["method"] = "scale-mismatch"
        result["verdict"] = "resized" if scale_changed else "cell-resized"
        result["message"] = (
            "The cut stencil cannot stretch to the new size; cut a new one."
            if scale_changed else
            "The ribbon cell size changed; the already cut sheet no longer registers.")
        return result

    if not mirror_changed:
        if not shift_changed:
            result.update(matches=True, compatible=True, fits=True, usable=True,
                          can_reuse=True, reusable=True, method="registered", verdict="fits")
            result["message"] = "The cut stencil registers exactly at the current placement."
        else:
            result.update(usable=True, can_reuse=True, reusable=True,
                          method="translate", verdict="translate")
            result["message"] = (
                f"Shift the dry stencil {dx:+.1f} mm horizontally and {dy:+.1f} mm vertically "
                "from the cell marks to follow the moved artwork.")
        return result

    # Mirror changed: a paper stencil produces the reflection when turned over.
    cell_w = cut["cell_width_mm"]
    reflected_cut_x = cell_w - (cut["placed_x_mm"] + cut["placed_w_mm"])
    flip_dx = current["placed_x_mm"] - reflected_cut_x
    flip_dy = dy
    flip_shift = hypot(flip_dx, flip_dy) > tol
    result["flip_delta_x_mm"] = round(flip_dx, 3)
    result["flip_delta_y_mm"] = round(flip_dy, 3)
    result.update(usable=True, can_reuse=True, reusable=True,
                  method="flip-over-translate" if flip_shift else "flip-over",
                  verdict="mirror-reversible")
    if flip_shift:
        result["message"] = (
            "Turn the cut stencil over for the mirror, then shift it "
            f"{flip_dx:+.1f} mm horizontally and {flip_dy:+.1f} mm.")
    else:
        result["message"] = (
            "Turn the already cut stencil face-down: its mirror matches the flipped frame. "
            "Align the corner marks from the back before brushing.")
    return result


def compatibility(stencil, frame, track, cell):
    """Compare an adopted stencil record with the frame's current transform."""
    ppm = track.pixels_per_mm
    width_px, height_px = track.x(cell.width), track.x(track.height)
    # Recompute placed size with the same fit rule as raster.place.
    source_w = stencil.get("source_width") or 300
    source_h = stencil.get("source_height") or 350
    scale = min(width_px / source_w, height_px / source_h) * frame["transform"]["scale"]
    placed = (max(1, round(source_w * scale)), max(1, round(source_h * scale)))
    ox = round((width_px - placed[0]) / 2 + frame["transform"]["shift_x"] * width_px / cell.width)
    oy = round((height_px - placed[1]) / 2 + frame["transform"]["shift_y"] * height_px / track.height)
    current = placement_signature(
        frame["transform"], placed, (ox, oy), cell.width, track.height, track.dpi,
        cell.start, track.circumference)
    cut = stencil["placement"]
    report = compare_placements(cut, current)
    same_frame = stencil.get("frame_id") == frame["id"]
    report["same_frame"] = same_frame
    if not same_frame:
        report.update(matches=False, compatible=False, fits=False, usable=False,
                      can_reuse=False, reusable=False, method="wrong-frame",
                      verdict="wrong-frame", reasons=["frame"])
        report["message"] = "That stencil was cut for a different frame."
    return report


# ---------------------------------------------------------------------------
# Pixel-level connector planner
# ---------------------------------------------------------------------------

def _raster_poly(points, width, shape, offset=(0, 0)):
    """Rasterise a constant-width (possibly bent) tie with round joints."""
    y0, x0 = offset
    height, width_total = shape
    mask = Image.new("L", (width_total, height), 0)
    draw = ImageDraw.Draw(mask)
    coords = [(x - x0, y - y0) for y, x in points]
    if len(coords) >= 2:
        draw.line(coords, fill=1, width=int(width), joint="curve")
    radius = width / 2 - 0.5
    for x, y in coords:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=1)
    return np.asarray(mask, dtype=bool)


def _patch_poly(points, width, ink, protect):
    height, width_total = ink.shape
    margin = int(width) + 2
    ys = [p[0] for p in points]
    xs = [p[1] for p in points]
    y0 = max(0, int(min(ys)) - margin)
    y1 = min(height, int(max(ys)) + margin + 1)
    x0 = max(0, int(min(xs)) - margin)
    x1 = min(width_total, int(max(xs)) + margin + 1)
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(mask)
    coords = [(x - x0, y - y0) for y, x in points]
    draw.line(coords, fill=1, width=int(width), joint="curve")
    radius = width / 2 - 0.5
    for x, y in coords:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=1)
    corridor = np.asarray(mask, dtype=bool)
    blocked = int(np.count_nonzero(corridor & ink[y0:y1, x0:x1]))
    protected_hits = int(np.count_nonzero(
        corridor & protect[y0:y1, x0:x1] & ink[y0:y1, x0:x1])) if protect is not None else 0
    return corridor, (y0, x0, y1, x1), blocked, protected_hits


def _connector_options(island, connected, indices, bridge_px, max_options=26):
    """Diverse anchored candidate polylines from one island to connected paper."""
    dist, nearest = indices
    ys, xs = np.where(island)
    if not len(ys):
        return []
    distances = dist[ys, xs]
    order = np.argsort(distances)
    cy, cx = ys.mean(), xs.mean()
    picked = [int(order[0])]
    bins = {}
    nearest_gap = distances[order[0]]
    for position in order[1:]:
        y, x = ys[position], xs[position]
        angle = int((np.arctan2(y - cy, x - cx) + pi) / (2 * pi) * 36) % 36
        if angle not in bins and distances[position] < nearest_gap * 4 + bridge_px * 3:
            bins[angle] = int(position)
    picked.extend(bins.values())

    extension = bridge_px * 0.6 + 2
    options = []
    seen = set()
    for position in picked[:max_options]:
        y, x = float(ys[position]), float(xs[position])
        qy = float(nearest[0, int(y), int(x)])
        qx = float(nearest[1, int(y), int(x)])
        dy, dx = qy - y, qx - x
        length = hypot(dx, dy)
        if length < 1:
            uy, ux = 1.0, 0.0
        else:
            uy, ux = dy / length, dx / length
        start = (y - uy * extension, x - ux * extension)
        end = (qy + uy * extension, qx + ux * extension)
        bucket = (int(y) // 8, int(x) // 8)
        if bucket in seen:
            continue
        seen.add(bucket)
        options.append([start, end])
        options.append([start, (start[0], end[1]), end])
        options.append([start, (end[0], start[1]), end])
    return options


def connect_components(ink, bridge_px, protect=None, min_island_px=0):
    """Connect every enclosed paper component to the border paper.

    Returns a deterministic plan with pixel coordinates and a full-width
    ``bridge_mask``.  Border-touching paper labels are all treated as outside
    (a real sheet margin makes them one piece).
    """
    ink = np.asarray(ink, dtype=bool)
    if ink.ndim != 2 or min(ink.shape) < 4:
        raise Invalid("The stencil artwork is too small to plan ties on.")
    height, width = ink.shape
    if protect is None:
        protect = np.zeros_like(ink)
    paper = ~ink
    labels, count = ndimage.label(paper, structure=STRUCT4)
    border_values = np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1],
    ])
    outside_ids = {int(v) for v in np.unique(border_values) if v}
    if not outside_ids:
        raise Invalid("The artwork reaches every sheet edge; add a paper margin before cutting ties.")

    sizes = ndimage.sum(np.ones_like(labels), labels, index=range(1, count + 1))
    connected = np.isin(labels, list(outside_ids))
    pending = {
        i for i in range(1, count + 1)
        if i not in outside_ids and sizes[i - 1] >= min_island_px
    }
    dropped = [
        {"label": i, "pixels": int(sizes[i - 1])}
        for i in range(1, count + 1)
        if i not in outside_ids and sizes[i - 1] < min_island_px
    ]

    bridges = []
    bridge_mask = np.zeros_like(ink)
    while pending:
        # One distance field per round: connected only grows during the round.
        distance, nearest = ndimage.distance_transform_edt(
            ~connected, return_indices=True)
        indices = (distance, nearest)
        best = None
        for island_id in list(pending):
            island = labels == island_id
            island_size = int(np.count_nonzero(island))
            for poly in _connector_options(island, connected, indices, bridge_px):
                corridor, box, blocked, protected_hits = _patch_poly(poly, bridge_px, ink, protect)
                y0, x0, y1, x1 = box
                anchor_island = int(np.count_nonzero(corridor & island[y0:y1, x0:x1]))
                anchor_outside = int(np.count_nonzero(corridor & connected[y0:y1, x0:x1]))
                # A tie wider than a tiny island simply swallows it.
                island_need = min(max(2, bridge_px // 3), island_size)
                outside_need = max(2, bridge_px // 3)
                if anchor_island < island_need or anchor_outside < outside_need:
                    continue
                length = sum(hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(poly, poly[1:]))
                # First avoid protected thin lines entirely, then waste the
                # least ink, then prefer the shortest tie.
                key = (1 if protected_hits else 0, protected_hits, blocked, length)
                if best is None or key < best[0]:
                    best = (key, island_id, poly, blocked, protected_hits, length)
        if best is None:
            raise Invalid("Could not route a cuttable tie to every paper island; "
                          "increase the cell size or reduce the minimum tie width.")
        _key, island_id, poly, blocked, protected_hits, length = best
        corridor_full = _raster_poly(poly, bridge_px, ink.shape)
        bridge_mask |= corridor_full
        connected |= corridor_full
        touched = {int(v) for v in np.unique(labels[corridor_full]) if v}
        for touched_id in touched:
            connected |= labels == touched_id
            pending.discard(touched_id)
        pending.discard(island_id)
        bridges.append({
            "points_px": [(float(y), float(x)) for y, x in poly],
            "width_px": int(bridge_px),
            "length_px": float(length),
            "blocked_ink_px2": int(blocked),
            "protected_hits_px": int(protected_hits),
            "connects": int(island_id),
        })

    final_paper = ~ink | bridge_mask
    final_labels, final_count = ndimage.label(final_paper, structure=STRUCT4)
    edge_values = np.concatenate([
        final_labels[0, :], final_labels[-1, :], final_labels[:, 0], final_labels[:, -1],
    ])
    edge_ids = {int(v) for v in np.unique(edge_values) if v}
    final_sizes = ndimage.sum(np.ones_like(final_labels), final_labels,
                              index=range(1, final_count + 1))
    interior = [
        i + 1 for i, size in enumerate(final_sizes)
        if i + 1 not in edge_ids and size > max(4, bridge_px)
    ]
    if interior:
        raise Invalid("The tie layout failed its connectivity check.")
    return {
        "bridges_px": bridges,
        "bridge_mask": bridge_mask,
        "outside_ids": outside_ids,
        "component_count": count,
        "island_count": count - len(outside_ids),
        "dropped": dropped,
        "all_connected": not interior,
        "labels": labels,
    }


def plan_bridges(ink, bridge_mm=DEFAULT_BRIDGE_MM, ppm=None, pixels_per_mm=None,
                 protect=None, marks=None, protect_radius_mm=None, min_island_mm2=0.0,
                 **_ignored):
    """Millimetre wrapper around :func:`connect_components`.

    ``protect``/``marks`` are millimetre points or polylines in mask
    coordinates.  Returns JSON-friendly bridge geometry plus masks.
    """
    ppm = float(ppm or pixels_per_mm or 10)
    width_mm = bridge_width(bridge_mm)
    bridge_px = max(3, int(ceil(width_mm * ppm)))
    ink = np.asarray(ink, dtype=bool)
    height, width = ink.shape

    mark_list = parse_marks(protect if protect is not None else marks)
    if protect_radius_mm is None:
        protect_radius_mm = max(0.6, width_mm * 0.62)
    protect_mask = np.zeros_like(ink)
    points = mark_points(mark_list)
    if points:
        yy, xx = np.ogrid[:height, :width]
        radius_px = protect_radius_mm * ppm
        for x_mm, y_mm in points:
            cx, cy = x_mm * ppm, y_mm * ppm
            protect_mask |= (xx - cx) ** 2 + (yy - cy) ** 2 <= radius_px ** 2

    min_island_px = max(0, int(round(min_island_mm2 * ppm * ppm)))
    result = connect_components(ink, bridge_px, protect_mask, min_island_px)

    # Island centroids/areas in millimetres.
    islands = []
    for island_id in range(1, result["component_count"] + 1):
        if island_id in result["outside_ids"]:
            continue
        ys, xs = np.where(result["labels"] == island_id)
        islands.append({
            "index": len(islands),
            "label": island_id,
            "x_mm": round(float(xs.mean()) / ppm, 3),
            "y_mm": round(float(ys.mean()) / ppm, 3),
            "area_mm2": round(len(xs) / ppm ** 2, 3),
        })

    bridges = []
    for index, bridge in enumerate(result["bridges_px"]):
        pts = [(x / ppm, y / ppm) for y, x in bridge["points_px"]]
        x1, y1 = pts[0]
        x2, y2 = pts[-1]
        angle = degrees_angle(x1, y1, x2, y2)
        bridges.append({
            "index": index,
            "points_mm": [[round(x, 3), round(y, 3)] for x, y in pts],
            "points": [[round(x, 3), round(y, 3)] for x, y in pts],
            "x1_mm": round(x1, 3), "y1_mm": round(y1, 3),
            "x2_mm": round(x2, 3), "y2_mm": round(y2, 3),
            "x_mm": round((x1 + x2) / 2, 3), "y_mm": round((y1 + y2) / 2, 3),
            "x": round((x1 + x2) / 2, 3), "y": round((y1 + y2) / 2, 3),
            "width_mm": round(bridge_px / ppm, 3),
            "width": round(bridge_px / ppm, 3),
            "requested_width_mm": round(width_mm, 3),
            "min_width_mm": round(width_mm, 3),
            "length_mm": round(bridge["length_px"] / ppm, 3),
            "length": round(bridge["length_px"] / ppm, 3),
            "angle_deg": round(angle, 1),
            "angle": round(angle, 1),
            "blocked_ink_mm2": round(bridge["blocked_ink_px2"] / ppm ** 2, 4),
            "protected_hits": bridge["protected_hits_px"],
        })

    ink_area = float(np.count_nonzero(ink)) / ppm ** 2
    blocked = sum(b["blocked_ink_mm2"] for b in bridges)
    protected_lines = _describe_marks(ink, ppm, mark_list, protect_mask,
                                      result["bridge_mask"], protect_radius_mm)
    return {
        "bridge_mm": width_mm,
        "min_bridge_mm": width_mm,
        "bridge_width_mm": width_mm,
        "actual_width_mm": round(bridge_px / ppm, 3),
        "pixels_per_mm": ppm,
        "bridge_px": bridge_px,
        "bridges": bridges,
        "ties": bridges,  # domain-language alias
        "islands": islands,
        "island_count": len(islands),
        "n_islands": len(islands),
        "bridge_count": len(bridges),
        "tie_count": len(bridges),
        "all_connected": result["all_connected"],
        "connected": result["all_connected"],
        "ink_area_mm2": round(ink_area, 3),
        "blocked_ink_mm2": round(blocked, 4),
        "blocked_fraction": round(min(1.0, blocked / max(ink_area, 1e-9)), 4),
        "protected_lines": protected_lines,
        "protected": protected_lines,
        "marks": [
            [round(x, 3), round(y, 3)] for x, y in points
        ],
        # Masks are numpy arrays for callers/renderers, never JSON serialized.
        "ink_mask": ink,
        "bridge_mask": result["bridge_mask"],
        "protect_mask": protect_mask,
    }


def degrees_angle(x1, y1, x2, y2):
    angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
    return float(((angle + 90) % 180) - 90)


def _describe_marks(ink, ppm, marks, protect_mask, bridge_mask, protect_radius_mm=None):
    distance = ndimage.distance_transform_edt(ink)
    height, width = ink.shape
    yy, xx = np.ogrid[:height, :width]
    if protect_radius_mm is None:
        protect_radius_mm = max(0.6, 2.0 * 0.62)
    radius_px = protect_radius_mm * ppm
    rows = []
    for mark in marks:
        points = mark if (mark and isinstance(mark[0], (tuple, list))) else [mark]
        for x_mm, y_mm in points[:1]:  # one reading per mark/polyline anchor
            px, py = x_mm * ppm, y_mm * ppm
            radius = max(2, int(1.5 * ppm))
            x0 = int(np.clip(px - radius, 0, width - 1))
            x1 = int(np.clip(px + radius + 1, 1, width))
            y0 = int(np.clip(py - radius, 0, height - 1))
            y1 = int(np.clip(py + radius + 1, 1, height))
            patch_ink = ink[y0:y1, x0:x1]
            if patch_ink.any():
                local = distance[y0:y1, x0:x1]
                thickness = 2 * float(local[patch_ink].max()) / ppm
                snapped = True
            else:
                thickness = None
                snapped = False
            if bridge_mask is not None:
                zone = (xx - px) ** 2 + (yy - py) ** 2 <= radius_px ** 2
                # Only tie paper covering protected ink blocks the line;
                # anchor caps resting on enclosed paper do not count.
                clash = bool(np.any(bridge_mask & zone & ink))
            else:
                clash = False
            rows.append({
                "x_mm": round(x_mm, 3), "y_mm": round(y_mm, 3),
                "ink_width_mm": round(thickness, 3) if thickness else None,
                "thin_line_mm": round(thickness, 3) if thickness else None,
                "found_ink": snapped,
                "bridge_clash": clash,
                "kept": not clash,
            })
    return rows


# ---------------------------------------------------------------------------
# Draft-level planning and frozen records
# ---------------------------------------------------------------------------

def resolve_frame(draft, frame=None, frame_id=None):
    frames = draft["frames"]
    if not frames:
        raise Invalid("Add at least one frame before cutting a paper stencil.")
    if frame_id:
        for index, item in enumerate(frames):
            if item["id"] == frame_id:
                return index, item
        raise Invalid("The selected stencil frame is unavailable.")
    if frame is None:
        frame = draft["settings"].get("proof_frame", 0)
    index = int(frame)
    if index < 0 or index >= len(frames):
        index = max(0, min(int(draft["settings"].get("proof_frame", 0)), len(frames) - 1))
    return index, frames[index]


def plan_for_draft(draft, track, library, frame=None, frame_id=None,
                   bridge_mm=DEFAULT_BRIDGE_MM, marks=None, protect=None,
                   protect_radius_mm=None, ink_threshold=0.5, ink_mode="auto",
                   margin_mm=DEFAULT_MARGIN_MM, source=None):
    """Render the active cell and plan ties in its physical millimetres."""
    index, frame_record = resolve_frame(draft, frame, frame_id)
    cell = track.cells[index]
    width_px, height_px = track.x(cell.width), track.x(track.height)
    if source is None:
        source = raster.decode(library.blob(frame_record["blob"]).read_bytes())
    canvas, offset = canvas_for_image(
        source, frame_record["transform"], width_px, height_px, cell.width, track.height)
    resolved_mode = resolve_ink_mode(source, ink_mode)
    ink = ink_mask(canvas, ink_threshold, resolved_mode)
    plan = plan_bridges(
        ink, bridge_mm=bridge_mm, ppm=track.pixels_per_mm,
        protect=marks if marks is not None else protect,
        protect_radius_mm=protect_radius_mm)
    plan["ink_mode"] = resolved_mode
    signature = placement_signature(
        frame_record["transform"], _placed_size(frame_record, source, track, cell),
        offset, cell.width, track.height, track.dpi, cell.start, track.circumference)
    plan.update({
        "frame": index,
        "frame_id": frame_record["id"],
        "frame_title": frame_record["title"],
        "cell_width_mm": cell.width,
        "cell_height_mm": track.height,
        "cell_start_mm": cell.start,
        "cell_end_mm": cell.end,
        "cell_center_mm": cell.center,
        "circumference_mm": track.circumference,
        "dpi": track.dpi,
        "margin_mm": margin_mm,
        "placement": signature,
        "source_width": source.width,
        "source_height": source.height,
        "ink_threshold": ink_threshold,
        "ink_mode": resolved_mode,
    })
    return plan, canvas, ink


def _placed_size(frame_record, source, track, cell):
    width_px, height_px = track.x(cell.width), track.x(track.height)
    art = raster.matte(source, frame_record["transform"]["matte"])
    scale = min(width_px / art.width, height_px / art.height) * frame_record["transform"]["scale"]
    return (max(1, round(art.width * scale)), max(1, round(art.height * scale)))


def adoption_record(plan, revision):
    """JSON-safe persisted stencil edition."""
    return {
        "frame": plan["frame"],
        "frame_id": plan["frame_id"],
        "frame_title": plan["frame_title"],
        "adopted_revision": revision,
        "bridge_mm": plan["bridge_mm"],
        "marks": plan["marks"],
        "protect_radius_mm": None,
        "ink_threshold": plan["ink_threshold"],
        "ink_mode": plan["ink_mode"],
        "margin_mm": plan["margin_mm"],
        "cell_width_mm": plan["cell_width_mm"],
        "cell_height_mm": plan["cell_height_mm"],
        "cell_start_mm": plan["cell_start_mm"],
        "cell_end_mm": plan["cell_end_mm"],
        "cell_center_mm": plan["cell_center_mm"],
        "circumference_mm": plan["circumference_mm"],
        "dpi": plan["dpi"],
        "source_width": plan["source_width"],
        "source_height": plan["source_height"],
        "placement": plan["placement"],
        "bridges": [{k: v for k, v in bridge.items()} for bridge in plan["bridges"]],
        "islands": plan["islands"],
        "protected_lines": plan.get("protected_lines", []),
        "island_count": plan["island_count"],
        "bridge_count": len(plan["bridges"]),
        "tie_count": len(plan["bridges"]),
        "actual_width_mm": plan.get("actual_width_mm", plan["bridge_mm"]),
        "blocked_fraction": plan.get("blocked_fraction"),
        "blocked_ink_mm2": plan["blocked_ink_mm2"],
        "ink_area_mm2": plan["ink_area_mm2"],
        "all_connected": plan["all_connected"],
    }


def bridge_mask_from_record(record, ppm, shape):
    """Rasterise stored millimetre bridge polylines at any print resolution."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(mask)
    for bridge in record.get("bridges", []):
        points = bridge.get("points_mm") or [
            [bridge["x1_mm"], bridge["y1_mm"]], [bridge["x2_mm"], bridge["y2_mm"]]]
        coords = [(round(x * ppm), round(y * ppm)) for x, y in points]
        width_mm = bridge.get("width_mm", record["bridge_mm"])
        width_px = max(3, int(round(width_mm * ppm)))
        if len(coords) >= 2:
            draw.line(coords, fill=255, width=width_px, joint="curve")
        radius = width_px / 2 - 0.5
        for x, y in coords:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    return np.asarray(mask, dtype=bool)


def adopted_masks(draft, library, record, ppm=None, cell_w_mm=None, cell_h_mm=None):
    """Reconstruct ink and tie masks from a frozen adopted edition.

    Pass ``ppm``/``cell_*`` to rasterise at the current track resolution while
    keeping the physical bridge widths; otherwise the adoption DPI is used.
    """
    frame_record = next((f for f in draft["frames"] if f["id"] == record.get("frame_id")), None)
    if frame_record is None:
        index = max(0, min(int(record.get("frame", 0)), len(draft["frames"]) - 1))
        frame_record = draft["frames"][index]
    source = raster.decode(library.blob(frame_record["blob"]).read_bytes())
    frozen = record["placement"]
    out_ppm = float(ppm or record["dpi"] / 25.4)
    cell_w = float(cell_w_mm if cell_w_mm is not None else record["cell_width_mm"])
    cell_h = float(cell_h_mm if cell_h_mm is not None else record["cell_height_mm"])
    width_px, height_px = round(cell_w * out_ppm), round(cell_h * out_ppm)
    transform = frozen.get("transform") or frame_record["transform"]
    canvas, _placed, _offset = raster.place(
        source, transform, (width_px, height_px), (cell_w, cell_h), None)
    resolved = resolve_ink_mode(source, record.get("ink_mode", "auto"))
    ink = ink_mask(canvas, record.get("ink_threshold", 0.5), resolved)
    bridge_mask = bridge_mask_from_record(record, out_ppm, ink.shape)
    return {
        "frame": frame_record, "source": source, "canvas": canvas,
        "ink": ink, "bridge_mask": bridge_mask, "ppm": out_ppm,
        "cell_width_mm": cell_w, "cell_height_mm": cell_h,
    }


# Descriptive aliases for callers and tests using domain language.
plan_stencil = plan_bridges
build_bridges = plan_bridges
plan_ties = plan_bridges
check_fit = compare_placements
check_compatibility = compare_placements
stencil_compatibility = compatibility
