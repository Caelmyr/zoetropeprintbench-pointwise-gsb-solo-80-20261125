"""Render proofs from a single draft revision, retaining image and measured evidence."""

from csv import DictWriter
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image, ImageDraw

from . import layout, optics, raster, stencil, stencil_view, scan
from .model import PROOF_MODES
from .store import json_bytes


def selected_frame(settings, track):
    return min(settings["proof_frame"], len(track.cells) - 1)


def screen_size(track, cell, max_edge=520):
    scale = max_edge / max(cell.width, track.height)
    return max(1, round(cell.width * scale)), max(1, round(track.height * scale))


def screen_frame(source, draft, track, index):
    frame, cell = draft["frames"][index], track.cells[index]
    return layout.paint_cell(source(frame), frame, track, cell, *screen_size(track, cell))[0]


def styled_frame(source, draft, track, index):
    return raster.on_stock(screen_frame(source, draft, track, index), draft["settings"]["stock"])


def _print_summary(track):
    return {
        "frames": len(track.cells), "wrap_mm": track.circumference, "height_mm": track.height,
        "join_tab_mm": track.join_mm, "cell_pitch_mm": round(track.pitch, 4), "print_dpi": track.dpi,
    }


def _ribbon_rows(track, frames):
    return [
        {"frame": cell.number + 1, "artwork": frame["title"], "left_mm": round(cell.start, 4),
         "right_mm": round(cell.end, 4), "center_mm": round(cell.center, 4), "device_start_x": track.x(cell.start),
         "device_end_x": track.x(cell.end)}
        for cell, frame in zip(track.cells, frames, strict=True)
    ]


def ribbon_proof(draft, track, source):
    image = layout.picture_ribbon(track, draft["frames"], source, draft["settings"]["stock"])
    return image, _print_summary(track), _ribbon_rows(track, draft["frames"]), track.dpi


def cut_guide(draft, track, source):
    base = layout.cut_guide(track)
    summary = _print_summary(track)
    rows = _ribbon_rows(track, draft["frames"])
    record = draft.get("stencil")
    if record:
        sheet, _ink, _mask, _record, _frame = _adopted_sheet(draft, track, source)
        summary.update(_adopted_summary(record))
        summary["cut_guide_pages"] = "band guide + adopted stencil sheet"
    elif _stencil_body(draft) and any(
            key in _stencil_body(draft) for key in
            ("bridge_mm", "min_bridge_mm", "bridge_width_mm", "marks", "protect", "stencil")):
        plan, _canvas, ink, _frame = _requested_plan(draft, track, source)
        sheet = stencil_view.render_cut_sheet(
            ink, plan["bridge_mask"], plan, track.pixels_per_mm,
            title=f"PAPER STENCIL - FRAME {plan['frame'] + 1:02}",
            subtitle="Cut-guide stencil preview - adopt to retain this edition")
        summary.update({
            "stencil_included": True, "stencil_edition": "preview",
            "stencil_ties": plan["bridge_count"], "stencil_islands": plan["island_count"],
            "stencil_all_connected": plan["all_connected"],
        })
    else:
        return base, summary, rows, track.dpi
    gap = track.x(8)
    combined = Image.new("RGB", (max(base.width, sheet.width), base.height + gap + sheet.height), "white")
    combined.paste(base, (0, 0))
    combined.paste(sheet, (0, base.height + gap))
    return combined, summary, rows, track.dpi


def _stencil_body(draft):
    request = draft.get("_stencil_request")
    return request if isinstance(request, dict) else {}


def _requested_plan(draft, track, source):
    """Plan from proof-request/body or panel values; never needs the library."""
    body = _stencil_body(draft)
    settings = draft["settings"]
    frame_value = body.get("frame", body.get("frame_index", settings.get("proof_frame", 0)))
    try:
        index = int(frame_value)
    except (TypeError, ValueError):
        index = settings.get("proof_frame", 0)
    if "frame_id" in body:
        index, frame = stencil.resolve_frame(draft, frame_id=body["frame_id"])
    else:
        index, frame = stencil.resolve_frame(draft, index)
    image = source(frame)
    bridge_mm = body.get("bridge_mm", body.get("min_bridge_mm",
                         body.get("bridge_width_mm", settings.get("bridge_mm", 2.0))))
    marks = body.get("marks", body.get("protect", body.get("protected_marks")))
    plan, canvas, ink = stencil.plan_for_draft(
        draft, track, None, frame=index, bridge_mm=bridge_mm, marks=marks,
        protect_radius_mm=body.get("protect_radius_mm"),
        ink_threshold=float(body.get("ink_threshold", 0.5)),
        source=image)
    return plan, canvas, ink, frame


