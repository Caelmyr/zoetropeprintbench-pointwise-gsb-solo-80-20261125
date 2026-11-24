"""Recommend a print placement from measured, unevenly spaced viewing slots.

The photograph is measured in the flat strip's own coordinates. A slot at
``center_mm`` on the strip shows the artwork half a turn away on the assembled
drum, so its projection onto the ribbon is ``(center_mm + circumference / 2)``
modulo the circumference. Picture cells are laid out in the original picture
order starting at the join, each with the same width that the user entered.
Every cell center must fall inside the projection of one measured opening; the
cells may not overlap and the first and last cell must stay clear of the join.

No nominal fallback is substituted for a photograph that is incomplete or for
openings that cannot hold the cells: those are reported as blocked with the
opening or spacing that prevents the fit.
"""

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, minimize

from image_lab.validation import Invalid

FIT_MARGIN_MM = 0.05
GAP_WEIGHT = 1.0
MIN_CELL_MM = 8.0


class _Blocked(Exception):
    def __init__(self, kind, opening=None, reach=0.0, limit=0.0):
        super().__init__(kind)
        self.kind = kind
        self.opening = opening
        self.reach = reach
        self.limit = limit


class SlotProjection:
    """One measured opening projected onto the printed ribbon."""

    def __init__(self, reading, circumference):
        self.slot = reading["slot"]
        self.photo_center = float(reading["center_mm"])
        self.photo_start = float(reading["start_mm"])
        self.photo_end = float(reading["end_mm"])
        self.width = float(reading["width_mm"])
        self.partial = bool(reading["partial"])
        self.projected_center = (self.photo_center + circumference / 2) % circumference
        self.projected_start = (self.projected_center - self.width / 2) % circumference
        self.projected_end = (self.projected_center + self.width / 2) % circumference

    @property
    def crosses_join(self):
        return self.projected_center - self.width / 2 < 0 < self.projected_center + self.width / 2

    def window(self, circumference, cell_width, margin=FIT_MARGIN_MM):
        """Centers inside this opening that keep the whole cell off the join."""
        low = max(self.projected_center - self.width / 2 + margin, cell_width / 2)
        high = min(self.projected_center + self.width / 2 - margin, circumference - cell_width / 2)
        return low, high


def projections(readings, circumference):
    openings = [SlotProjection(row, circumference) for row in readings]
    return sorted((row for row in openings if not row.partial), key=lambda row: row.projected_center)


def _windows(openings, circumference, cell_width):
    windows = []
    for row in openings:
        if row.crosses_join:
            raise _Blocked("join", row)
        low, high = row.window(circumference, cell_width)
        if low > high + 1e-9:
            raise _Blocked("opening", row, low, high)
        windows.append((low, high))
    return windows


def _schedule(openings, circumference, cell_width):
    """Earliest and latest feasible center for each opening in ribbon order."""
    windows = _windows(openings, circumference, cell_width)
    low = np.array([row[0] for row in windows])
    high = np.array([row[1] for row in windows])
    count = len(openings)
    earliest = np.empty(count)
    latest = np.empty(count)
    earliest[0] = low[0]
    for index in range(1, count):
        earliest[index] = max(low[index], earliest[index - 1] + cell_width)
        if earliest[index] > high[index] + 1e-9:
            raise _Blocked("order", openings[index], earliest[index], high[index])
    latest[-1] = high[-1]
    for index in range(count - 2, -1, -1):
        latest[index] = min(high[index], latest[index + 1] - cell_width)
        if latest[index] < low[index] - 1e-9:
            raise _Blocked("order", openings[index], low[index], latest[index])
    return low, high, earliest, latest


def _fits(openings, circumference, cell_width):
    try:
        _schedule(openings, circumference, cell_width)
        return True
    except _Blocked:
        return False


