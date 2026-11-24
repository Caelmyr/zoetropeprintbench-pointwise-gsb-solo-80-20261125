"""Configuration and input validation for measured drum projects."""

from math import isfinite
from pathlib import PurePath
from re import fullmatch

from image_lab.validation import Invalid

MAX_IMAGES = 24
MAX_SAVED_PROOFS = 30
MAX_REFERENCE_REGIONS = 12
CALIBRATION_STATUSES = ("off", "reference", "aligned", "limited", "edge-limited", "unresolved", "manual")
RELIABLE_STATUSES = ("reference", "aligned", "limited", "edge-limited", "manual")
DEFAULT_CALIBRATION = {
    "shift_x": 0.0,
    "shift_y": 0.0,
    "dx": 0.0,
    "dy": 0.0,
    "status": "off",
    "confidence": None,
    "score": None,
    "reason": None,
    "method": None,
    "updated": None,
}
PROOF_MODES = {
    "ribbon": "Print ribbon",
    "cut-guide": "Cut guide",
    "slot-ruler": "Slot ruler",
    "direct-loop": "Direct frame loop",
    "optical-loop": "Slit-view proof",
    "neighbors": "Previous and next frame",
    "coverage": "Print clipping map",
    "motion": "Loop motion trace",
    "contrast": "Proof tone inspection",
    "scan": "Measured slots",
    "scale": "Print scale target",
    "assembly": "Join check",
}
DEFAULT_SETTINGS = {
    "circumference": 288,
    "height_mm": 47,
    "dpi": 240,
    "slot_width": 2.4,
    "slot_phase": 0,
    "rpm": 42,
    "exposure_ms": 12,
    "join_mm": 8,
    "action_margin": 1.5,
    "stock": "ivory",
    "direction": "clockwise",
    "proof_frame": 0,
    "threshold": 0.56,
    "align_max_shift_mm": 6,
    "align_edge_tolerance": 0.05,
}


def document(value):
    if not isinstance(value, dict):
        raise Invalid("Provide a JSON object.")
    return value


