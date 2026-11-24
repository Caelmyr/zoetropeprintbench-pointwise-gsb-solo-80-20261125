"""Render proofs from a single draft revision, retaining image and measured evidence."""

from csv import DictWriter
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import layout, optics, raster, scan
from .model import PROOF_MODES
from .store import json_bytes

UNCOVERED_BLUE = (38, 104, 178)
CUT_RED = (201, 58, 35)
LEGEND_BG = (252, 249, 238)
FRINGE_BG = (226, 222, 205)


def load_font(size=14):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def selected_frame(settings, track):
    return min(settings["proof_frame"], len(track.cells) - 1)


def screen_size(track, cell, max_edge=520):
    return layout.screen_size(track, cell, max_edge)


def screen_frame(source, draft, track, index, calibration=True):
    frame, cell = draft["frames"][index], track.cells[index]
    return layout.paint_cell(source(frame), frame, track, cell, *screen_size(track, cell),
                             calibration=calibration)[0]


def styled_frame(source, draft, track, index, calibration=True):
    return raster.on_stock(screen_frame(source, draft, track, index, calibration),
                           draft["settings"]["stock"])


def _hatch_patch(width, height, color, spacing=10):
    patch = Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(patch)
    for x in range(-height, width + height, spacing):
        draw.line((x, height, x + height, 0), fill=color, width=1)
    return patch


