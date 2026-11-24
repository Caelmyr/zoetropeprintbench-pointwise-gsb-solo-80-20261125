"""Adopt, verify and revert a measured-slot print placement on a drum."""

from hashlib import sha256
from json import dumps

from image_lab.validation import Invalid

from . import fitting, layout, raster, scan

KIND = "measured-slots"
TRACKED = ("circumference", "height_mm", "dpi", "join_mm", "action_margin")


def settings_digest(settings):
    """Identify the drum and slit setup a placement was fitted against."""
    return sha256(dumps({key: settings[key] for key in TRACKED}, sort_keys=True).encode()).hexdigest()


def scan_readings(draft, library):
    sample = draft.get("scan")
    if not sample:
        raise Invalid("Import a flat photograph of the slit ring before fitting a measured placement.")
    picture = raster.decode(library.blob(sample["blob"]).read_bytes())
    _, summary, readings = scan.extract(picture, sample["registration"], draft["settings"]["threshold"])
    if not readings:
        raise Invalid("No openings were measured inside the saved scan rectangle.")
    return summary, readings


def recommendation(draft, library):
    """Fit the current photograph against the drum's frames and entered width."""
    if len(draft["frames"]) < 2:
        raise Invalid("Load at least two picture frames before fitting a measured placement.")
    width = draft["settings"]["cell_width"]
    if width == "":
        raise Invalid("Enter the uniform picture cell width in millimetres before fitting.")
    if draft["settings"]["action_margin"] * 2 >= min(width, draft["settings"]["height_mm"]):
        raise Invalid("The quiet margin must leave a visible area inside every picture cell.")
    summary, readings = scan_readings(draft, library)
    report = fitting.recommend(readings, draft["settings"]["circumference"], width)
    report["quiet_margin_mm"] = draft["settings"]["action_margin"]
    report["scan"] = summary
    report["frame_count"] = len(draft["frames"])
    if report["feasible"] and len(report["cells"]) != len(draft["frames"]):
        report["feasible"] = False
        report["blocked"] = {
            "reason": "count",
            "message": (
                f"The photograph gives {len(report['cells'])} complete opening"
                f"{'s' if len(report['cells']) != 1 else ''} but the drum has {len(draft['frames'])} picture "
                "frames. Measure one complete opening per picture, or keep printing the nominal band."
            ),
        }
    if not report["feasible"] and report["incomplete_openings"] and "blocked" in report:
        report["blocked"]["message"] += (
            f" {report['incomplete_openings']} opening"
            f"{'s reach' if report['incomplete_openings'] != 1 else ' reaches'} an edge of the photograph, so "
            "the whole slit ring was not measured."
        )
    return report


def record(report, draft):
    """Turn a feasible report into the retained placement record."""
    if not report.get("feasible"):
        raise Invalid("Only a placement that fits every measured opening can be adopted.")
    if len(report["cells"]) != len(draft["frames"]):
        raise Invalid("The measured placement must have one cell per picture frame.")
    return {
        "kind": KIND,
        "cell_width_mm": report["cell_width_mm"],
        "cells": [
            {"slot": row["slot"], "start_mm": row["start_mm"], "end_mm": row["end_mm"],
             "photo_center_mm": row["photo_center_mm"], "width_mm": row["width_mm"],
             "projected_center_mm": row["projected_center_mm"],
             "projected_span_mm": row["projected_span_mm"],
             "center_offset_mm": row["center_offset_mm"], "gap_after_mm": row["gap_after_mm"]}
            for row in report["cells"]
        ],
        "report": {
            "circumference_mm": report.get("circumference_mm"),
            "widest_cell_mm": report.get("widest_cell_mm"),
            "mean_abs_center_offset_mm": report.get("mean_abs_center_offset_mm"),
            "maximum_center_offset_mm": report.get("maximum_center_offset_mm"),
            "internal_gap_min_mm": report.get("internal_gap_min_mm"),
            "internal_gap_max_mm": report.get("internal_gap_max_mm"),
            "internal_gap_range_mm": report.get("internal_gap_range_mm"),
            "gap_after_join_mm": report.get("gap_after_join_mm"),
            "join_lead_gap_mm": report.get("join_lead_gap_mm"),
            "threshold": draft["settings"]["threshold"],
            "frame_count": report.get("frame_count"),
            "measured_openings": report.get("measured_openings"),
            "quiet_margin_mm": report.get("quiet_margin_mm"),
        },
        "scan_blob": draft["scan"]["blob"],
        "scan_filename": draft["scan"]["filename"],
        "scan_registration": dict(draft["scan"]["registration"]),
        "scan_threshold": draft["settings"]["threshold"],
        "settings_digest": settings_digest(draft["settings"]),
    }


