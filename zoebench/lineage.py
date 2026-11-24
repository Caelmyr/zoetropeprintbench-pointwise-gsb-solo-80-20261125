"""Cross-revision frame lineage between a retained proof's loop and the current loop.

The retained proof keeps the frames and placement settings of the earlier cut-paper
loop, while the working drum holds the re-shot loop. A single anchor pair (a frame
recognizable in both loops) orients a cyclic sequence alignment. Each old frame can
be used at most once, gaps on either side are reported explicitly, and weak pairings
are flagged instead of being presented as certain.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from image_lab.validation import Invalid

from . import layout, raster
from .layout import CUT_INK, ORANGE, SOFT_INK, TEAL

RELIABLE_SCORE = 0.58
GAP_OPEN = 0.75
GAP_EXTEND = 0.3
POSE_SIZE = 64
OLD_TINT = (218, 76, 91)
UNMATCHED_TINT = (132, 140, 133)


def frame_image(blob, frame, track, cell, width, height):
    """Paint one frame exactly as its own revision places it, without distortion."""
    picture, _, _ = layout.paint_cell(blob, frame, track, cell, width, height)
    return picture


def alpha_mask(image):
    return np.asarray(image.getchannel("A"), dtype=np.float32) / 255


def pose_signature(image, backdrop=None):
    """Square, aspect-preserving alpha silhouette plus its ink centroid.

    ``backdrop`` is an optional static-mask estimate (pixels present in nearly
    every loop frame, such as the reed line); it is removed so the signature
    describes the moving cut-paper figure rather than the shared background.
    """
    mask = alpha_mask(image)
    if backdrop is not None:
        mask = np.clip(mask - backdrop, 0, 1)
    height, width = mask.shape
    side = max(height, width)
    canvas = np.zeros((side, side), dtype=np.float32)
    top = (side - height) // 2
    left = (side - width) // 2
    canvas[top:top + height, left:left + width] = mask
    coarse = np.asarray(
        Image.fromarray(np.round(canvas * 255).astype(np.uint8)).resize(
            (POSE_SIZE, POSE_SIZE), Image.Resampling.BILINEAR,
        ),
        dtype=np.float32,
    ) / 255
    total = float(coarse.sum())
    if total == 0:
        return coarse, (.5, .5)
    xs = (coarse.sum(axis=0) * (np.arange(POSE_SIZE) + .5)).sum() / total / POSE_SIZE
    ys = (coarse.sum(axis=1) * (np.arange(POSE_SIZE) + .5)).sum() / total / POSE_SIZE
    return coarse, (float(xs), float(ys))


def backdrop_mask(painted):
    """Ink that stays put across at least 90 percent of the loop's frames."""
    if not painted:
        return None
    height = min(image.height for image in painted)
    width = min(image.width for image in painted)
    stacked = np.stack([
        np.asarray(image.resize((width, height), Image.Resampling.BILINEAR)
                   .getchannel("A"), dtype=np.float32) / 255
        for image in painted
    ])
    present = (stacked > .25).sum(axis=0)
    return np.where(present >= max(2, round(.9 * len(painted))), 1.0, 0.0)


def pose_score(first, second):
    """Dice overlap of the moving silhouettes in 0..1, with a centroid drift penalty."""
    mask_a, center_a = first
    mask_b, center_b = second
    shared = float(np.minimum(mask_a, mask_b).sum())
    covered = float((mask_a + mask_b).sum())
    if covered == 0:
        return 0.0
    dice = 2 * shared / covered
    drift = ((center_a[0] - center_b[0]) ** 2 + (center_a[1] - center_b[1]) ** 2) ** .5
    return round(max(0.0, dice * (1 - .12 * drift)), 4)


def score_matrix(old_signatures, new_signatures):
    return np.array(
        [[pose_score(old_sig, new_sig) for new_sig in new_signatures]
         for old_sig in old_signatures],
        dtype=np.float64,
    )


@dataclass(frozen=True)
class Alignment:
    rows: tuple
    score_table: np.ndarray
    anchor_old: int
    anchor_new: int
    old_count: int
    new_count: int


def _rotate(items, start):
    return list(items[start:]) + list(items[:start])