def string(value, label, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise Invalid(f"Enter {label} with 1 to {limit} characters.")
    return value.strip()


def filename(value):
    clean = PurePath((value or "").replace("\\", "/")).name
    return (clean if clean not in {"", ".", ".."} else "source.png")[:160]


def identifier(value):
    if not isinstance(value, str) or not fullmatch(r"[0-9a-f]{24}", value):
        raise Invalid("The record identifier is invalid.")
    return value


def number(value, label, lower, upper):
    try:
        if isinstance(value, bool):
            raise ValueError
        parsed = float(value)
    except (ValueError, TypeError) as error:
        raise Invalid(f"Enter a number for {label}.") from error
    if not isfinite(parsed) or not lower <= parsed <= upper:
        raise Invalid(f"{label} must be between {lower} and {upper}.")
    return parsed


def integer(value, label, lower, upper):
    parsed = number(value, label, lower, upper)
    if parsed != int(parsed):
        raise Invalid(f"{label} must be a whole number.")
    return int(parsed)


def option(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        raise Invalid(f"Choose a valid {label}.")
    return value


def find(records, key, label):
    identifier(key)
    result = next((record for record in records if record["id"] == key), None)
    if result is None:
        raise Invalid(f"The {label} is unavailable in this drum.")
    return result


def settings(data, current=None):
    source = {**DEFAULT_SETTINGS, **(current or {}), **document(data)}
    return {
        "circumference": number(source["circumference"], "drum circumference (mm)", 120, 680),
        "height_mm": number(source["height_mm"], "art height (mm)", 20, 100),
        "dpi": integer(source["dpi"], "printer DPI", 120, 360),
        "slot_width": number(source["slot_width"], "viewing slit width (mm)", .6, 8),
        "slot_phase": number(source["slot_phase"], "slit phase (mm)", -12, 12),
        "rpm": number(source["rpm"], "rotation (RPM)", 8, 95),
        "exposure_ms": number(source["exposure_ms"], "proof exposure (ms)", 2, 48),
        "join_mm": number(source["join_mm"], "blank join tab (mm)", 0, 20),
        "action_margin": number(source["action_margin"], "art quiet margin (mm)", .25, 5),
        "stock": option(source["stock"], ("ivory", "white", "black"), "paper stock"),
        "direction": option(source["direction"], ("clockwise", "counterclockwise"), "rotation direction"),
        "proof_frame": integer(source["proof_frame"], "proof frame", 0, MAX_IMAGES - 1),
        "threshold": number(source["threshold"], "slot brightness", .1, .95),
        "align_max_shift_mm": number(source["align_max_shift_mm"], "maximum alignment offset (mm)", .25, 25),
        "align_edge_tolerance": number(source["align_edge_tolerance"], "edge ink tolerance", 0, .25),
    }


def frame_transform(data, current=None):
    defaults = {"shift_x": 0, "shift_y": 0, "scale": 1, "matte": "original", "flip": False}
    value = {**defaults, **(current or {}), **document(data)}
    if type(value["flip"]) is not bool:
        raise Invalid("Flip must be on or off.")
    return {
        "shift_x": number(value["shift_x"], "horizontal adjustment (mm)", -25, 25),
        "shift_y": number(value["shift_y"], "vertical adjustment (mm)", -25, 25),
        "scale": number(value["scale"], "picture scale", .25, 2.5),
        "matte": option(value["matte"], ("original", "white", "black"), "frame matte"),
        "flip": value["flip"],
    }


def calibration_of(frame):
    """The base negative layer for a frame, tolerating older drafts."""
    current = frame.get("calibration") if isinstance(frame, dict) else None
    if not isinstance(current, dict):
        return dict(DEFAULT_CALIBRATION)
    return {**DEFAULT_CALIBRATION, **current}


def frame_calibration(data, current=None):
    """Validate the calibration layer. User picture transforms stay separate."""
    value = {**DEFAULT_CALIBRATION, **(current or {}), **document(data)}
    status = value.get("status", "off")
    if status not in CALIBRATION_STATUSES:
        status = "manual"

    def finite_number(key, label, lower, upper):
        raw = value.get(key)
        if raw is None:
            return 0.0
        return number(raw, label, lower, upper)

    result = {
        "shift_x": finite_number("shift_x", "calibration horizontal shift (mm)", -25, 25),
        "shift_y": finite_number("shift_y", "calibration vertical shift (mm)", -25, 25),
        "dx": finite_number("dx", "calibration source x fraction", -2, 2),
        "dy": finite_number("dy", "calibration source y fraction", -2, 2),
        "status": status,
        "confidence": None,
        "score": None,
        "reason": None,
        "method": value.get("method") if isinstance(value.get("method"), str) else None,
        "updated": value.get("updated") if isinstance(value.get("updated"), str) else None,
    }
    confidence = value.get("confidence")
    if confidence is not None:
        result["confidence"] = number(confidence, "alignment confidence", 0, 1)
    score = value.get("score")
    if score is not None:
        result["score"] = number(score, "alignment score", -2, 2)
    reason = value.get("reason")
    if isinstance(reason, str) and len(reason) <= 80:
        result["reason"] = reason
    return result


def region_box(value):
    if isinstance(value, dict):
        if isinstance(value.get("box"), (list, tuple)):
            value = value["box"]
        elif isinstance(value.get("rect"), (list, tuple)):
            value = value["rect"]
        elif all(key in value for key in ("x", "y", "width", "height")):
            x, y = float(value["x"]), float(value["y"])
            value = [x, y, x + float(value["width"]), y + float(value["height"])]
        elif all(key in value for key in ("left", "top", "right", "bottom")):
            value = [value["left"], value["top"], value["right"], value["bottom"]]
        elif isinstance(value.get("bounds"), (list, tuple)):
            value = value["bounds"]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise Invalid("Draw each reference mark as a rectangle with four coordinates.")
    x1, y1, x2, y2 = (number(point, "normalized reference coordinates", -2, 2) for point in value)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(1, x2), min(1, y2)
    if x2 - x1 < .004 or y2 - y1 < .004:
        raise Invalid("Each reference rectangle needs a little width and height.")
    return [x1, y1, x2, y2]


def reference_regions(value):
    if isinstance(value, dict):
        value = value.get("regions") or value.get("marks") or value.get("landmarks")
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(
            isinstance(point, (int, float)) for point in value):
        value = [value]
    if not isinstance(value, list) or not value:
        raise Invalid("Mark at least one fixed feature, such as the moon or ground line.")
    if len(value) > MAX_REFERENCE_REGIONS:
        raise Invalid(f"Use at most {MAX_REFERENCE_REGIONS} reference marks.")
    regions = []
    for entry in value:
        label = None
        box = entry
        if isinstance(entry, dict):
            label = entry.get("label") or entry.get("name")
            box = entry.get("box") or entry.get("rect") or entry.get("bounds") or entry
        coords = region_box(box)
        regions.append({"box": coords, "label": string(label, "reference label", 40) if isinstance(label, str) and label.strip() else None})
    return regions


def max_alignment_shift(data, settings=None):
    raw = None
    for key in ("max_shift_mm", "max_offset_mm", "max_shift", "maximum_offset_mm", "limit_mm", "alignment_limit_mm"):
        if isinstance(data, dict) and data.get(key) is not None:
            raw = data[key]
            break
    if raw is None:
        raw = (settings or {}).get("align_max_shift_mm", DEFAULT_SETTINGS["align_max_shift_mm"])
    return number(raw, "maximum alignment offset (mm)", .25, 25)


def box(value):
    if not isinstance(value, list) or len(value) != 4:
        raise Invalid("Choose a rectangle within the source scan.")
    x1, y1, x2, y2 = (number(point, "normalized scan coordinates", 0, 1) for point in value)
    if x2 - x1 < .02 or y2 - y1 < .02:
        raise Invalid("Draw a wider source rectangle from top left to bottom right.")
    return [x1, y1, x2, y2]


def source_scan(data, width, height, circumference):
    crop = box(data.get("crop", [.03, .19, .97, .82]))
    if (crop[2] - crop[0]) * width < 20 or (crop[3] - crop[1]) * height < 12:
        raise Invalid("The scan rectangle needs at least 20×12 source pixels.")
    return {
        "crop": crop,
        "scan_span_mm": number(data.get("scan_span_mm", circumference), "the scan rectangle width (mm)", 10, 800),
        "origin_mm": number(data.get("origin_mm", 0), "scan start relative to the join mark (mm)", -40, 700),
    }


def frame_list_order(payload, frames):
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise Invalid("Frame order must list every frame exactly once.")
    ids = [frame["id"] for frame in frames]
    if sorted(payload) != sorted(ids):
        raise Invalid("Frame order must list every frame exactly once.")
    by_id = {frame["id"]: frame for frame in frames}
    return [by_id[key] for key in payload]