def widest_cell(openings, circumference):
    """Widest equal cell that can be centered in every measured opening in order."""
    if not openings:
        return None
    ceiling = circumference / len(openings)
    if ceiling <= MIN_CELL_MM:
        return MIN_CELL_MM if _fits(openings, circumference, MIN_CELL_MM) else None
    if _fits(openings, circumference, ceiling):
        return ceiling
    if not _fits(openings, circumference, MIN_CELL_MM):
        return None
    low, high = MIN_CELL_MM, ceiling
    for _ in range(60):
        middle = (low + high) / 2
        if _fits(openings, circumference, middle):
            low = middle
        else:
            high = middle
    return low


def _solve(openings, circumference, cell_width, low, high, earliest, latest, gap_weight):
    count = len(openings)
    center_pin = np.array([row.projected_center for row in openings])
    half_width = np.maximum(np.array([row.width / 2 for row in openings]), .5)

    def gaps(cents):
        return np.append(np.diff(cents) - cell_width, circumference - cents[-1] + cents[0] - cell_width)

    def scale(cents):
        internal = gaps(cents)[:-1]
        return max(float(internal.mean()), .5) if len(internal) else .5

    def residual(cents):
        internal = gaps(cents)[:-1]
        even = 0.0
        if len(internal) > 1:
            even = np.sum(((internal - internal.mean()) / scale(cents)) ** 2)
        return np.sum(((cents - center_pin) / half_width) ** 2) + gap_weight * even

    def gradient(cents):
        result = 2 * (cents - center_pin) / half_width ** 2
        internal = gaps(cents)[:-1]
        if len(internal) > 1:
            deviation = 2 * gap_weight * (internal - internal.mean()) / scale(cents) ** 2
            result[0] -= deviation[0]
            result[-1] += deviation[-1]
            result[1:-1] += deviation[:-1] - deviation[1:]
        return result

    spacing = np.zeros((max(count - 1, 1), count))
    for index in range(count - 1):
        spacing[index, index] = -1
        spacing[index, index + 1] = 1
    if count == 1:
        spacing[0, 0] = -1
    constraints = [LinearConstraint(spacing, cell_width if count > 1 else 0, np.inf)]
    start = np.clip((earliest + latest) / 2, low, high)
    try:
        result = minimize(
            residual, start, jac=gradient, method="SLSQP", bounds=Bounds(low, high),
            constraints=constraints, options={"ftol": 1e-13, "maxiter": 2000},
        )
        cents = np.clip(result.x, low, high)
    except (ValueError, np.linalg.LinAlgError):
        cents = start
    for index in range(1, count):
        cents[index] = max(cents[index], cents[index - 1] + cell_width)
    cents = np.clip(cents, low, high)
    found = gaps(cents)
    violation = max(float((low - cents).max()), float((cents - high).max()), -float(found.min()))
    if violation > 1e-7 or not np.all(np.isfinite(cents)):
        raise _Blocked("order", openings[-1], float(low[-1]), float(high[-1]))
    return cents, found


def _blocked_detail(cell_width, error):
    row = error.opening
    if row is None:
        return {"reason": error.kind, "message": "The measured openings could not be fitted."}
    span = [round(row.projected_start, 4), round(row.projected_end, 4)]
    if error.kind == "join":
        return {
            "reason": "join", "slot": row.slot, "opening_mm": span,
            "message": (
                f"Slot {row.slot} projects to {row.projected_start:.2f}–{row.projected_end:.2f} mm, crossing the "
                "join. A cell cannot sit there without running into the join tab."
            ),
        }
    if error.kind == "opening":
        return {
            "reason": "opening", "slot": row.slot, "opening_mm": span,
            "message": (
                f"Slot {row.slot} projects to {row.projected_start:.2f}–{row.projected_end:.2f} mm, too narrow to "
                f"center a {cell_width:.2f} mm cell inside it."
            ),
        }
    return {
        "reason": "spacing", "slot": row.slot, "opening_mm": span,
        "needed_center_mm": round(float(error.reach), 4), "limit_center_mm": round(float(error.limit), 4),
        "message": (
            f"{cell_width:.2f} mm cells do not fit in picture order: by slot {row.slot} a cell center would have to "
            f"reach {float(error.reach):.2f} mm, but that opening only allows {float(error.limit):.2f} mm."
        ),
    }


