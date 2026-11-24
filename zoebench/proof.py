"""Render proofs from a single draft revision, retaining image and measured evidence."""

from csv import DictWriter
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image, ImageDraw

from . import layout, optics, raster, scan
from .model import PROOF_MODES
from .store import json_bytes


def selected_frame(settings, track):
    return min(settings["proof_frame"], len(track.cells) - 1)


def screen_size(track, cell, max_edge=520):
    scale = max_edge / max(cell.width, track.height)
    return max(1, round(cell.width * scale)), max(1, round(track.height * scale))


def slot_panel(source, draft, track, index, hatch=True):
    """An RGBA screen-size slot, hatched when the old frame was not captured."""
    frame, cell = draft["frames"][index], track.cells[index]
    size = screen_size(track, cell)
    if frame.get("missing"):
        if not hatch:
            return Image.new("RGBA", size, (0, 0, 0, 0))
        return layout.empty_panel(size, slot_number(frame, index), draft["settings"]["stock"])
    return layout.paint_cell(source(frame), frame, track, cell, *size)[0]


def slot_number(frame, index):
    return (frame.get("band") or {}).get("slot", index + 1)


def screen_frame(source, draft, track, index):
    return slot_panel(source, draft, track, index, hatch=False)


def styled_frame(source, draft, track, index):
    frame = draft["frames"][index]
    if frame.get("missing"):
        return layout.empty_panel(screen_size(track, track.cells[index]), slot_number(frame, index),
                                  draft["settings"]["stock"]).convert("RGB")
    return raster.on_stock(screen_frame(source, draft, track, index), draft["settings"]["stock"])


def _print_summary(track):
    return {
        "frames": len(track.cells), "wrap_mm": track.circumference, "height_mm": track.height,
        "join_tab_mm": track.join_mm, "cell_pitch_mm": round(track.pitch, 4), "print_dpi": track.dpi,
    }


def _ribbon_rows(track, frames):
    return [
        {"frame": slot_number(frame, cell.number), "artwork": frame["title"],
         "status": "missing" if frame.get("missing") else "captured",
         "left_mm": round(cell.start, 4),
         "right_mm": round(cell.end, 4), "center_mm": round(cell.center, 4), "device_start_x": track.x(cell.start),
         "device_end_x": track.x(cell.end)}
        for cell, frame in zip(track.cells, frames, strict=True)
    ]


def ribbon_proof(draft, track, source):
    image = layout.picture_ribbon(track, draft["frames"], source, draft["settings"]["stock"])
    summary = _print_summary(track)
    summary["missing_frames"] = _missing_frames(draft, track)
    return image, summary, _ribbon_rows(track, draft["frames"]), track.dpi


def _missing_frames(draft, track):
    return [slot_number(frame, cell.number)
            for cell, frame in zip(track.cells, draft["frames"], strict=True) if frame.get("missing")]


def cut_guide(draft, track, source):
    image = layout.cut_guide(track, draft["frames"])
    summary = _print_summary(track)
    summary["missing_frames"] = _missing_frames(draft, track)
    return image, summary, _ribbon_rows(track, draft["frames"]), track.dpi


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
    images = [slot_panel(source, draft, track, num, hatch=False) for num in indexes]
    image, fractions = optics.onion_image(*images)
    summary = {"current": index + 1, "previous": indexes[0] + 1, "next": indexes[2] + 1,
               "missing_in_overlay": [num + 1 for num in indexes if draft["frames"][num].get("missing")],
               **fractions}
    return image, summary, [], None


def coverage(draft, track, source):
    index = selected_frame(draft["settings"], track)
    rows = []
    overlay = None
    for frame, cell in zip(draft["frames"], track.cells, strict=True):
        size = screen_size(track, cell)
        if frame.get("missing"):
            rows.append({"frame": slot_number(frame, cell.number), "status": "missing",
                         "ink_fraction": 0, "cut_fraction": 0, "quiet_fraction": 0})
            if cell.number == index:
                overlay = layout.empty_panel(size, slot_number(frame, cell.number),
                                             draft["settings"]["stock"]).convert("RGB")
            continue
        visible, aa, offset = layout.paint_cell(source(frame), frame, track, cell, *size)
        margin = draft["settings"]["action_margin"]
        margin_x = round(margin / cell.width * size[0])
        margin_y = round(margin / track.height * size[1])
        visible_stats = raster.mask_visibility(aa, offset, size, (margin_x, margin_y))
        rows.append({"frame": cell.number + 1, "status": "captured", **visible_stats})
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
        "missing_frames": [row["frame"] for row in rows if row["status"] == "missing"],
    }
    return overlay, summary, rows, None


def motion(draft, track, source):
    index = selected_frame(draft["settings"], track)
    rendered = [screen_frame(source, draft, track, cell.number) for cell in track.cells]
    centers = [optics.sample_centroid(image) for image in rendered]
    base = styled_frame(source, draft, track, index)
    overlay = optics.motion_overlay(base, centers)
    rows = []
    for center, image, cell, frame in zip(centers, rendered, track.cells, draft["frames"], strict=True):
        coords = (round(center[0] / image.width * cell.width, 4), round(center[1] / image.height * track.height, 4)) if center else (None, None)
        rows.append({"frame": cell.number + 1, "status": "missing" if frame.get("missing") else "captured",
                     "ink_center_x_mm": coords[0], "ink_center_y_mm": coords[1]})
    visible = [row for row in rows if row["ink_center_x_mm"] is not None]
    spread = max((row["ink_center_x_mm"] for row in visible), default=0) - min(
        (row["ink_center_x_mm"] for row in visible), default=0,
    )
    return overlay, {"frames_observed": len(visible), "horizontal_travel_mm": round(spread, 4),
                     "missing_frames": [row["frame"] for row in rows if row["status"] == "missing"]}, rows, None


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
                if frame.get("missing") or not frame.get("blob"):
                    continue
                zipfile.writestr(f"frames/{index:02}-{frame['filename']}", library.blob(frame["blob"]).read_bytes())
            contact_sheets = {frame["contact"]["sheet"]: frame["contact"]["filename"]
                              for frame in detail["frames"] if "contact" in frame}
            for index, (digest, filename) in enumerate(contact_sheets.items(), 1):
                zipfile.writestr(f"contacts/{index:02}-{filename}", library.blob(digest).read_bytes())
            sources = {frame["band"]["source_blob"]: frame["band"]["photo"]
                       for frame in detail["frames"]
                       if isinstance(frame.get("band"), dict) and frame["band"].get("source_blob")}
            for index, (digest, filename) in enumerate(sources.items(), 1):
                zipfile.writestr(f"band-photos/{index:02}-{filename}", library.blob(digest).read_bytes())
    return buffer.getvalue()