def check(draft, placement, readings=None):
    """Re-verify a retained placement against the photograph and the drum setup."""
    if not isinstance(placement, dict) or placement.get("kind") != KIND:
        raise Invalid("This drum does not have a measured print placement.")
    if placement.get("settings_digest") != settings_digest(draft["settings"]):
        raise Invalid(
            "The drum circumference, art height, DPI, join tab or quiet margin changed since this placement "
            "was adopted. Fit the measured placement again."
        )
    sample = draft.get("scan")
    if not sample or sample["blob"] != placement.get("scan_blob"):
        raise Invalid("The slit photograph changed since this placement was adopted. Fit the placement again.")
    if len(placement["cells"]) != len(draft["frames"]):
        raise Invalid(
            f"The measured placement has {len(placement['cells'])} cells but the drum now has "
            f"{len(draft['frames'])} picture frames. Fit the measured placement again."
        )
    width = placement["cell_width_mm"]
    if readings is not None:
        live = {row["slot"]: row for row in readings if not row["partial"]}
        circumference = draft["settings"]["circumference"]
        for cell in placement["cells"]:
            row = live.get(cell["slot"])
            if row is None:
                raise Invalid(f"Slot {cell['slot']} no longer reads as a complete opening. Fit the placement again.")
            opening = fitting.SlotProjection(row, circumference)
            center = (cell["start_mm"] + cell["end_mm"]) / 2
            if not opening.projected_start - 1e-4 <= center <= opening.projected_end + 1e-4:
                raise Invalid(
                    f"The saved cell for slot {cell['slot']} is no longer centered in that opening under the "
                    "current scan brightness. Fit the placement again."
                )


def verify(draft, library, placement):
    _, readings = scan_readings(draft, library)
    check(draft, placement, readings)
    return placement


def stale(draft, placement):
    """Whether a retained placement still matches the drum and photograph."""
    if not isinstance(placement, dict) or placement.get("kind") != KIND:
        return True
    sample = draft.get("scan")
    return (
        placement.get("settings_digest") != settings_digest(draft["settings"])
        or not sample
        or sample["blob"] != placement.get("scan_blob")
        or len(placement.get("cells", [])) != len(draft["frames"])
    )


def track(draft, placement):
    return fitting.build_track(len(draft["frames"]), placement, draft["settings"])


def active_track(draft, library):
    """The measured print track, or None when the drum still uses its nominal band."""
    placement = draft.get("layout")
    if placement is None:
        layout.uniform_track(len(draft["frames"]), draft["settings"])
        return None
    verify(draft, library, placement)
    return track(draft, placement)


def rows(placement):
    return [
        {"cell": index, "start_mm": round(cell["start_mm"], 4), "end_mm": round(cell["end_mm"], 4),
         "center_mm": round((cell["start_mm"] + cell["end_mm"]) / 2, 4),
         "assigned_slit": cell["slot"], "photo_slit_center_mm": round(cell["photo_center_mm"], 4),
         "projected_center_mm": round(cell["projected_center_mm"], 4),
         "projected_span_mm": "–".join(f"{value:.2f}" for value in cell["projected_span_mm"]),
         "center_offset_mm": round((cell["start_mm"] + cell["end_mm"]) / 2 - cell["projected_center_mm"], 4),
         "gap_after_mm": round(cell["gap_after_mm"], 4)}
        for index, cell in enumerate(placement["cells"], 1)
    ]