def recommend(readings, circumference, cell_width, gap_weight=GAP_WEIGHT):
    """Fit one equal-width cell per complete opening, in original picture order.

    Returns a report describing the placement, or a report with ``feasible``
    false and the opening or spacing that prevents it.
    """
    if not isinstance(cell_width, (int, float)) or not np.isfinite(cell_width) or cell_width <= 0:
        raise Invalid("Enter a positive picture cell width in millimetres.")
    openings = projections(readings, circumference)
    incomplete = [row for row in readings if row["partial"]]
    report = {
        "feasible": False,
        "cell_width_mm": round(float(cell_width), 4),
        "circumference_mm": circumference,
        "measured_openings": len(readings),
        "complete_openings": len(readings) - len(incomplete),
        "incomplete_openings": len(incomplete),
        "incomplete_readings": [
            {"slot": row["slot"], "start_mm": row["start_mm"], "end_mm": row["end_mm"],
             "center_mm": row["center_mm"], "width_mm": row["width_mm"]}
            for row in incomplete
        ],
        "projections": [
            {"slot": row.slot, "photo_center_mm": round(row.photo_center, 4),
             "photo_span_mm": [round(row.photo_start, 4), round(row.photo_end, 4)],
             "projected_center_mm": round(row.projected_center, 4),
             "projected_span_mm": [round(row.projected_start, 4), round(row.projected_end, 4)],
             "width_mm": round(row.width, 4)}
            for row in openings
        ],
    }
    if not openings:
        report["widest_cell_mm"] = None
        report["blocked"] = {"reason": "no-openings", "message": "No complete opening was measured in the photograph."}
        return report
    crossing = [row for row in openings if row.crosses_join]
    if crossing:
        report["widest_cell_mm"] = None
        report["blocked"] = _blocked_detail(cell_width, _Blocked("join", crossing[0]))
        return report
    widest = widest_cell(openings, circumference)
    report["widest_cell_mm"] = None if widest is None else round(widest, 4)
    if cell_width < MIN_CELL_MM:
        report["blocked"] = {
            "reason": "cell", "message": f"A picture cell must be at least {MIN_CELL_MM:.1f} mm wide.",
        }
        return report
    if widest is None:
        report["blocked"] = {
            "reason": "spacing",
            "message": "The measured openings cannot hold one equal-width cell each in picture order, even at 8 mm.",
        }
        return report
    if cell_width > widest + 1e-9:
        try:
            _schedule(openings, circumference, cell_width)
        except _Blocked as error:
            detail = _blocked_detail(cell_width, error)
        else:
            detail = {"reason": "spacing", "message": "This width does not fit the measured openings."}
        detail["widest_cell_mm"] = round(widest, 4)
        detail["message"] += f" The widest equal cell these openings allow is {widest:.2f} mm."
        report["blocked"] = detail
        return report
    try:
        low, high, earliest, latest = _schedule(openings, circumference, cell_width)
        cents, gaps = _solve(openings, circumference, cell_width, low, high, earliest, latest, gap_weight)
    except _Blocked as error:
        detail = _blocked_detail(cell_width, error)
        detail["widest_cell_mm"] = round(widest, 4)
        report["blocked"] = detail
        return report

    pins = np.array([row.projected_center for row in openings])
    cells = []
    for index, (row, center) in enumerate(zip(openings, cents, strict=True)):
        center = float(center)
        cells.append({
            "cell": index + 1,
            "slot": row.slot,
            "start_mm": round(center - cell_width / 2, 4),
            "end_mm": round(center + cell_width / 2, 4),
            "center_mm": round(center, 4),
            "projected_center_mm": round(row.projected_center, 4),
            "projected_span_mm": [round(row.projected_start, 4), round(row.projected_end, 4)],
            "center_offset_mm": round(center - row.projected_center, 4),
            "inside_opening": bool(row.projected_start - 1e-6 <= center <= row.projected_end + 1e-6),
            "gap_after_mm": round(float(gaps[index]), 4),
            "photo_center_mm": round(row.photo_center, 4),
            "width_mm": round(row.width, 4),
        })
    internal = gaps[:-1]
    realizable = all(row["inside_opening"] for row in cells)
    report.update({
        "feasible": realizable,
        "cells": cells,
        "cell_count": len(cells),
        "mean_abs_center_offset_mm": round(float(np.mean(np.abs(cents - pins))), 4),
        "maximum_center_offset_mm": round(float(np.max(np.abs(cents - pins))), 4),
        "gap_after_join_mm": round(float(gaps[-1]), 4),
        "join_lead_gap_mm": round(float(cents[0] - cell_width / 2), 4),
        "internal_gap_min_mm": round(float(internal.min()), 4) if len(internal) else None,
        "internal_gap_max_mm": round(float(internal.max()), 4) if len(internal) else None,
        "internal_gap_range_mm": round(float(internal.max() - internal.min()), 4) if len(internal) else None,
        "total_gap_mm": round(float(gaps.sum()), 4),
        "gap_weight": gap_weight,
    })
    if not realizable:
        report["blocked"] = {
            "reason": "unrealized", "message": "The solver could not keep every cell inside its measured opening.",
        }
    return report


