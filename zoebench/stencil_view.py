"""Millimetre-accurate rendering of stencil cut sheets and brushing proofs."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import stencil as st

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size_mm, ppm, bold=False):
    size_px = max(9, int(round(size_mm * ppm)))
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size_px)
    except OSError:
        return ImageFont.load_default()


def mm(value, ppm):
    return int(round(value * ppm))


def text(draw, xy, value, ppm, size_mm=3.0, fill=st.CUT_DARK, bold=False, anchor="la", halo=True):
    face = font(size_mm, ppm, bold)
    x, y = xy
    if halo:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.text((x + dx, y + dy), value, font=face, fill=(255, 255, 255), anchor=anchor)
    draw.text((x, y), value, font=face, fill=fill, anchor=anchor)


def cross_mark(draw, x, y, ppm, radius_mm=2.2, color=st.CUT_DARK, width_mm=0.35, ring=True):
    r = mm(radius_mm, ppm)
    w = max(1, mm(width_mm, ppm))
    gap = mm(0.8, ppm)
    draw.line((x - r, y, x - gap, y), fill=color, width=w)
    draw.line((x + gap, y, x + r, y), fill=color, width=w)
    draw.line((x, y - r, x, y - gap), fill=color, width=w)
    draw.line((x, y + gap, x, y + r), fill=color, width=w)
    if ring:
        rr = mm(0.9, ppm)
        draw.ellipse((x - rr, y - rr, x + rr, y + rr), outline=color, width=w)


def dashed_rectangle(draw, box, fill, width=2, dash_mm=2.0, ppm=9.45):
    x0, y0, x1, y1 = box
    dash = max(4, mm(dash_mm, ppm))
    step = dash * 2
    for x in range(x0, x1, step):
        draw.line((x, y0, min(x + dash, x1), y0), fill=fill, width=width)
        draw.line((x, y1, min(x + dash, x1), y1), fill=fill, width=width)
    for y in range(y0, y1, step):
        draw.line((x0, y, x0, min(y + dash, y1)), fill=fill, width=width)
        draw.line((x1, y, x1, min(y + dash, y1)), fill=fill, width=width)


def bridge_mask_from_mm(bridges, ppm, shape, width_key="width_mm"):
    mask = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(mask)
    for bridge in bridges:
        points = bridge.get("points_mm") or [
            [bridge["x1_mm"], bridge["y1_mm"]], [bridge["x2_mm"], bridge["y2_mm"]]]
        coords = [(mm(x, ppm), mm(y, ppm)) for x, y in points]
        width_px = max(3, int(round(float(bridge[width_key]) * ppm)))
        if len(coords) >= 2:
            draw.line(coords, fill=255, width=width_px, joint="curve")
        r = width_px / 2 - 0.5
        for x, y in coords:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return np.asarray(mask, bool)


def _sheet_geometry(cell_w, cell_h, ppm, margin_mm, header_mm):
    m = mm(margin_mm, ppm)
    head = mm(header_mm, ppm)
    x0 = m
    y0 = head + m
    return {
        "m": m, "head": head, "x0": x0, "y0": y0,
        "x1": x0 + mm(cell_w, ppm), "y1": y0 + mm(cell_h, ppm),
        "width": mm(cell_w + 2 * margin_mm, ppm),
        "sheet_h": head + 2 * m + mm(cell_h, ppm),
    }


def render_cut_sheet(ink, bridge_mask, plan, ppm, title="Paper stencil",
                     subtitle=None, marks_mm=None, protected=None):
    """Dark areas are cut out; teal strips are the ties that must stay."""
    cell_h, cell_w = ink.shape
    cell_w_mm = cell_w / ppm
    cell_h_mm = cell_h / ppm
    margin_mm = float(plan.get("margin_mm", st.DEFAULT_MARGIN_MM))
    header_mm = 17
    jig_header_mm = 15
    jig_h_mm = cell_h_mm + 13
    scale_h_mm = 17
    canvas_w_mm = max(cell_w_mm + 2 * margin_mm, 116.0)
    geo = _sheet_geometry(cell_w_mm, cell_h_mm, ppm, margin_mm, header_mm)
    total_h = geo["sheet_h"] + mm(jig_header_mm + jig_h_mm + scale_h_mm + 4, ppm)
    image = Image.new("RGB", (mm(canvas_w_mm, ppm), total_h), "white")

    x0, y0, x1, y1 = geo["x0"], geo["y0"], geo["x1"], geo["y1"]

    # Cut openings (ink minus retained ties).
    opening = ink & ~bridge_mask
    image_arr = np.asarray(image).copy()
    image_arr[y0:y1, x0:x1][opening] = st.CUT_DARK

    # Light teal wash over the retained tie extent.
    tie_wash = np.zeros_like(image_arr)
    tie_wash[y0:y1, x0:x1][bridge_mask] = st.BRIDGE_TEAL
    wash_mask = np.zeros(image_arr.shape[:2], dtype=bool)
    wash_mask[y0:y1, x0:x1] = bridge_mask
    alpha = 0.18
    image_arr[wash_mask] = (
        image_arr[wash_mask].astype(np.float32) * (1 - alpha)
        + tie_wash[wash_mask].astype(np.float32) * alpha
    ).astype(np.uint8)
    image = Image.fromarray(image_arr, "RGB")
    draw = ImageDraw.Draw(image)

    # Cell reference outline.
    dashed_rectangle(draw, (x0, y0, x1 - 1, y1 - 1), (120, 128, 121),
                     width=max(1, mm(0.25, ppm)), ppm=ppm)

    # Tie centre lines, edge hints and labels.
    for bridge in plan.get("bridges", []):
        points = bridge.get("points_mm") or [
            [bridge["x1_mm"], bridge["y1_mm"]], [bridge["x2_mm"], bridge["y2_mm"]]]
        coords = [(x0 + mm(x, ppm), y0 + mm(y, ppm)) for x, y in points]
        width_px = max(3, int(round(bridge["width_mm"] * ppm)))
        draw.line(coords, fill=st.BRIDGE_TEAL, width=max(1, mm(0.3, ppm)))
        mx = x0 + mm(bridge["x_mm"], ppm)
        my = y0 + mm(bridge["y_mm"], ppm)
        label = f"B{bridge['index'] + 1} {bridge['width_mm']:g}mm"
        fnt = font(2.6, ppm, True)
        tb = draw.textbbox((mx, my), label, font=fnt, anchor="mm")
        draw.rectangle((tb[0] - 3, tb[1] - 2, tb[2] + 3, tb[3] + 2), fill="white",
                       outline=st.BRIDGE_TEAL, width=1)
        draw.text((mx, my), label, font=fnt, fill=st.BRIDGE_TEAL, anchor="mm")

    # Protected thin-line marks.
    rows = protected or plan.get("protected_lines") or []
    for index, row in enumerate(rows):
        mx = x0 + mm(row["x_mm"], ppm)
        my = y0 + mm(row["y_mm"], ppm)
        color = st.BLOCK_ORANGE if row.get("bridge_clash") else st.PROTECT_BLUE
        r = mm(2.6, ppm)
        draw.ellipse((mx - r, my - r, mx + r, my + r), outline=color, width=max(2, mm(0.4, ppm)))
        text(draw, (mx + r + mm(0.8, ppm), my - mm(2.2, ppm)),
             f"L{index + 1}", ppm, 2.8, color, bold=True, halo=False)

    # Registration marks at the four cell corners and axes.
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        cross_mark(draw, cx, cy, ppm, radius_mm=2.0)
    cmx = (x0 + x1) // 2
    cmy = (y0 + y1) // 2
    draw.line((cmx, y0 - mm(4, ppm), cmx, y0 - mm(1, ppm)), fill=st.BRIDGE_TEAL, width=max(1, mm(.3, ppm)))
    draw.polygon([(cmx - mm(1.4, ppm), y0 - mm(4.6, ppm)),
                  (cmx + mm(1.4, ppm), y0 - mm(4.6, ppm)),
                  (cmx, y0 - mm(2.6, ppm))], fill=st.BRIDGE_TEAL)
    draw.line((x0 - mm(4, ppm), cmy, x0 - mm(1, ppm), cmy), fill=st.BRIDGE_TEAL, width=max(1, mm(.3, ppm)))

    # Header.
    text(draw, (x0, mm(4.5, ppm)), title[:42], ppm, 4.4, st.CUT_DARK, bold=True, halo=False)
    if subtitle:
        text(draw, (x0, mm(10.4, ppm)), subtitle[:72], ppm, 2.9, (92, 104, 97), halo=False)
    text(draw, (x0, mm(14.0, ppm)), "CUT BLACK  -  KEEP TEAL TIES  -  PRINT AT 100% (NO FIT)",
         ppm, 2.7, st.BRIDGE_TEAL, bold=True, halo=False)

    # ---- Paper registration jig ----------------------------------------
    jig_y = geo["sheet_h"] + mm(jig_header_mm, ppm)
    text(draw, (x0, jig_y - mm(12.5, ppm)),
         "Paper alignment marks", ppm, 3.6,
         st.CUT_DARK, bold=True, halo=False)
    start = plan.get("cell_start_mm", 0.0)
    end = plan.get("cell_end_mm", cell_w_mm)
    center = plan.get("cell_center_mm", (start + end) / 2)
    text(draw, (x0, jig_y - mm(7.0, ppm)),
         f"keep on paper - ribbon x {start:.1f}-{end:.1f} mm, center {center:.1f} mm, "
         f"cell {cell_w_mm:.1f} x {cell_h_mm:.1f} mm", ppm, 2.7, (92, 104, 97), halo=False)
    jig_x0 = x0
    jig_y0 = jig_y
    jig_x1 = x0 + mm(cell_w_mm, ppm)
    jig_y1 = jig_y + mm(cell_h_mm, ppm)
    draw.rectangle((jig_x0, jig_y0, jig_x1 - 1, jig_y1 - 1), outline=(150, 156, 148),
                   width=max(1, mm(.25, ppm)))
    for cx, cy in ((jig_x0, jig_y0), (jig_x1, jig_y0), (jig_x0, jig_y1), (jig_x1, jig_y1)):
        cross_mark(draw, cx, cy, ppm, radius_mm=3.0, width_mm=.4)
    mid_y = (jig_y0 + jig_y1) // 2
    mid_x = (jig_x0 + jig_x1) // 2
    draw.line((jig_x0, mid_y, jig_x1, mid_y),
              fill=st.BRIDGE_TEAL, width=max(1, mm(.25, ppm)))
    draw.line((mid_x, jig_y0, mid_x, jig_y1),
              fill=st.BRIDGE_TEAL, width=max(1, mm(.25, ppm)))
    label_y = jig_y1 + mm(4.2, ppm)
    text(draw, (jig_x0, label_y), f"x {start:.1f}", ppm, 2.6, st.CUT_DARK, halo=False)
    text(draw, (jig_x1, label_y), f"x {end:.1f}", ppm, 2.6, st.CUT_DARK,
         anchor="ra", halo=False)
    text(draw, (mid_x, label_y), f"center {center:.1f}",
         ppm, 2.6, st.BRIDGE_TEAL, anchor="ma", halo=False)

    # ---- Scale check ----------------------------------------------------
    bar_y = jig_y1 + mm(15, ppm)
    bar_x0 = x0
    draw.line((bar_x0, bar_y, bar_x0 + mm(100, ppm), bar_y), fill=st.CUT_DARK, width=max(2, mm(.4, ppm)))
    for value in range(0, 101, 10):
        tx = bar_x0 + mm(value, ppm)
        tall = 4 if value % 50 == 0 else 2.5
        draw.line((tx, bar_y, tx, bar_y + mm(tall, ppm)), fill=st.CUT_DARK, width=max(1, mm(.25, ppm)))
        if value % 20 == 0:
            text(draw, (tx, bar_y + mm(4.5, ppm)), str(value), ppm, 2.5, st.CUT_DARK,
                 anchor="ma", halo=False)
    text(draw, (bar_x0, bar_y - mm(5.2, ppm)), "100 mm print-scale check", ppm, 2.8,
         st.CUT_DARK, bold=True, halo=False)

    return image


def render_plan_preview(canvas_rgba, ink, plan, ppm):
    """Stage preview: original art dimmed under the planned cut/tie overlay."""
    height, width = ink.shape
    base = Image.new("RGB", (width, height), "white")
    art = canvas_rgba.convert("RGBA")
    base.paste(art, (0, 0), art)
    arr = np.asarray(base).astype(np.float32)
    # Dim original slightly so the planning overlay reads clearly.
    arr = arr * 0.55 + 255 * 0.45
    preview = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")

    overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
    pixels = np.asarray(overlay).copy()
    bm = plan["bridge_mask"]
    pm = plan.get("protect_mask")
    pixels[ink] = [38, 46, 43, 150]
    pixels[ink & ~bm] = [38, 46, 43, 190]
    pixels[bm] = [21, 110, 90, 120]
    if pm is not None:
        pixels[pm] = [53, 97, 153, 90]
    overlay = Image.fromarray(pixels, "RGBA")
    preview = Image.alpha_composite(preview, overlay)
    draw = ImageDraw.Draw(preview)
    draw.rectangle((0, 0, width - 1, height - 1), outline=st.CUT_DARK, width=2)
    for bridge in plan["bridges"]:
        pts = [(mm(x, ppm), mm(y, ppm)) for x, y in bridge["points_mm"]]
        draw.line(pts, fill=st.BRIDGE_TEAL, width=max(2, mm(.5, ppm)))
        mx, my = mm(bridge["x_mm"], ppm), mm(bridge["y_mm"], ppm)
        fnt = font(3.2, ppm, True)
        label = str(bridge["index"] + 1)
        tb = draw.textbbox((mx, my), label, font=fnt, anchor="mm")
        draw.ellipse((tb[0] - 4, tb[1] - 3, tb[2] + 4, tb[3] + 3), fill="white",
                     outline=st.BRIDGE_TEAL, width=2)
        draw.text((mx, my), label, font=fnt, fill=st.BRIDGE_TEAL, anchor="mm")
    for index, row in enumerate(plan.get("protected_lines", [])):
        mx, my = mm(row["x_mm"], ppm), mm(row["y_mm"], ppm)
        color = st.BLOCK_ORANGE if row.get("bridge_clash") else st.PROTECT_BLUE
        r = mm(3, ppm)
        draw.ellipse((mx - r, my - r, mx + r, my + r), outline=color, width=3)
    return preview.convert("RGB")


def brushing_categories(ink, bridge_mask):
    opening = ink & ~bridge_mask
    blocked = ink & bridge_mask
    blank = ~ink
    return opening, blocked, blank


def render_brush_cell(ink, bridge_mask, stock=(250, 245, 225), paint=st.PAINT_DEFAULT,
                      diagnostic=True, ppm=9.45):
    """One cell: realistic brushing or the bridge-vs-blank category map."""
    h, w = ink.shape
    opening, blocked, blank = brushing_categories(ink, bridge_mask)
    if not diagnostic:
        image = np.broadcast_to(np.array(stock, dtype=np.uint8), (h, w, 3)).copy()
        image[opening] = paint
        # Tie shadows make the retained paper strips legible.
        image[bridge_mask & ~ink] = tuple(max(0, c - 12) for c in stock)
        return Image.fromarray(image, "RGB")

    image = np.full((h, w, 3), 244, dtype=np.uint8)
    image[blank] = st.NEVER_INK
    image[opening] = (44, 52, 49)
    image[blocked] = st.BLOCK_ORANGE
    # Diagonal light stripes clipped exactly to tie-blocked original ink.
    yy, xx = np.indices((h, w))
    stripe = ((xx + yy) % max(7, mm(1.4, ppm))) < max(2, mm(.45, ppm))
    hatch = blocked & stripe
    image[hatch] = (255, 226, 205)
    out = Image.fromarray(image, "RGB")
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, 0, w - 1, h - 1), outline=st.CUT_DARK, width=2)
    return out


def render_brush_proof(ribbon, cell, track, ink, bridge_mask,
                       stock=(250, 245, 225), paint=st.PAINT_DEFAULT):
    """Ribbon at true position plus diagnostic and legend panels."""
    ppm = track.pixels_per_mm
    ribbon_rgb = ribbon.convert("RGB")
    left, right = track.x(cell.start), track.x(cell.end)
    cell_w_px, cell_h_px = right - left, ribbon_rgb.height

    realistic = render_brush_cell(ink, bridge_mask, stock, paint, False, ppm).resize(
        (cell_w_px, cell_h_px), Image.Resampling.NEAREST)
    diagnostic = render_brush_cell(ink, bridge_mask, stock, paint, True, ppm).resize(
        (cell_w_px, cell_h_px), Image.Resampling.NEAREST)

    brushed_ribbon = ribbon_rgb.copy()
    brushed_ribbon.paste(realistic, (left, 0))
    draw = ImageDraw.Draw(brushed_ribbon)
    draw.rectangle((left, 0, right - 1, cell_h_px - 1), outline=st.BLOCK_ORANGE, width=3)
    text(draw, (left + 4, 4), f"FRAME {cell.number + 1} BRUSHED", ppm, 3.0,
         st.BLOCK_ORANGE, bold=True)

    # Context crop: selected cell plus up to one neighbour each side.
    pitch_px = track.x(track.pitch)
    cx = (left + right) // 2
    crop_w = min(ribbon_rgb.width, pitch_px * 3 + 40)
    c0 = max(0, min(ribbon_rgb.width - crop_w, cx - crop_w // 2))
    context = brushed_ribbon.crop((c0, 0, c0 + crop_w, cell_h_px))

    row_h = mm(7, ppm)
    legend_rows = 3
    diag_h = cell_h_px
    total_w = max(context.width, cell_w_px * 2 + mm(8, ppm), mm(120, ppm))
    cap_y = context.height + mm(2.5, ppm)
    diag_title_y = context.height + mm(9.5, ppm)
    diag_y = context.height + mm(16, ppm)
    legend_y = diag_y + diag_h + mm(5, ppm)
    stats_y = legend_y + legend_rows * row_h + mm(1.5, ppm)
    total_h = stats_y + mm(13, ppm)
    canvas = Image.new("RGB", (total_w, total_h), "white")
    canvas.paste(context, (0, 0))
    draw = ImageDraw.Draw(canvas)
    text(draw, (mm(2, ppm), cap_y),
         "Brushed through the stencil at the original ribbon cell",
         ppm, 3.1, st.CUT_DARK, bold=True)

    canvas.paste(diagnostic, (0, diag_y))
    canvas.paste(realistic, (cell_w_px + mm(8, ppm), diag_y))
    text(draw, (0, diag_title_y), "Diagnostic map",
         ppm, 3.2, st.CUT_DARK, bold=True)
    text(draw, (cell_w_px + mm(8, ppm), diag_title_y), "Actual brushed result",
         ppm, 3.2, st.CUT_DARK, bold=True)

    opening, blocked, blank = brushing_categories(ink, bridge_mask)
    items = [
        ((44, 52, 49), "Painted through an opening (original ink here)"),
        (st.BLOCK_ORANGE, "Original ink blocked by a paper tie"),
        (st.NEVER_INK, "Never drawn on: blank paper in the original"),
    ]
    fnt = font(2.9, ppm)
    for index, (color, label) in enumerate(items):
        ry = legend_y + index * row_h
        draw.rectangle((mm(2, ppm), ry, mm(7.5, ppm), ry + mm(5, ppm)), fill=color,
                       outline=st.CUT_DARK, width=max(1, mm(.2, ppm)))
        draw.text((mm(9, ppm), ry - mm(.4, ppm)), label, font=fnt, fill=st.CUT_DARK)

    areas = {
        "painted_mm2": round(float(np.count_nonzero(opening)) / ppm ** 2, 3),
        "bridge_blocked_ink_mm2": round(float(np.count_nonzero(blocked)) / ppm ** 2, 3),
        "blank_mm2": round(float(np.count_nonzero(blank)) / ppm ** 2, 3),
    }
    text(draw, (mm(2, ppm), stats_y),
         f"Orange = tie-blocked ink {areas['bridge_blocked_ink_mm2']} mm2",
         ppm, 3.0, st.BLOCK_ORANGE, bold=True, halo=False)
    text(draw, (mm(2, ppm), stats_y + mm(5.2, ppm)),
         f"Painted {areas['painted_mm2']} mm2   -   "
         f"originally blank {areas['blank_mm2']} mm2",
         ppm, 2.8, (70, 84, 77), bold=True, halo=False)
    return canvas, areas
