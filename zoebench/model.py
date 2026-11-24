"""Configuration and input validation for measured drum projects."""

from math import isfinite
from pathlib import PurePath
from re import fullmatch

from image_lab.validation import Invalid

MAX_IMAGES = 24
MAX_SAVED_PROOFS = 30
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
    "stencil": "Stencil cut sheet",
    "stencil-brush": "Stencil brush proof",
}

MODE_ALIASES = {
    "stencil-cut": "stencil",
    "stencil-guide": "stencil",
    "stencil-sheet": "stencil",
    "bridges": "stencil",
    "tie-guide": "stencil",
    "stencil-proof": "stencil-brush",
    "brush": "stencil-brush",
    "brush-proof": "stencil-brush",
}


def canonical_mode(value):
    return MODE_ALIASES.get(value, value)
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
    "bridge_mm": 2.0,
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
    if "bridge_mm" not in data:
        for alias in ("min_bridge_mm", "bridge_width_mm", "tie_width_mm"):
            if alias in data and data[alias] is not None:
                source["bridge_mm"] = data[alias]
                break
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
        "bridge_mm": number(source.get("bridge_mm", 2.0), "stencil tie width (mm)", .5, 12),
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