def widest_scan_cell(readings, circumference):
    """Widest equal cell the photograph alone allows, for guidance before adopting."""
    return widest_cell(projections(readings, circumference), circumference)


def build_track(total, placement, settings):
    """Build a print track from a recorded measured-slot placement."""
    from .layout import Cell, Track

    circumference = settings["circumference"]
    rows = placement["cells"]
    cell_width = placement["cell_width_mm"]
    if total < 2:
        raise Invalid("A picture ribbon needs at least two cells.")
    if len(rows) != total:
        raise Invalid(
            f"The measured placement has {len(rows)} cell{'s' if len(rows) != 1 else ''} but this drum has {total} "
            "picture frames. Fit and adopt the measured placement again, or revert to the nominal band."
        )
    if cell_width < MIN_CELL_MM:
        raise Invalid(f"Each picture cell must be at least {MIN_CELL_MM:.1f} mm wide.")
    if cell_width * total > circumference + 1e-9:
        raise Invalid("The picture cells are wider than the drum circumference.")
    cells, views = [], []
    for index, row in enumerate(rows):
        start = float(row["start_mm"])
        if start < -1e-6 or start + cell_width > circumference + 1e-6:
            raise Invalid("A measured cell extends outside the drum circumference or into the join tab.")
        if index and start < float(rows[index - 1]["start_mm"]) + cell_width - 1e-6:
            raise Invalid("Measured cells overlap or are out of order.")
        cells.append(Cell(index, round(start, 6), round(start + cell_width, 6), row["slot"]))
        views.append({
            "slot": row["slot"], "photo_center_mm": float(row["photo_center_mm"]),
            "width_mm": float(row["width_mm"]),
            "projected_center_mm": float(row["projected_center_mm"]),
        })
    return Track(
        tuple(cells), circumference, settings["height_mm"], settings["dpi"], settings["join_mm"], tuple(views),
    )


def summarize_fit(report):
    """Compact summary for the proof header."""
    if not report.get("feasible"):
        blocked = report.get("blocked") or {}
        return {
            "placement": "measured slots, blocked", "reason": blocked.get("reason"),
            "cell_width_mm": report.get("cell_width_mm"), "complete_openings": report.get("complete_openings"),
            "widest_cell_mm": report.get("widest_cell_mm"), "explanation": blocked.get("message"),
        }
    return {
        "placement": "measured slots", "cells": report["cell_count"], "cell_width_mm": report["cell_width_mm"],
        "complete_openings": report["complete_openings"], "incomplete_openings": report["incomplete_openings"],
        "maximum_center_offset_mm": report["maximum_center_offset_mm"],
        "internal_gap_range_mm": report["internal_gap_range_mm"],
        "gap_after_join_mm": report["gap_after_join_mm"],
    }