def _bridge_rows(bridges):
    return [
        {"tie": b["index"] + 1, "width_mm": b["width_mm"], "length_mm": b["length_mm"],
         "blocked_ink_mm2": b["blocked_ink_mm2"], "angle_deg": b["angle_deg"]}
        | ({"protected_clash": b.get("protected_hits", 0) > 0})
        for b in bridges
    ]


def _adopted_summary(record):
    return {
        "stencil_frame": record["frame"] + 1,
        "stencil_ties": len(record.get("bridges", [])),
        "stencil_islands": record.get("island_count", 0),
        "stencil_tie_width_mm": record.get("bridge_mm"),
        "stencil_blocked_ink_mm2": record.get("blocked_ink_mm2"),
        "stencil_all_connected": record.get("all_connected"),
    }


def _adopted_sheet(draft, track, source):
    record = draft["stencil"]
    index, frame = stencil.resolve_frame(draft, record.get("frame"), record.get("frame_id"))
    image = source(frame)
    ppm = track.pixels_per_mm
    cell_w, cell_h = record["cell_width_mm"], record["cell_height_mm"]
    width_px, height_px = track.x(cell_w), track.x(cell_h)
    transform = record["placement"]["transform"]
    canvas, _placed, _offset = raster.place(image, transform, (width_px, height_px),
                                            (cell_w, cell_h), None)
    ink_mode = stencil.resolve_ink_mode(image, record.get("ink_mode", "auto"))
    ink = stencil.ink_mask(canvas, record.get("ink_threshold", 0.5), ink_mode)
    mask = stencil.bridge_mask_from_record(record, ppm, ink.shape)
    title = f"PAPER STENCIL - FRAME {index + 1:02}"
    subtitle = f"{record.get('frame_title', '')[:48]} · adopted rev {record.get('adopted_revision')}"
    sheet = stencil_view.render_cut_sheet(
        ink, mask, record, ppm, title=title, subtitle=subtitle,
        protected=record.get("protected_lines"))
    return sheet, ink, mask, record, frame


def stencil_cut(draft, track, source):
    body = _stencil_body(draft)
    record = draft.get("stencil")
    explicit = any(key in body for key in
                   ("bridge_mm", "min_bridge_mm", "bridge_width_mm", "marks", "protect"))
    if record and not explicit and not body.get("replanned"):
        sheet, _ink, _mask, record, _frame = _adopted_sheet(draft, track, source)
        summary = {**_print_summary(track), **_adopted_summary(record), "stencil_edition": "adopted"}
        return sheet, summary, _bridge_rows(record.get("bridges", [])), track.dpi

    plan, canvas, ink, _frame = _requested_plan(draft, track, source)
    sheet = stencil_view.render_cut_sheet(
        ink, plan["bridge_mask"], plan, track.pixels_per_mm,
        title=f"PAPER STENCIL - FRAME {plan['frame'] + 1:02}",
        subtitle="Preview tie layout - adopt this edition to keep it")
    summary = {
        **_print_summary(track),
        "stencil_edition": "preview",
        "stencil_ties": plan["bridge_count"],
        "stencil_islands": plan["island_count"],
        "stencil_tie_width_mm": plan["actual_width_mm"],
        "stencil_blocked_ink_mm2": plan["blocked_ink_mm2"],
        "stencil_blocked_fraction": plan["blocked_fraction"],
        "stencil_all_connected": plan["all_connected"],
    }
    return sheet, summary, _bridge_rows(plan["bridges"]), track.dpi