def clipping_map(visible, placed, offset, size, margin_xy, stock, stats):
    """Cell map with blue 'not photographed' edges and red 'ink cut off' ink."""
    width, height = size
    pad = 30
    legend_h = 78
    map_w, map_h = width + pad * 2, height + pad * 2 + legend_h
    base = Image.new("RGB", (map_w, map_h), FRINGE_BG)
    cell_origin = (pad, pad)

    paper_cell = Image.new("RGBA", (width, height), (*raster.PAPER[stock], 255))
    paper_cell.alpha_composite(visible)
    base.paste(paper_cell.convert("RGB"), cell_origin)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    ox, oy = offset
    px_w, px_h = placed.size

    # Blue: portions of the print cell the source negative rectangle never reaches.
    blue_fill = (*UNCOVERED_BLUE, 46)
    blue_hatch = (*UNCOVERED_BLUE, 150)
    uncovered_rects = []
    if ox > 0:
        uncovered_rects.append((0, 0, min(ox, width), height))
    if ox + px_w < width:
        uncovered_rects.append((max(0, ox + px_w), 0, width, height))
    if oy > 0:
        uncovered_rects.append((0, 0, width, min(oy, height)))
    if oy + px_h < height:
        uncovered_rects.append((0, max(0, oy + px_h), width, height))
    for rect in uncovered_rects:
        x0, y0 = rect[0] + pad, rect[1] + pad
        x1, y1 = rect[2] + pad, rect[3] + pad
        rw, rh = max(0, x1 - x0), max(0, y1 - y0)
        if rw <= 0 or rh <= 0:
            continue
        patch = Image.new("RGBA", (rw, rh), blue_fill)
        patch.alpha_composite(_hatch_patch(rw, rh, blue_hatch))
        overlay.alpha_composite(patch, (x0, y0))

    # Red: real artwork/pigment that falls outside the print-cell boundary.
    weights = raster.artwork_weights(placed)
    cut_alpha = np.zeros((map_h, map_w), dtype=np.uint8)
    ys, xs = np.nonzero(weights > .06)
    if len(xs):
        canvas_x = xs + ox
        canvas_y = ys + oy
        outside = (canvas_x < 0) | (canvas_x >= width) | (canvas_y < 0) | (canvas_y >= height)
        if outside.any():
            tx = canvas_x[outside] + pad
            ty = canvas_y[outside] + pad
            weight = (np.clip(weights[ys[outside], xs[outside]], 0, 1) * 255).astype(np.uint8)
            valid = (tx >= 0) & (tx < map_w) & (ty >= 0) & (ty < map_h)
            cut_alpha[ty[valid], tx[valid]] = np.maximum(weight[valid], 150)
    if cut_alpha.any():
        cut_rgba = np.zeros((map_h, map_w, 4), dtype=np.uint8)
        cut_rgba[..., 0] = CUT_RED[0]
        cut_rgba[..., 1] = CUT_RED[1]
        cut_rgba[..., 2] = CUT_RED[2]
        cut_rgba[..., 3] = cut_alpha
        red_layer = Image.fromarray(cut_rgba, "RGBA").filter(ImageFilter.MaxFilter(3))
        base = Image.alpha_composite(base.convert("RGBA"), red_layer)

    # Thick inner edge bars mark which print edges are cutting real artwork.
    bar = 6
    edge_alpha = 185
    edge_keys = (
        ("ink_cut_left_fraction", (pad, pad, pad + bar, pad + height)),
        ("ink_cut_right_fraction", (pad + width - bar, pad, pad + width, pad + height)),
        ("ink_cut_top_fraction", (pad, pad, pad + width, pad + bar)),
        ("ink_cut_bottom_fraction", (pad, pad + height - bar, pad + width, pad + height)),
    )
    for key, rect in edge_keys:
        if stats.get(key, 0) > .002:
            od.rectangle(rect, fill=(*CUT_RED, edge_alpha))

    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    # Print-cell boundary and quiet-margin rectangle.
    draw.rectangle((pad, pad, pad + width - 1, pad + height - 1), outline=(35, 44, 40), width=2)
    margin_x, margin_y = margin_xy
    draw.rectangle((pad + margin_x, pad + margin_y, pad + width - margin_x - 1,
                    pad + height - margin_y - 1), outline=layout.ORANGE, width=2)

    # Legend.
    small = load_font(13)
    draw.rectangle((0, map_h - legend_h, map_w, map_h), fill=LEGEND_BG)
    items = [
        (UNCOVERED_BLUE, "Not in original photo (uncovered edge)"),
        (CUT_RED, "Artwork cut by print-cell edge"),
        (layout.ORANGE, "Quiet margin"),
    ]
    y = map_h - legend_h + 9
    for color, text in items:
        draw.rectangle((pad, y, pad + 15, y + 15), fill=color)
        draw.text((pad + 22, y), text, fill=(40, 48, 44), font=small)
        y += 22
    return base


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
    return layout.cut_guide(track), _print_summary(track), _ribbon_rows(track, draft["frames"]), track.dpi


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
        visible, placed, offset = layout.paint_cell(source(frame), frame, track, cell, *size)
        margin = draft["settings"]["action_margin"]
        margin_x = round(margin / cell.width * size[0])
        margin_y = round(margin / track.height * size[1])
        stats = raster.mask_visibility(placed, offset, size, (margin_x, margin_y))
        px_per_mm_x = size[0] / cell.width
        px_per_mm_y = size[1] / track.height
        row = {
            "frame": cell.number + 1,
            "ink_fraction": stats["ink_fraction"],
            "cut_fraction": stats["cut_fraction"],
            "ink_cut_fraction": stats["ink_cut_fraction"],
            "quiet_fraction": stats["quiet_fraction"],
            "uncovered_fraction": stats["uncovered_fraction"],
            "unphotographed_fraction": stats["uncovered_fraction"],
            "missing_edge_fraction": stats["uncovered_fraction"],
            "uncovered_left_mm": round(stats["uncovered_left_px"] / px_per_mm_x, 2),
            "uncovered_right_mm": round(stats["uncovered_right_px"] / px_per_mm_x, 2),
            "uncovered_top_mm": round(stats["uncovered_top_px"] / px_per_mm_y, 2),
            "uncovered_bottom_mm": round(stats["uncovered_bottom_px"] / px_per_mm_y, 2),
            "cut_left_fraction": stats["cut_left_fraction"],
            "cut_right_fraction": stats["cut_right_fraction"],
            "cut_top_fraction": stats["cut_top_fraction"],
            "cut_bottom_fraction": stats["cut_bottom_fraction"],
            "ink_cut_left_fraction": stats["ink_cut_left_fraction"],
            "ink_cut_right_fraction": stats["ink_cut_right_fraction"],
            "ink_cut_top_fraction": stats["ink_cut_top_fraction"],
            "ink_cut_bottom_fraction": stats["ink_cut_bottom_fraction"],
        }
        rows.append(row)
        if cell.number == index:
            overlay = clipping_map(
                visible, placed, offset, size, (margin_x, margin_y),
                draft["settings"]["stock"], stats,
            )
    summary = {
        "frames_with_cut_ink": sum(row["cut_fraction"] > .001 for row in rows),
        "frames_with_ink_in_margin": sum(row["quiet_fraction"] > .001 for row in rows),
        "frames_with_uncovered_edges": sum(row["uncovered_fraction"] > .002 for row in rows),
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