def align_loops(score_table, anchor_old, anchor_new):
    """Cyclic, injective alignment starting from the anchor and following loop order.

    A three-state dynamic program (match / old gap / new gap) with affine gap
    costs keeps each old frame usable at most once. Opening a gap is expensive,
    extending an existing gap is cheap, so one genuine dropped hold is preferred
    over two short gaps that would dodge a slightly changed pose. The anchor
    closes the wrap, so the first new frame cannot acquire a different old
    partner at the join.
    """
    old_count, new_count = score_table.shape
    if not (0 <= anchor_old < old_count and 0 <= anchor_new < new_count):
        raise Invalid("Choose an anchor frame from each loop.")
    old_order = _rotate(range(old_count), anchor_old)
    new_order = _rotate(range(new_count), anchor_new)
    m, n = old_count, new_count
    inf = float("inf")

    # State 0 = ending on a match, 1 = old gap (old frame skipped), 2 = new gap.
    dp = np.full((3, m, n), inf, dtype=np.float64)
    source = np.full((3, m, n), -1, dtype=np.int8)
    dp[0, 0, 0] = 1 - float(score_table[anchor_old, anchor_new])
    for i in range(m):
        for j in range(n):
            if i == 0 and j == 0:
                continue
            if i > 0 and j > 0:
                best = int(np.argmin(dp[:, i - 1, j - 1]))
                dp[0, i, j] = dp[best, i - 1, j - 1] + 1 - float(
                    score_table[old_order[i], new_order[j]])
                source[0, i, j] = best
            if i > 0:
                extend = dp[1, i - 1, j] + GAP_EXTEND
                open_match, open_new = dp[0, i - 1, j] + GAP_OPEN, dp[2, i - 1, j] + GAP_OPEN
                if extend <= open_match and extend <= open_new:
                    dp[1, i, j], source[1, i, j] = extend, 1
                elif open_match <= open_new:
                    dp[1, i, j], source[1, i, j] = open_match, 0
                else:
                    dp[1, i, j], source[1, i, j] = open_new, 2
            if j > 0:
                extend = dp[2, i, j - 1] + GAP_EXTEND
                open_match, open_old = dp[0, i, j - 1] + GAP_OPEN, dp[1, i, j - 1] + GAP_OPEN
                if extend <= open_match and extend <= open_old:
                    dp[2, i, j], source[2, i, j] = extend, 2
                elif open_match <= open_old:
                    dp[2, i, j], source[2, i, j] = open_match, 0
                else:
                    dp[2, i, j], source[2, i, j] = open_old, 1

    i, j = m - 1, n - 1
    state = int(np.argmin(dp[:, i, j]))
    path = []
    while i > 0 or j > 0:
        if state == 0:
            old_index, new_index = old_order[i], new_order[j]
            score = float(score_table[old_index, new_index])
            path.append({"old": old_index, "new": new_index, "score": round(score, 4),
                         "reliable": score >= RELIABLE_SCORE, "anchor": False})
            state = int(source[0, i, j])
            i, j = i - 1, j - 1
        elif state == 1:
            path.append({"old": old_order[i], "new": None, "score": None,
                         "reliable": False, "anchor": False})
            state = int(source[1, i, j])
            i -= 1
        else:
            path.append({"old": None, "new": new_order[j], "score": None,
                         "reliable": False, "anchor": False})
            state = int(source[2, i, j])
            j -= 1
    path.reverse()
    anchor_row = {"old": anchor_old, "new": anchor_new,
                  "score": round(float(score_table[anchor_old, anchor_new]), 4),
                  "reliable": True, "anchor": True}
    rows = tuple([anchor_row] + path)

    rows_by_new = {row["new"]: row for row in rows if row["new"] is not None}
    for row in rows:
        if row["new"] is None:
            continue
        prev_new = (row["new"] - 1) % new_count
        next_new = (row["new"] + 1) % new_count
        prev_row = rows_by_new.get(prev_new)
        next_row = rows_by_new.get(next_new)
        continuity = 0.0
        compared = 0
        for neighbour in (prev_row, next_row):
            if neighbour is None or neighbour["old"] is None:
                continue
            step_new = _cyclic_distance(neighbour["new"], row["new"], new_count)
            step_old = _cyclic_distance(neighbour["old"], row["old"], old_count)
            continuity += 1 - abs(step_new - step_old) / max(old_count, new_count)
            compared += 1
        row["continuity"] = round(continuity / compared, 4) if compared else None
    return Alignment(rows, score_table, anchor_old, anchor_new, old_count, new_count)


def _cyclic_distance(first, second, count):
    direct = abs(first - second)
    return min(direct, count - direct)