def stencil_brush(draft, track, source):
    body = _stencil_body(draft)
    record = draft.get("stencil")
    if record and not any(key in body for key in ("bridge_mm", "marks", "protect")):
        index, frame = stencil.resolve_frame(draft, record.get("frame"), record.get("frame_id"))
        ppm = track.pixels_per_mm
        cell = track.cells[index]
        image = source(frame)
        width_px, height_px = track.x(cell.width), track.x(track.height)
        canvas, _ = stencil.canvas_for_image(
            image, frame["transform"], width_px, height_px, cell.width, track.height)
        ink_mode = stencil.resolve_ink_mode(image, record.get("ink_mode", "auto"))
        ink = stencil.ink_mask(canvas, record.get("ink_threshold", 0.5), ink_mode)
        mask = stencil.bridge_mask_from_record(record, ppm, ink.shape)
        compat = stencil.compatibility(record, frame, track, cell)
        bridges = record.get("bridges", [])
    else:
        plan, _canvas, ink, frame = _requested_plan(draft, track, source)
        index = plan["frame"]
        cell = track.cells[index]
        mask = plan["bridge_mask"]
        compat = None
        bridges = plan["bridges"]

    stock = raster.PAPER[draft["settings"]["stock"]]
    ribbon = layout.picture_ribbon(track, draft["frames"], source, draft["settings"]["stock"])
    paint = body.get("paint")
    paint_rgb = stencil_view.st.PAINT_DEFAULT
    if isinstance(paint, str) and paint.startswith("#") and len(paint) == 7:
        try:
            paint_rgb = tuple(int(paint[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            paint_rgb = stencil_view.st.PAINT_DEFAULT
    image, areas = stencil_view.render_brush_proof(
        ribbon, cell, track, ink, mask, stock=stock, paint=paint_rgb)
    summary = {
        "frame": index + 1, **areas,
        "ties": len(bridges),
        "tie_blocked_ink_mm2": areas["bridge_blocked_ink_mm2"],
    }
    if compat:
        summary.update({
            "stencil_matches": compat["matches"],
            "stencil_reusable": compat["reusable"],
            "stencil_method": compat["method"],
            "stencil_message": compat["message"],
        })
    rows = _bridge_rows(bridges)
    return image, summary, rows, None


def slot_ruler(draft, track, source):
    settings = draft["settings"]
    image, slots = layout.slot_ruler(track, settings["slot_width"], settings["slot_phase"], settings["direction"])
    rows = [
        {"opposite_frame": index + 1, "slot_center_mm": round(center, 4), "slit_mm": width}
        for index, (center, width) in enumerate(slots)
    ]
    summary = {**_print_summary(track), "slot_width_mm": settings["slot_width"], "phase_mm": settings["slot_phase"]}
    return image, summary, rows, track.dpi


def direct_loop(draft, track, source):
    index = selected_frame(draft["settings"], track)
    return styled_frame(source, draft, track, index), {"frame": index + 1, "frames": len(track.cells)}, [], None


def optical_loop(draft, track, source):
    index = selected_frame(draft["settings"], track)
    band = layout.picture_ribbon(track, draft["frames"], source, draft["settings"]["stock"])
    image, result, rows = optics.slit_view(band, track, draft["settings"], index)
    return image, result, rows, None


def neighbors(draft, track, source):
    index = selected_frame(draft["settings"], track)
    count = len(track.cells)
    indexes = [(index - 1) % count, index, (index + 1) % count]
    images = [screen_frame(source, draft, track, num) for num in indexes]
    image, fractions = optics.onion_image(*images)
    summary = {"current": index + 1, "previous": indexes[0] + 1, "next": indexes[2] + 1, **fractions}
    return image, summary, [], None


def coverage(draft, track, source):
    index = selected_frame(draft["settings"], track)
    rows = []
    overlay = None
    for frame, cell in zip(draft["frames"], track.cells, strict=True):
        size = screen_size(track, cell)
        visible, aa, offset = layout.paint_cell(source(frame), frame, track, cell, *size)
        margin = draft["settings"]["action_margin"]
        margin_x = round(margin / cell.width * size[0])
        margin_y = round(margin / track.height * size[1])
        visible_stats = raster.mask_visibility(aa, offset, size, (margin_x, margin_y))
        rows.append({"frame": cell.number + 1, **visible_stats})
        if cell.number == index:
            overlay = raster.on_stock(visible, draft["settings"]["stock"])
            ImageDraw.Draw(overlay).rectangle(
                (margin_x, margin_y, size[0] - margin_x - 1, size[1] - margin_y - 1),
                outline=layout.ORANGE, width=2,
            )
    summary = {
        "frames_with_cut_ink": sum(row["cut_fraction"] > .001 for row in rows),
        "frames_with_ink_in_margin": sum(row["quiet_fraction"] > .001 for row in rows),
        "quiet_margin_mm": draft["settings"]["action_margin"],
        "selected_frame": index + 1,
    }
    return overlay, summary, rows, None


def motion(draft, track, source):
    index = selected_frame(draft["settings"], track)
    rendered = [screen_frame(source, draft, track, cell.number) for cell in track.cells]
    centers = [optics.sample_centroid(image) for image in rendered]
    overlay = optics.motion_overlay(raster.on_stock(rendered[index], draft["settings"]["stock"]), centers)
    rows = []
    for center, image, cell in zip(centers, rendered, track.cells, strict=True):
        coords = (round(center[0] / image.width * cell.width, 4), round(center[1] / image.height * track.height, 4)) if center else (None, None)
        rows.append({"frame": cell.number + 1, "ink_center_x_mm": coords[0], "ink_center_y_mm": coords[1]})
    visible = [row for row in rows if row["ink_center_x_mm"] is not None]
    spread = max((row["ink_center_x_mm"] for row in visible), default=0) - min(
        (row["ink_center_x_mm"] for row in visible), default=0,
    )
    return overlay, {"frames_observed": len(visible), "horizontal_travel_mm": round(spread, 4)}, rows, None


def contrast(draft, track, source):
    index = selected_frame(draft["settings"], track)
    image = styled_frame(source, draft, track, index)
    exposure = raster.luminance(image)
    quantiles = np.quantile(exposure, [.05, .5, .95])
    histogram, _ = np.histogram(exposure, bins=10, range=(0, 1))
    summary = {
        "linear_p05": round(float(quantiles[0]), 4), "linear_median": round(float(quantiles[1]), 4),
        "linear_p95": round(float(quantiles[2]), 4), "linear_range": round(float(quantiles[2] - quantiles[0]), 4),
    }
    canvas = np.asarray(image).copy()
    canvas[exposure < .06] = [53, 97, 153]
    canvas[exposure > .9] = [237, 182, 66]
    rows = [{"linear_min": i / 10, "linear_max": (i + 1) / 10, "pixels": int(count)}
            for i, count in enumerate(histogram)]
    return Image.fromarray(canvas), summary, rows, None


def scale_target(draft, track, source):
    return layout.scale_ruler(track), {"target_length_mm": 100, "print_dpi": track.dpi}, [], track.dpi


def assembly(draft, track, source):
    image = layout.picture_ribbon(track, draft["frames"], source, draft["settings"]["stock"])
    active_right = track.x(track.circumference)
    edges = np.abs(raster.luminance(image.crop((0, 0, 1, image.height))) -
                   raster.luminance(image.crop((active_right - 1, 0, active_right, image.height))))
    summary = {
        "join_at_mm": track.circumference, "join_tab_mm": track.join_mm,
        "mean_edge_luminance_difference": round(float(edges.mean()), 4),
    }
    return layout.join_preview(image, track), summary, [], None


RENDERERS = {
    "ribbon": ribbon_proof, "cut-guide": cut_guide, "slot-ruler": slot_ruler, "direct-loop": direct_loop,
    "optical-loop": optical_loop, "neighbors": neighbors, "coverage": coverage, "motion": motion,
    "contrast": contrast, "scale": scale_target, "assembly": assembly,
    "stencil": stencil_cut, "stencil-cut": stencil_cut, "stencil-guide": stencil_cut,
    "stencil_bridges": stencil_cut,
    "stencil-brush": stencil_brush, "stencil-proof": stencil_brush, "stencil_brush": stencil_brush,
}


def render(mode, draft, library):
    if mode == "scan":
        sample = draft["scan"]
        picture = raster.decode(library.blob(sample["blob"]).read_bytes())
        return *scan.extract(picture, sample["registration"], draft["settings"]["threshold"]), None
    track = layout.uniform_track(len(draft["frames"]), draft["settings"])

    def source(frame):
        return raster.decode(library.blob(frame["blob"]).read_bytes())

    return RENDERERS[mode](draft, track, source)


def header(mode, draft, summary, rows, size, dpi):
    return {
        "mode": mode, "title": PROOF_MODES[mode], "summary": summary, "rows": rows,
        "source_settings": draft["settings"], "frames": draft["frames"], "scan": draft["scan"] if mode == "scan" else None,
        "size": list(size), "dpi": dpi, "project_title": draft["title"], "synthetic": draft["synthetic"],
    }


def rows_csv(rows):
    output = StringIO(newline="")
    if rows:
        writer = DictWriter(output, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output.getvalue().encode()


def packet(library, detail):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zipfile:
        zipfile.writestr("proof.png", library.blob(detail["image"]).read_bytes())
        zipfile.writestr("proof.json", json_bytes(detail))
        zipfile.writestr("readings.csv", rows_csv(detail["rows"]))
        if detail["scan"]:
            sample = detail["scan"]
            zipfile.writestr("scan/" + sample["filename"], library.blob(sample["blob"]).read_bytes())
        elif detail["frames"]:
            for index, frame in enumerate(detail["frames"], 1):
                zipfile.writestr(f"frames/{index:02}-{frame['filename']}", library.blob(frame["blob"]).read_bytes())
            contact_sheets = {frame["contact"]["sheet"]: frame["contact"]["filename"]
                              for frame in detail["frames"] if "contact" in frame}
            for index, (digest, filename) in enumerate(contact_sheets.items(), 1):
                zipfile.writestr(f"contacts/{index:02}-{filename}", library.blob(digest).read_bytes())
    return buffer.getvalue()
