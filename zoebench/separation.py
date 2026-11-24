"""Loop-wide ink separation for the old single-ink printer.

The bench models a printer that can hold exactly two stable gray ink tones on
top of the paper stock.  A handful of recurring "primary" colours are sampled
from every frame.  Each primary is assigned to one of the two tones once, for
the whole loop, so the same source colour prints with the same ink level in
every cell.  Transparent pixels stay on the paper and every rendered ribbon
pixel is one of the printable colours -- no intermediate anti-aliased grays
are left in the output.

The assignment is a tiny combinatorial optimisation: it keeps luminance order
as a weak prior while maximising the number of observed cut-paper edges, thin
action lines and user-marked adjacent colour pairs that end up on different
print tones.  Mark verification afterwards reads the quantised device-pixel
ribbon rather than the assignment table.
"""

from itertools import product
from uuid import uuid4

import numpy as np
from PIL import Image

from image_lab.validation import Invalid

from . import layout, raster

# Output gray bytes used until the user fills in measured levels.
DEFAULT_TONES = (186, 82)
MAX_SWATCHES = 14
BASE_SWATCHES = 10
MERGE_DISTANCE = 50.0
MARK_DISTANCE = 46.0
MARK_WEIGHT = 10 ** 9
SCREEN_EDGE = 470
THIN_HALF_MM = .5


# ---------------------------------------------------------------------------
# Small colour / shape helpers
# ---------------------------------------------------------------------------

def hex_rgb(value):
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise Invalid("Use a six digit hex colour such as #237763.")
    try:
        return [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError as error:
        raise Invalid("Use a six digit hex colour such as #237763.") from error


def rgb_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(round(max(0, min(255, c)))) for c in rgb[:3]))


def gray_value(entry):
    """Coerce one user supplied ink level to a 0..255 gray byte."""
    if isinstance(entry, dict):
        if "rgb" in entry:
            rgb = entry["rgb"]
            if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
                raise Invalid("An ink tone RGB must have three channels.")
            return int(round(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]))
        for key in ("gray", "grey", "value", "level", "tone", "brightness"):
            if key in entry:
                return gray_value(entry[key])
        if "coverage" in entry or "ink" in entry:
            raw = entry.get("coverage", entry.get("ink"))
            coverage = float(str(raw).rstrip("%"))
            if isinstance(raw, str) and raw.endswith("%"):
                coverage /= 100
            elif coverage > 1.5:
                coverage /= 100
            return int(round(255 * (1 - min(1, max(0, coverage)))))
        raise Invalid("Each ink level needs a gray value.")
    if isinstance(entry, str):
        text = entry.strip()
        if text.startswith("#"):
            rgb = hex_rgb(text)
            return int(round(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]))
        if text.endswith("%"):
            number = float(text[:-1])
            return int(round(255 * (1 - min(100, max(0, number)) / 100)))
        entry = text
    if isinstance(entry, bool):
        raise Invalid("Ink levels must be numbers between 0 and 255.")
    value = float(entry)
    if not np.isfinite(value):
        raise Invalid("Ink levels must be numbers between 0 and 255.")
    if 0 <= value <= 1 and not float(value).is_integer():
        value *= 255
    value = int(round(value))
    if not 0 <= value <= 255:
        raise Invalid("Ink gray levels must lie between 0 and 255.")
    return value


def coerce_tones(data, current=None):
    """Accept the many field names a client might use for the two levels."""
    candidates = None
    if isinstance(data, dict):
        for key in ("tones", "gray_levels", "grey_levels", "levels", "ink_levels",
                    "inks", "grays", "greys", "tone_values", "device_tones"):
            if key in data and data[key] is not None:
                candidates = data[key]
                break
        if candidates is None:
            first = next((data[k] for k in ("gray_1", "gray1", "tone_1", "level_1",
                                             "gray_a", "tone_a", "light_gray", "gray_light")
                          if k in data), None)
            second = next((data[k] for k in ("gray_2", "gray2", "tone_2", "level_2",
                                              "gray_b", "tone_b", "dark_gray", "gray_dark")
                           if k in data), None)
            if first is not None and second is not None:
                candidates = [first, second]
    elif isinstance(data, (list, tuple)):
        candidates = data
    if candidates is None:
        return None
    if not isinstance(candidates, (list, tuple)) or len(candidates) != 2:
        raise Invalid("Provide exactly two stable gray ink levels.")
    return [{"value": gray_value(candidates[0]), "name": "Gray ink 1"},
            {"value": gray_value(candidates[1]), "name": "Gray ink 2"}]