def ambiguous_rows(alignment, margin=0.15):
    """Flag new frames that sit near a held pause.

    Two situations deserve a human judgement: the current pairing is weak, or a
    second old frame looks almost as much like the new drawing (the duplicated
    poses of a held pause). The runner-up must itself clear the reliability
    bar, so a merely present but dissimilar neighbour does not raise a flag.
    """
    table = alignment.score_table
    result = []
    used_old = {row["old"] for row in alignment.rows
                if row["old"] is not None and row["new"] is not None}
    for row in alignment.rows:
        if row["new"] is None or row["anchor"]:
            continue
        column = table[:, row["new"]]
        ordered = sorted(range(alignment.old_count), key=lambda old: -column[old])
        best_old, second_old = ordered[0], ordered[1]
        near_tie = float(column[best_old] - column[second_old]) < margin
        runner_up_strong = float(column[second_old]) >= RELIABLE_SCORE
        weak = row["score"] is None or row["score"] < RELIABLE_SCORE
        if not (weak or (near_tie and runner_up_strong)):
            continue
        alternatives = []
        for old_index in ordered:
            if old_index in used_old and old_index != row["old"]:
                continue
            if float(column[old_index]) < RELIABLE_SCORE and old_index != row.get("old"):
                continue
            continuity = _continuity_for(alignment, old_index, row["new"])
            alternatives.append({
                "old_frame": old_index + 1,
                "pose_score": round(float(column[old_index]), 4),
                "continuity_score": continuity,
                "combined_score": round(float(column[old_index]) * .55 + continuity * .45, 4),
                "in_use_elsewhere": old_index in used_old and old_index != row["old"],
            })
            if len(alternatives) >= 3:
                break
        if not alternatives:
            continue
        recommended = max(alternatives, key=lambda item: item["combined_score"])
        result.append({
            "new_frame": row["new"] + 1,
            "suggested_old_frame": recommended["old_frame"],
            "current_old_frame": None if row["old"] is None else row["old"] + 1,
            "pose_gap": round(float(column[best_old] - column[second_old]), 4),
            "alternatives": alternatives,
            "reason": _ambiguity_reason(row, recommended, near_tie and runner_up_strong),
        })
    return result


def _continuity_for(alignment, old_index, new_index):
    score = 0.0
    compared = 0
    for new_neighbour in ((new_index - 1) % alignment.new_count,
                          (new_index + 1) % alignment.new_count):
        neighbour = next((item for item in alignment.rows if item["new"] == new_neighbour), None)
        if neighbour is None or neighbour["old"] is None:
            continue
        step_new = _cyclic_distance(new_neighbour, new_index, alignment.new_count)
        step_old = _cyclic_distance(neighbour["old"], old_index, alignment.old_count)
        score += 1 - abs(step_new - step_old) / max(alignment.old_count, alignment.new_count)
        compared += 1
    return round(score / compared, 4) if compared else 0.0


def _ambiguity_reason(row, recommended, lookalike):
    if lookalike:
        pose = ("two held-pause drawings share almost the same pose",)
    else:
        pose = ("no single pose clears the reliability threshold",)
    return (
        f"Frame {recommended['old_frame']} is suggested because {pose[0]}, and its placement "
        "keeps the before/after frames in one continuous action rather than jumping the loop order."
    )


def correspondence(alignment, old_frames, new_frames):
    """Public per-new-frame mapping, including explicit unmatched entries."""
    old_by_index = {row["old"]: row for row in alignment.rows if row["old"] is not None}
    rows = []
    for new_index in range(alignment.new_count):
        row = next((item for item in alignment.rows if item["new"] == new_index), None)
        entry = {
            "new_frame": new_index + 1,
            "new_title": new_frames[new_index]["title"],
            "old_frame": None,
            "old_title": None,
            "pose_score": None,
            "continuity_score": None,
            "reliable": False,
            "anchor": False,
            "status": "unmatched",
        }
        if row is not None and row["old"] is not None:
            entry.update({
                "old_frame": row["old"] + 1,
                "old_title": old_frames[row["old"]]["title"],
                "pose_score": row["score"],
                "continuity_score": row["continuity"],
                "reliable": row["reliable"],
                "anchor": row["anchor"],
                "status": "anchor" if row["anchor"] else ("matched" if row["reliable"] else "uncertain"),
            })
        rows.append(entry)
    dropped = [
        {"old_frame": row["old"] + 1, "old_title": old_frames[row["old"]]["title"]}
        for row in alignment.rows if row["new"] is None
    ]
    return rows, dropped


def tinted_silhouette(image, tint):
    mask = alpha_mask(image)
    rgb = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.float32)
    rgb[:] = np.array(tint, dtype=np.float32) / 255
    return Image.fromarray(np.round(rgb * 255).astype(np.uint8)), mask