def tone_bytes(sep):
    tones = sep.get("tones") or []
    values = [int(tone["value"]) for tone in tones[:2]]
    while len(values) < 2:
        values.append(DEFAULT_TONES[len(values)])
    return values


def coerce_assignment(sep, raw):
    """Merge an assignment keyed by swatch id, hex or RGB into id -> level."""
    if not isinstance(raw, dict):
        return {}
    tones = tone_bytes(sep)
    merged = {}
    for key, value in raw.items():
        identifier = None
        if isinstance(key, str) and any(s["id"] == key for s in sep.get("swatches", [])):
            identifier = key
        else:
            rgb = None
            try:
                if isinstance(key, str) and key.strip().startswith("#"):
                    rgb = hex_rgb(key)
                elif isinstance(key, str) and "," in key:
                    rgb = [float(part) for part in key.split(",")[:3]]
            except Invalid:
                rgb = None
            if rgb is not None and sep.get("swatches"):
                centers = [s["rgb"] for s in sep["swatches"]]
                index = nearest_index(rgb, centers)
                if index >= 0:
                    identifier = sep["swatches"][index]["id"]
        if identifier is None:
            continue
        if isinstance(value, dict):
            value = value.get("level", value.get("tone", value.get("index")))
        level = None
        if isinstance(value, str) and value.strip().startswith("#"):
            byte = gray_value(value)
            level = int(np.argmin([abs(byte - tone) for tone in tones]))
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number in (0, 1) or (isinstance(value, int) and value in (0, 1)):
                level = int(number)
            else:
                level = int(np.argmin([abs(number - tone) for tone in tones]))
        if level in (0, 1):
            merged[identifier] = level
    return merged


def luminance_of_rgb(rgb):
    arr = np.asarray(rgb, dtype=np.float32) / 255
    linear = raster.linear(arr)
    return float(linear @ np.array([.2126, .7152, .0722], dtype=np.float32))