def overlay_pair(new_image, old_image=None, stock="ivory", label=""):
    """Draw the current frame in its own colours and the old frame as a red silhouette.

    Both frames keep their revision's cell width and placement; the narrow strip is
    centred rather than stretched, so an old frame never widens to fit the new one.
    """
    width = max(new_image.width, old_image.width if old_image is not None else 0)
    height = max(new_image.height, old_image.height if old_image is not None else 0)
    canvas = Image.new("RGB", (width, height), raster.PAPER[stock])
    draw = ImageDraw.Draw(canvas)
    if old_image is not None:
        tinted, mask = tinted_silhouette(old_image, OLD_TINT)
        offset = ((width - old_image.width) // 2, (height - old_image.height) // 2)
        old_alpha = Image.fromarray(np.round(mask * 255).astype(np.uint8))
        canvas.paste(tinted, offset, old_alpha)
        draw.rectangle(
            (offset[0], offset[1], offset[0] + old_image.width - 1, offset[1] + old_image.height - 1),
            outline=OLD_TINT, width=2,
        )
    foreground = new_image.convert("RGB")
    offset = ((width - new_image.width) // 2, (height - new_image.height) // 2)
    alpha = new_image.getchannel("A")
    canvas.paste(foreground, offset, alpha)
    draw.rectangle(
        (offset[0], offset[1], offset[0] + new_image.width - 1, offset[1] + new_image.height - 1),
        outline=TEAL, width=2,
    )
    if label:
        draw.rectangle((4, 4, 6 + draw.textlength(label), 22), fill=(255, 253, 246))
        draw.text((7, 7), label, fill=CUT_INK)
    return canvas


def unmatched_canvas(new_image, stock="ivory", label=""):
    image = raster.on_stock(new_image, stock)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, new_image.width - 1, new_image.height - 1), outline=UNMATCHED_TINT, width=3)
    if label:
        draw.rectangle((4, 4, 6 + draw.textlength(label), 22), fill=(255, 253, 246))
        draw.text((7, 7), label, fill=SOFT_INK)
    return image


def lineage_sheet(alignment, old_painted, new_painted, mapping, dropped, stock):
    """Overview proof: one entry per new frame, drawn in loop order with its old partner."""
    tile_h = 150
    tile_w = 132
    row_step = tile_h + 44
    columns = min(4, len(new_painted))
    rows = (len(new_painted) + columns - 1) // columns
    header_h = 46
    footer_h = 26 if dropped else 12
    width = columns * tile_w + 18
    height = header_h + rows * row_step + footer_h
    sheet = Image.new("RGB", (width, height), "#f8f5e7")
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 9), "Cross-revision frame lineage", fill=CUT_INK)
    draw.text((10, 26), "teal = current frame · red = retained proof frame · grey = no reliable old partner",
              fill=SOFT_INK)
    old_by_new = {entry["new_frame"] - 1: entry for entry in mapping}
    for new_index, new_image in enumerate(new_painted):
        column, band_row = divmod(new_index, columns)
        x0 = 8 + column * tile_w
        y0 = header_h + band_row * row_step
        entry = old_by_new[new_index]
        old_image = old_painted[entry["old_frame"] - 1] if entry["old_frame"] else None
        label = f"new {new_index + 1}"
        if old_image is not None:
            tile = overlay_pair(new_image, old_image, stock)
            label += f" / old {entry['old_frame']}"
        else:
            tile = unmatched_canvas(new_image, stock)
            label += " · no old match"
        scale = min((tile_w - 12) / tile.width, tile_h / tile.height)
        tile = tile.resize(
            (max(1, round(tile.width * scale)), max(1, round(tile.height * scale))),
            Image.Resampling.LANCZOS,
        )
        sheet.paste(tile, (x0 + (tile_w - tile.width) // 2, y0))
        if entry["status"] == "uncertain":
            caption = label + "  (uncertain)"
        elif entry["anchor"]:
            caption = label + "  (anchor)"
        else:
            caption = label
        draw.text((x0 + 4, y0 + tile_h + 6), caption,
                  fill=ORANGE if entry["status"] == "uncertain" else CUT_INK)
        if entry["pose_score"] is not None:
            draw.text((x0 + 4, y0 + tile_h + 21), f"pose {entry['pose_score']:.2f}", fill=SOFT_INK)
    if dropped:
        names = ", ".join(f"old {item['old_frame']}" for item in dropped)
        draw.text((10, height - 18), "Old frames without a new counterpart: " + names, fill=SOFT_INK)
    return sheet


def build_analysis(old_painted, new_painted, anchor_old, anchor_new):
    old_backdrop = backdrop_mask(old_painted)
    new_backdrop = backdrop_mask(new_painted)
    old_signatures = [pose_signature(image, old_backdrop) for image in old_painted]
    new_signatures = [pose_signature(image, new_backdrop) for image in new_painted]
    table = score_matrix(old_signatures, new_signatures)
    alignment = align_loops(table, anchor_old, anchor_new)
    questions = ambiguous_rows(alignment)
    return alignment, questions