def color_distance(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    dr = a[0] - b[0]
    dg = a[1] - b[1]
    db = a[2] - b[2]
    rmean = (a[0] + b[0]) / 2
    return float(np.sqrt((2 + rmean / 256) * dr * dr + 4 * dg * dg +
                         (2 + (255 - rmean) / 256) * db * db))


def nearest_index(rgb, centers):
    if not len(centers):
        return -1
    distances = [color_distance(rgb, center) for center in centers]
    return int(np.argmin(distances))


# ---------------------------------------------------------------------------
# Rendering the same framed cells the proofs use
# ---------------------------------------------------------------------------

def screen_track_size(track, cell):
    scale = SCREEN_EDGE / max(cell.width, track.height)
    return max(1, round(cell.width * scale)), max(1, round(track.height * scale))


def render_cell(source, draft, track, index):
    frame, cell = draft["frames"][index], track.cells[index]
    width, height = screen_track_size(track, cell)
    image = layout.paint_cell(source(frame), frame, track, cell, width, height)[0]
    return np.asarray(image.convert("RGBA")), width / cell.width


# ---------------------------------------------------------------------------
# Global primary-colour sampling and adjacency analysis
# ---------------------------------------------------------------------------

def _merge_palette(candidates, max_swatches=MAX_SWATCHES):
    """Greedy count-weighted merge of near identical exact colours."""
    swatches = []  # [rgb np.array, count]
    for rgb, count, frames in candidates:
        if swatches:
            distances = [color_distance(rgb, item[0]) for item in swatches]
            index = int(np.argmin(distances))
            if distances[index] <= MERGE_DISTANCE or len(swatches) >= max_swatches:
                center, total, frame_bits = swatches[index]
                merged = (center * total + np.asarray(rgb) * count) / (total + count)
                swatches[index] = (merged, total + count, frame_bits | frames)
                continue
        swatches.append((np.asarray(rgb, dtype=np.float64), int(count), frames))
    swatches.sort(key=lambda item: -item[1])
    result = []
    for index, (rgb, count, frames) in enumerate(swatches):
        value = [int(round(c)) for c in rgb]
        result.append({
            "id": f"s{index}", "rgb": value, "hex": rgb_hex(value),
            "count": int(count), "frames": sorted(_frame_bits(frames)),
            "luminance": round(luminance_of_rgb(value), 4),
        })
    return result


def _frame_bits(bits):
    index = 0
    while bits:
        if bits & 1:
            yield index
        bits >>= 1
        index += 1


def sample_palette(draft, track, source):
    histogram = {}
    opaque_total = 0
    frame_count = len(draft["frames"])
    for index in range(frame_count):
        arr, _ = render_cell(source, draft, track, index)
        alpha = arr[:, :, 3]
        mask = alpha >= 245
        opaque_total += int(mask.sum())
        if not mask.any():
            continue
        rgb = arr[:, :, :3][mask].astype(np.uint32)
        packed = (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
        values, counts = np.unique(packed, return_counts=True)
        bit = 1 << index
        for value, count in zip(values, counts):
            count = int(count)
            if int(value) in histogram:
                old_count, frames = histogram[int(value)]
                histogram[int(value)] = (old_count + count, frames | bit)
            else:
                histogram[int(value)] = (count, bit)

    recurrence = max(2, int(np.ceil(frame_count / 3)))
    minimum = max(70, opaque_total // 5000)
    candidates = []
    for packed, (count, frames) in histogram.items():
        seen_frames = frames.bit_count()
        if count < minimum and not (seen_frames >= recurrence and count >= 35):
            continue
        rgb = [(packed >> 16) & 255, (packed >> 8) & 255, packed & 255]
        candidates.append((rgb, count, frames))
    candidates.sort(key=lambda item: -item[1])
    candidates = candidates[:80]
    swatches = _merge_palette(candidates, BASE_SWATCHES)
    if not swatches:  # fully transparent / empty artwork
        swatches = [{
            "id": "s0", "rgb": [40, 40, 40], "hex": "#282828", "count": 0,
            "frames": [], "luminance": round(luminance_of_rgb([40, 40, 40]), 4),
        }]
    # Re-issue ids after the sort.
    for index, swatch in enumerate(swatches):
        swatch["id"] = f"s{index}"
    return swatches


def _classify(arr, centers):
    rgb = arr[:, :, :3].astype(np.float32)
    h, w, _ = rgb.shape
    labels = np.full((h, w), -1, dtype=np.int16)
    valid = arr[:, :, 3] >= 128
    flat = rgb.reshape(-1, 3)
    center_array = np.asarray(centers, dtype=np.float32)
    block = 60000
    assigned = np.empty(flat.shape[0], dtype=np.int16)
    for start in range(0, flat.shape[0], block):
        chunk = flat[start:start + block]
        distances = ((chunk[:, None, :] - center_array[None, :, :]) ** 2).sum(axis=2)
        assigned[start:start + block] = distances.argmin(axis=1).astype(np.int16)
    labels_flat = labels.reshape(-1)
    labels_flat[valid.reshape(-1)] = assigned[valid.reshape(-1)]
    return labels


def _count_pairs(left_a, left_b, dist_a=None, dist_b=None, radius=0, accumulator=None):
    different = (left_a != left_b) & (left_a >= 0) & (left_b >= 0)
    if not different.any():
        return
    aa = left_a[different]
    bb = left_b[different]
    keys = np.minimum(aa, bb).astype(np.int32) * 1000 + np.maximum(aa, bb).astype(np.int32)
    unique, counts = np.unique(keys, return_counts=True)
    for key, count in zip(unique.tolist(), counts.tolist()):
        a, b = divmod(key, 1000)
        previous = accumulator.get((a, b), 0)
        accumulator[(a, b)] = previous + int(count)


def analyze_edges(draft, track, source, swatches):
    centers = [swatch["rgb"] for swatch in swatches]
    count = len(swatches)
    accumulated = {}
    thin_mm = _swatch_thickness(draft, track, source, swatches)
    for index in range(len(draft["frames"])):
        arr, _ = render_cell(source, draft, track, index)
        labels = _classify(arr, centers)
        _count_pairs(labels[:, :-1], labels[:, 1:], None, None, 0, accumulated)
        _count_pairs(labels[:-1, :], labels[1:, :], None, None, 0, accumulated)
    edges = []
    for (a, b), total in accumulated.items():
        if total < 6:
            continue
        kind = "line" if thin_mm[a] <= THIN_HALF_MM * 2.2 or thin_mm[b] <= THIN_HALF_MM * 2.2 else "cut"
        edges.append({"a": swatches[a]["id"], "b": swatches[b]["id"],
                      "count": int(total),
                      "thin_mm_a": round(thin_mm[a], 3), "thin_mm_b": round(thin_mm[b], 3),
                      "kind": kind})
    edges.sort(key=lambda edge: -edge["count"])
    return edges


def _swatch_thickness(draft, track, source, swatches):
    """Estimate typical stroke thickness (mm) of every primary.

    For a long thin stroke, twice the mask area divided by its boundary pixel
    count approximates its width; broad cut-paper pieces give large values.
    Pure numpy so the module never depends on optional native GUI libraries.
    """
    count = len(swatches)
    samples = [[] for _ in range(count)]
    for index in range(len(draft["frames"])):
        arr, px_per_mm = render_cell(source, draft, track, index)
        labels = _classify(arr, [swatch["rgb"] for swatch in swatches])
        for label in range(count):
            mask = labels == label
            area = int(mask.sum())
            if area < 12:
                continue
            centre = mask[1:-1, 1:-1]
            boundary = (
                centre
                & (~mask[:-2, 1:-1] | ~mask[2:, 1:-1]
                   | ~mask[1:-1, :-2] | ~mask[1:-1, 2:])
            )
            edge_pixels = int(boundary.sum()) + 1
            samples[label].append(2.0 * area / edge_pixels / px_per_mm)
    thickness = []
    for label in range(count):
        thickness.append(float(np.median(samples[label])) if samples[label] else 99.0)
    return thickness


# ---------------------------------------------------------------------------
# Assignment suggestion
# ---------------------------------------------------------------------------

def default_assignment(swatches, tones):
    """Nearest printable gray by luminance -- the conservative fallback."""
    tone_luma = [luminance_of_rgb([v, v, v]) for v in tones]
    assignment = {}
    for swatch in swatches:
        lum = swatch.get("luminance", luminance_of_rgb(swatch["rgb"]))
        assignment[swatch["id"]] = int(np.argmin([abs(lum - tone) for tone in tone_luma]))
    return assignment


def suggest_assignment(sep):
    swatches = sep.get("swatches", [])
    tones = tone_bytes(sep)
    ids = [swatch["id"] for swatch in swatches]
    index_of = {identifier: index for index, identifier in enumerate(ids)}
    fallback = default_assignment(swatches, tones)

    weighted_edges = []
    for edge in sep.get("edges", []):
        if edge["a"] in index_of and edge["b"] in index_of:
            multiplier = 3.0 if edge.get("kind") == "line" else 1.0
            weighted_edges.append((index_of[edge["a"]], index_of[edge["b"]],
                                   float(edge["count"]) * multiplier))

    mark_pairs = []
    for mark in sep.get("marks", []):
        pair = mark.get("swatches") or []
        if len(pair) == 2 and pair[0] in index_of and pair[1] in index_of:
            if pair[0] != pair[1]:
                mark_pairs.append((index_of[pair[0]], index_of[pair[1]], mark["id"]))

    prior_preferred = [index_of and fallback[identifier] for identifier in ids]

    best = None
    k = len(ids)
    if k == 0:
        assignment = {}
    else:
        for values in product((0, 1), repeat=k):
            marks_kept = sum(1 for a, b, _ in mark_pairs if values[a] != values[b])
            edge_score = sum(weight for a, b, weight in weighted_edges
                             if values[a] != values[b])
            prior = sum(1e-4 for i in range(k) if values[i] == prior_preferred[i])
            score = marks_kept * MARK_WEIGHT + edge_score + prior
            if best is None or score > best[0]:
                best = (score, values, marks_kept, edge_score)
        assignment = {identifier: int(level) for identifier, level in zip(ids, best[1])}

    # Preserve explicit user overrides if the caller locked them.
    locked = sep.get("locked") or {}
    for identifier, level in locked.items():
        if identifier in assignment and int(level) in (0, 1):
            assignment[identifier] = int(level)

    return evaluate_assignment(sep, assignment)


def evaluate_assignment(sep, assignment):
    swatches = sep.get("swatches", [])
    tones = tone_bytes(sep)
    ids = [swatch["id"] for swatch in swatches]
    fallback = default_assignment(swatches, tones)
    assignment = {identifier: int(assignment.get(identifier, fallback.get(identifier, 0)))
                  for identifier in ids}

    edge_results = []
    cut_total = cut_kept = line_total = line_kept = 0
    for edge in sep.get("edges", []):
        a, b = edge["a"], edge["b"]
        kept = assignment.get(a, fallback.get(a, 0)) != assignment.get(b, fallback.get(b, 1))
        row = {**edge, "kept": bool(kept)}
        edge_results.append(row)
        if edge.get("kind") == "line":
            line_total += 1
            line_kept += int(kept)
        else:
            cut_total += 1
            cut_kept += int(kept)

    mark_results = []
    for mark in sep.get("marks", []):
        pair = mark.get("swatches") or []
        separable = None
        if len(pair) == 2 and pair[0] in assignment and pair[1] in assignment:
            separable = assignment[pair[0]] != assignment[pair[1]]
        result = {"id": mark.get("id"), "separable": separable}
        mark_results.append(result)

    marks_total = sum(1 for result in mark_results if result["separable"] is not None)
    marks_separable = sum(1 for result in mark_results if result["separable"] is True)
    summary = {
        "swatches": len(ids), "tones": tones,
        "cut_edges": cut_total, "cut_edges_kept": cut_kept,
        "line_edges": line_total, "line_edges_kept": line_kept,
        "marks_total": marks_total, "marks_separable": marks_separable,
        "marks_merged": marks_total - marks_separable,
    }
    return assignment, edge_results, mark_results, summary


# ---------------------------------------------------------------------------
# Mark construction
# ---------------------------------------------------------------------------

def sample_point_color(arr, point):
    h, w = arr.shape[:2]
    u = min(1, max(0, float(point[0])))
    v = min(1, max(0, float(point[1])))
    x = min(w - 1, max(0, int(round(u * (w - 1)))))
    y = min(h - 1, max(0, int(round(v * (h - 1)))))
    radius = 3
    patch = arr[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1]
    opaque = patch[patch[:, :, 3] >= 160]
    if len(opaque) >= 5:
        rgb = np.median(opaque[:, :3], axis=0)
    else:
        rgb = arr[y, x, :3]
    return [int(round(c)) for c in rgb], [u, v]


def ensure_swatch_for_color(sep, rgb, tight=False):
    swatches = sep.setdefault("swatches", [])
    centers = [item["rgb"] for item in swatches]
    threshold = 15.0 if tight else MARK_DISTANCE
    index = nearest_index(rgb, centers) if centers else -1
    if index >= 0 and color_distance(rgb, swatches[index]["rgb"]) <= threshold:
        return swatches[index]["id"]
    if len(swatches) < MAX_SWATCHES:
        identifier = f"m{len(swatches)}"
        existing = {item["id"] for item in swatches}
        while identifier in existing:
            identifier += "x"
        swatch = {"id": identifier, "rgb": list(rgb), "hex": rgb_hex(rgb),
                  "count": 0, "frames": [], "dedicated": True, "marked": True,
                  "luminance": round(luminance_of_rgb(rgb), 4)}
        swatches.append(swatch)
        return identifier
    # No room: share the closest existing primary rather than blow up the
    # optimiser complexity.
    return swatches[index]["id"] if index >= 0 else swatches[0]["id"]


def resolve_mark_pair(sep, colors):
    """Resolve two clicked colours to two distinct assignment variables."""
    first = ensure_swatch_for_color(sep, colors[0], tight=True)
    second = ensure_swatch_for_color(sep, colors[1], tight=True)
    if first == second and color_distance(colors[0], colors[1]) > 12:
        # The auto palette collapsed two colours the user explicitly needs:
        # split them into two dedicated variables so the optimiser can try.
        swatches = sep["swatches"]
        shared = next(item for item in swatches if item["id"] == first)
        if not shared.get("dedicated"):
            shared["dedicated"] = True
            shared["rgb"] = list(colors[0])
            shared["hex"] = rgb_hex(colors[0])
            shared["luminance"] = round(luminance_of_rgb(colors[0]), 4)
        second = ensure_swatch_for_color(sep, colors[1], tight=True)
        if second == first and len(swatches) < MAX_SWATCHES:
            identifier = f"m{len(swatches)}b"
            existing = {item["id"] for item in swatches}
            while identifier in existing:
                identifier += "x"
            swatches.append({"id": identifier, "rgb": list(colors[1]),
                             "hex": rgb_hex(colors[1]), "count": 0, "frames": [],
                             "dedicated": True, "marked": True,
                             "luminance": round(luminance_of_rgb(colors[1]), 4)})
            second = identifier
    return [first, second]


def normalize_points(points):
    if not isinstance(points, (list, tuple)) or len(points) != 2:
        raise Invalid("A marked pair needs two clicked points.")
    clean = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise Invalid("Each click needs normalized x and y coordinates.")
        u, v = float(point[0]), float(point[1])
        if not (0 <= u <= 1 and 0 <= v <= 1):
            raise Invalid("Click coordinates must lie on the artwork.")
        clean.append([u, v])
    return clean


# ---------------------------------------------------------------------------
# Device-pixel quantisation and final ribbon verification
# ---------------------------------------------------------------------------

def output_lookup(sep, stock):
    """Return centers and exact output RGB for every classified label."""
    swatches = sep.get("swatches", [])
    tones = tone_bytes(sep)
    assignment = sep.get("assignment") or default_assignment(swatches, tones)
    fallback = default_assignment(swatches, tones)
    paper = list(raster.PAPER[stock])
    palette = []
    outputs = []
    for swatch in swatches:
        level = assignment.get(swatch["id"], fallback.get(swatch["id"], 0))
        level = 0 if int(level) not in (0, 1) else int(level)
        palette.append(swatch["rgb"])
        outputs.append([tones[level]] * 3)
    palette.append(paper)
    outputs.append(paper)
    return np.asarray(palette, dtype=np.float32), np.asarray(outputs, dtype=np.uint8)


def quantize_image(image, sep, stock):
    """Hard-map every pixel to paper or one of the two ink tones."""
    rgb_image = image.convert("RGB")
    arr = np.asarray(rgb_image, dtype=np.float32)
    centers, lookup = output_lookup(sep, stock)
    if not len(centers):
        return rgb_image
    labels = nearest_labels(arr.reshape(-1, 3), centers)
    result = lookup[labels].reshape(arr.shape).astype(np.uint8)
    return Image.fromarray(result, "RGB")


def nearest_labels(flat_pixels, centers):
    labels = np.empty(flat_pixels.shape[0], dtype=np.int32)
    block = 200000
    for start in range(0, flat_pixels.shape[0], block):
        chunk = flat_pixels[start:start + block]
        distances = ((chunk[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels[start:start + block] = distances.argmin(axis=1)
    return labels


def separated_cell(source_image, frame, size, physical, sep, stock):
    """Index the source art to ink levels, then scale labels with NEAREST.

    Classifying before resizing keeps thin source strokes on one level instead
    of letting resampling ringing split them, and NEAREST cannot introduce
    tones absent from the device palette.
    """
    from PIL import ImageOps

    artwork = raster.matte(source_image, frame["transform"]["matte"])
    if frame["transform"]["flip"]:
        artwork = ImageOps.mirror(artwork)
    width, height = size
    scale = min(width / artwork.width, height / artwork.height) * frame["transform"]["scale"]
    drawn_size = (max(1, round(artwork.width * scale)), max(1, round(artwork.height * scale)))
    sx = frame["transform"]["shift_x"] * width / physical[0]
    sy = frame["transform"]["shift_y"] * height / physical[1]
    offset = (round((width - drawn_size[0]) / 2 + sx),
              round((height - drawn_size[1]) / 2 + sy))

    rgba = np.asarray(artwork.convert("RGBA"))
    # Downscale labels, not colours: nearest resampling keeps exact source
    # tones and thin strokes coherent while classifying at print resolution.
    if (rgba.shape[1], rgba.shape[0]) != drawn_size:
        scaled = artwork.convert("RGBA").resize(drawn_size, Image.Resampling.NEAREST)
        rgba = np.asarray(scaled)
    centers, lookup = output_lookup(sep, stock)
    if len(sep.get("swatches", [])):
        labels = nearest_labels(rgba[:, :, :3].reshape(-1, 3).astype(np.float32),
                                centers).reshape(rgba.shape[:2])
        alpha = rgba[:, :, 3]
        paper_label = len(sep["swatches"])
        labels[alpha < 128] = paper_label
        indexed = Image.fromarray(lookup[labels].astype(np.uint8), "RGB")
    else:
        indexed = Image.fromarray(rgba[:, :, :3].astype(np.uint8), "RGB")
    drawn = indexed.resize(drawn_size, Image.Resampling.NEAREST)

    paper = tuple(raster.PAPER[stock])
    cell = Image.new("RGB", size, paper)
    cell.paste(drawn, offset)
    return cell


def separated_ribbon(track, frames, source, stock, sep):
    """Whole device-resolution ribbon carrying only paper and the two grays."""
    band = Image.new("RGB", track.exact_image_size, tuple(raster.PAPER[stock]))
    for frame, cell in zip(frames, track.cells, strict=True):
        left, right = track.x(cell.start), track.x(cell.end)
        rendered = separated_cell(source(frame), frame, (right - left, band.height),
                                  (cell.width, track.height), sep, stock)
        band.paste(rendered, (left, 0))
    return band


def _modal_output(patch, allowed):
    pixels = patch.reshape(-1, 3)
    best_color = None
    best_count = -1
    for color in allowed:
        count = int((np.abs(pixels.astype(int) - np.asarray(color)).sum(axis=1) == 0).sum())
        if count > best_count:
            best_count = count
            best_color = tuple(color)
    if best_count <= 0:
        center = nearest_index(pixels.mean(axis=0), allowed)
        best_color = tuple(allowed[center])
    return best_color


def check_marks(band, track, draft, sep):
    """Read marked edges back from the final quantised device ribbon."""
    array = np.asarray(band.convert("RGB"))
    height, width = array.shape[:2]
    stock = draft["settings"]["stock"]
    paper = tuple(raster.PAPER[stock])
    tones = tone_bytes(sep)
    allowed = [paper] + [tuple([value] * 3) for value in tones]
    frame_ids = [frame["id"] for frame in draft["frames"]]
    checks = []
    radius = max(2, round(.5 * track.pixels_per_mm))

    for mark in sep.get("marks", []):
        frame_index = frame_ids.index(mark["frame"]) if mark.get("frame") in frame_ids \
            else int(mark.get("frame_index", mark.get("frame", 0)))
        frame_index = max(0, min(len(track.cells) - 1, frame_index))
        cell = track.cells[frame_index]
        x0, x1 = track.x(cell.start), track.x(cell.end)
        points = mark.get("points") or [[.5, .5], [.55, .55]]
        device_points = []
        colors = []
        for point in points:
            u, v = float(point[0]), float(point[1])
            px = min(width - 1, max(0, round(x0 + u * (x1 - x0))))
            py = min(height - 1, max(0, round(v * height)))
            left = max(0, px - radius)
            right = min(width, px + radius + 1)
            top = max(0, py - radius)
            bottom = min(height, py + radius + 1)
            color = _modal_output(array[top:bottom, left:right], allowed)
            device_points.append((px, py))
            colors.append(list(color))

        (ax, ay), (bx, by) = device_points
        steps = max(2, int(round(np.hypot(bx - ax, by - ay))) + 1)
        sequence = []
        for step in range(steps):
            t = step / max(1, steps - 1)
            x = min(width - 1, max(0, round(ax + (bx - ax) * t)))
            y = min(height - 1, max(0, round(ay + (by - ay) * t)))
            sequence.append(tuple(int(c) for c in array[y, x]))
        crossings = sum(1 for prev, nxt in zip(sequence, sequence[1:]) if prev != nxt)

        separated = colors[0] != colors[1]
        checks.append({
            "id": mark.get("id"), "label": mark.get("label") or f"Pair {len(checks) + 1}",
            "frame": frame_index + 1, "frame_id": mark.get("frame"),
            "separated": bool(separated),
            "status": "separated" if separated else "merged",
            "crossings": int(crossings),
            "tone_a": rgb_hex(colors[0]), "tone_b": rgb_hex(colors[1]),
            "source_a": (mark.get("colors") or [None, None])[0],
            "source_b": (mark.get("colors") or [None, None])[1],
            "points": points,
        })
    return checks


# ---------------------------------------------------------------------------
# Draft state lifecycle
# ---------------------------------------------------------------------------

def default_state():
    return {
        "tones": [
            {"id": "tone-1", "value": DEFAULT_TONES[0], "name": "Gray ink 1"},
            {"id": "tone-2", "value": DEFAULT_TONES[1], "name": "Gray ink 2"},
        ],
        "swatches": [],
        "assignment": {},
        "edges": [],
        "marks": [],
        "adopted": False,
        "sampled": False,
        "summary": None,
    }


def normalize_state(stored, draft=None):
    """Return a complete, response-safe separation document."""
    state = default_state()
    if isinstance(stored, dict):
        for key in ("tones", "swatches", "assignment", "edges", "marks",
                    "adopted", "sampled", "summary", "locked"):
            if key in stored:
                state[key] = stored[key]
    tones = coerce_tones(state["tones"]) or coerce_tones(
        {"tones": DEFAULT_TONES})
    state["tones"] = [
        {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
        {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
    ]
    if not isinstance(state["swatches"], list):
        state["swatches"] = []
    if not isinstance(state["assignment"], dict):
        state["assignment"] = {}
    if not isinstance(state["marks"], list):
        state["marks"] = []
    state["adopted"] = bool(state["adopted"])
    state["sampled"] = bool(state["sampled"])

    valid_frames = {frame["id"] for frame in (draft or {}).get("frames", [])}
    clean_marks = []
    for index, mark in enumerate(state["marks"]):
        if not isinstance(mark, dict):
            continue
        if valid_frames and mark.get("frame") not in valid_frames:
            continue
        mark.setdefault("id", f"pair-{index + 1}")
        mark.setdefault("label", f"Pair {index + 1}")
        clean_marks.append(mark)
    for index, mark in enumerate(clean_marks):
        mark["label"] = mark.get("label") or f"Pair {index + 1}"
    state["marks"] = clean_marks
    return state


def build_state(draft, track, source, payload=None, existing=None):
    """Sample global primaries, analyse adjacency and propose assignment."""
    state = normalize_state(existing if existing is not None else draft.get("separation"), draft)
    payload = payload or {}
    tones = coerce_tones(payload)
    if tones is not None:
        state["tones"] = [
            {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
            {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
        ]
    fresh = sample_palette(draft, track, source)
    dedicated = [item for item in state.get("swatches", []) if item.get("dedicated")]
    state["swatches"] = fresh + dedicated
    state["edges"] = analyze_edges(draft, track, source, fresh)
    state["sampled"] = True

    # Re-point marks at current swatch variables, splitting explicitly marked
    # colours back apart if the global palette merged them.
    for mark in state["marks"]:
        colors = mark.get("colors")
        if isinstance(colors, list) and len(colors) == 2:
            mark["swatches"] = resolve_mark_pair(state, colors)

    assignment, edge_results, mark_results, summary = suggest_assignment(state)
    state["assignment"] = assignment
    state["edges"] = edge_results
    state["mark_preview"] = mark_results
    state["summary"] = summary
    return state


def add_mark(state, payload, draft, track, source):
    """Attach one user-clicked adjacent colour pair to the global plan."""
    state = normalize_state(state, draft)
    frames = draft["frames"]
    if len(frames) < 2:
        raise Invalid("Load at least two frames before marking colour pairs.")

    frame_ref = payload.get("frame", payload.get("frame_id", payload.get("frame_index", 0)))
    if isinstance(frame_ref, str) and frame_ref in {f["id"] for f in frames}:
        frame_id = frame_ref
        frame_index = next(i for i, f in enumerate(frames) if f["id"] == frame_id)
    else:
        try:
            frame_index = int(frame_ref)
        except (TypeError, ValueError):
            frame_index = draft["settings"].get("proof_frame", 0)
        frame_index = max(0, min(len(frames) - 1, frame_index))
        frame_id = frames[frame_index]["id"]

    colors = payload.get("colors") or payload.get("colour") or payload.get("rgb")
    points = payload.get("points")
    if payload.get("point") and not points:
        points = [payload["point"], payload.get("second_point")]

    if points is not None and len(points) == 2:
        points = normalize_points(points)
        arr, _ = render_cell(source, draft, track, frame_index)
        sampled = []
        for point in points:
            rgb, point = sample_point_color(arr, point)
            sampled.append(rgb)
        colors = sampled
    elif isinstance(colors, list) and len(colors) == 2:
        colors = [hex_rgb(c) if isinstance(c, str) else [int(c[0]), int(c[1]), int(c[2])]
                  for c in colors]
        points = payload.get("points") or [[.42, .46], [.58, .54]]
        points = normalize_points(points) if points and len(points) == 2 else [[.42, .46], [.58, .54]]
    else:
        raise Invalid("Mark a pair with two artwork clicks or two colours.")

    if not state["swatches"]:
        state = build_state(draft, track, source, existing=state)

    ids = resolve_mark_pair(state, colors)
    # Recompute luminance metadata / assignment fallback for appended swatches.
    for swatch in state["swatches"]:
        swatch.setdefault("luminance", round(luminance_of_rgb(swatch["rgb"]), 4))

    mark_id = payload.get("id") or f"pair-{len(state['marks']) + 1}-{uuid4().hex[:6]}"
    mark = {
        "id": str(mark_id),
        "label": str(payload.get("label") or f"Pair {len(state['marks']) + 1}")[:80],
        "frame": frame_id,
        "frame_index": frame_index,
        "points": points,
        "colors": colors,
        "swatches": ids,
    }
    state["marks"].append(mark)

    assignment, edge_results, mark_results, summary = suggest_assignment(state)
    state["assignment"] = assignment
    state["edges"] = edge_results
    state["mark_preview"] = mark_results
    state["summary"] = summary
    return state


def public_state(draft):
    """State returned by GET endpoints, with lightweight alias fields."""
    state = normalize_state(draft.get("separation"), draft)
    if state.get("assignment") and state.get("swatches"):
        assignment, edges, marks, summary = evaluate_assignment(state, state["assignment"])
        state["assignment"] = assignment
        state["edge_view"] = edges
        state["mark_preview"] = marks
        state["summary"] = summary or state.get("summary")
    # Convenience aliases for API clients.
    values = [tone["value"] for tone in state["tones"]]
    state["gray_levels"] = values
    state["levels"] = values
    state["ink_levels"] = values
    state["mapping"] = state["assignment"]
    state["pairs"] = state["marks"]
    return state


# Compatibility aliases for alternate naming of the same workflow.
sample_colors = sample_palette
build_palette = sample_palette
analyse_edges = analyze_edges
suggest_mapping = suggest_assignment
build_separation = build_state
separate_ribbon = separated_ribbon
render_separated_ribbon = separated_ribbon
apply_separation = quantize_image
verify_edges = check_marks
inspect_marks = check_marks
