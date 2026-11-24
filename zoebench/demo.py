"""Deterministic synthetic paper-cut art and a photographed-slot surrogate."""

from math import cos, pi, sin

from PIL import Image, ImageDraw

from . import model, raster
from .store import identity


def artwork(index, count=8):
    turn = index / count * 2 * pi
    image = Image.new("RGBA", (300, 350))
    pen = ImageDraw.Draw(image)

    # Cut-paper bird jumping above a short curved reed. The thread and star
    # give the proof both low-motion detail and a recognizable silhouette.
    x = round(150 + 60 * sin(turn))
    y = round(185 + 64 * cos(turn))
    wing = round(43 * sin(turn * 2 + pi / 5))
    mirror = 1 if cos(turn) >= 0 else -1
    pen.line([(23, 303), (65, 299), (105, 303), (145, 301), (187, 298), (232, 302), (274, 299)],
             fill="#6f7864", width=3)
    pen.ellipse((38, 50, 79, 91), fill="#e8ae4e")
    pen.ellipse((62, 44, 87, 69), fill="#f7cb74")
    pen.line((39, 117, 44, 124, 38, 131), fill="#698575", width=3)
    pen.line((253, 88, 253, 110), fill="#698575", width=3)
    pen.line((243, 99, 263, 99), fill="#698575", width=3)
    pen.polygon([(x - mirror * 26, y + 7), (x - mirror * 83, y + 24), (x - mirror * 61, y - 9),
                 (x - mirror * 33, y - 17)], fill="#254e47")
    pen.ellipse((x - 42, y - 19, x + 40, y + 32), fill="#237763")
    head = x + mirror * 32
    pen.ellipse((head - 15, y - 27, head + 15, y + 4), fill="#2f8a73")
    pen.polygon([(head + mirror * 10, y - 14), (head + mirror * 34, y - 10), (head + mirror * 11, y - 3)],
                fill="#e5a143")
    pen.ellipse((head + mirror * 4 - 3, y - 19, head + mirror * 4 + 3, y - 13), fill="#1d312b")
    pen.polygon([(x + mirror * 10, y + 9), (x - mirror * 6, y + wing - 58),
                 (x - mirror * 37, y + wing - 37), (x - mirror * 29, y + 8)], fill="#55a591")
    pen.arc((x - 29, y - 11, x + 24, y + 24), 15, 165, fill="#1c6756", width=3)
    return image


def slot_artwork():
    width, height = 1440, 140
    image = Image.new("RGB", (width, height), "#c9c0a7")
    draw = ImageDraw.Draw(image)
    draw.rectangle((36, 26, width - 37, 110), fill="#293d38", outline="#172825", width=2)
    scale = (width - 72) / 288
    slots = [(18, 1.9), (54, 2.7), (89, 2.1), (125, 2.0), (170, 2.9), (202, 1.8), (234, 3.25), (270, 2.5)]
    for index, (center, breadth) in enumerate(slots):
        start = round(36 + (center - breadth / 2) * scale)
        end = round(36 + (center + breadth / 2) * scale)
        draw.rectangle((start, 34, end, 102), fill="#f8eed1")
        draw.text((round(36 + center * scale), 117), str(index + 1), fill="#283b35", anchor="mt")
    draw.line((36, 18, width - 37, 18), fill="#30463e", width=2)
    for point in range(0, 288, 8):
        xx = round(36 + point * scale)
        draw.line((xx, 18, xx, 22), fill="#30463e")
    return image


def frame_record(library, image, original, title, source_name):
    return {
        "id": identity(), "title": title, "filename": source_name, "blob": library.keep(original),
        "width": image.width, "height": image.height, "transform": model.frame_transform({}),
        "calibration": model.frame_calibration({}),
    }


def create(library):
    frames = []
    for index in range(8):
        source = artwork(index)
        filename = f"reed-bird-{index + 1:02}.png"
        frames.append(frame_record(library, source, raster.png(source), f"Bird {index + 1:02}", filename))
    frames[2]["transform"]["shift_x"] = 1.5
    frames[4]["transform"]["shift_y"] = -2
    source = slot_artwork()
    slot_scan = {
        "blob": library.keep(raster.png(source)), "filename": "synthetic-slots.png",
        "width": source.width, "height": source.height,
        "registration": {"crop": [.025, .27, .975, .72], "scan_span_mm": 288, "origin_mm": 0},
    }
    return library.create("Reed bird · sample drum", model.settings({}), frames, slot_scan, synthetic=True)


ALIGN_JITTER = [(0, 0), (7, -5), (-9, 4), (4, 9), (-6, -7), (11, 3), (2, -2), (-3, 6), (6, -9)]
ALIGN_BAD_INDEX = 6
ALIGN_SIZE = (432, 600)


def alignment_artwork(index, count=9):
    """Moon + ground stay put in art space; a jitter stands in for camera framing."""
    from random import Random

    width, height = ALIGN_SIZE
    jx, jy = ALIGN_JITTER[index]
    rnd = Random(3000 + index)
    image = Image.new("RGBA", (width, height), (244, 238, 220, 255))
    pen = ImageDraw.Draw(image)
    for _ in range(6000):
        x, y = rnd.randrange(width), rnd.randrange(height)
        delta = rnd.randint(-8, 6)
        pen.point((x, y), fill=(244 + delta, 238 + delta, 220 + delta, 255))

    def p(x, y):
        return x + jx, y + jy

    mx, my = p(372, 96)
    pen.ellipse((mx - 24, my - 24, mx + 24, my + 24), fill=(232, 190, 92, 255))
    pen.ellipse((mx - 12, my - 29, mx + 30, my + 19), fill=(244, 238, 220, 255))
    pen.ellipse((mx - 24, my - 24, mx + 24, my + 24), outline=(190, 140, 60, 255), width=2)
    sx, sy = p(300, 60)
    pen.ellipse((sx - 3, sy - 3, sx + 3, sy + 3), fill=(120, 130, 110, 255))

    points = []
    for x in range(-10, width + 11, 12):
        y = 512 + int(6 * sin(x / 70.0))
        points.append(p(x, y))
    pen.line(points, fill=(96, 110, 88, 255), width=4, joint="curve")

    for base_x, lean in ((14, 10), (30, -8), (width - 16, -12), (width - 34, 9)):
        gx, gy = p(base_x, 514)
        pen.line((gx, gy, gx + lean, gy - 78), fill=(84, 112, 78, 255), width=5)
        pen.line((gx + 4, gy, gx + 4 + lean // 2, gy - 52), fill=(104, 132, 92, 255), width=4)

    rx, ry = p(96, 470)
    pen.line((rx, ry + 44, rx + 6, ry - 34), fill=(110, 122, 96, 255), width=5)
    pen.ellipse((rx - 12, ry - 46, rx + 16, ry - 20), fill=(150, 120, 70, 255))

    t = index / count * 2 * pi
    bx = 150 + 190 * (index / max(1, count - 1)) + jx
    by = 300 - 120 * sin(t) + jy
    flap = 34 * sin(t * 2)
    pen.polygon([(bx - 30, by), (bx - 92, by + 26), (bx - 66, by - 16), (bx - 34, by - 20)],
                fill=(37, 78, 71, 255))
    pen.ellipse((bx - 46, by - 22, bx + 44, by + 34), fill=(35, 119, 99, 255))
    hx = bx + 36
    pen.ellipse((hx - 16, by - 30, hx + 16, by + 4), fill=(47, 138, 115, 255))
    pen.polygon([(hx + 12, by - 16), (hx + 40, by - 11), (hx + 13, by - 4)], fill=(229, 161, 67, 255))
    pen.polygon([(bx + 6, by + 8), (bx - 8, by - 56 + flap), (bx - 46, by - 34 + flap),
                 (bx - 30, by + 10)], fill=(85, 165, 145, 255))
    pen.ellipse((hx + 2, by - 21, hx + 8, by - 15), fill=(20, 30, 26, 255))

    if index == ALIGN_BAD_INDEX:
        from PIL import ImageFilter

        image = image.filter(ImageFilter.GaussianBlur(7))
        wash = Image.new("RGBA", image.size, (238, 234, 222, 255))
        image = Image.blend(image, wash, .55).filter(ImageFilter.GaussianBlur(2))
    return image


def create_alignment(library):
    frames = []
    for index in range(len(ALIGN_JITTER)):
        source = alignment_artwork(index)
        filename = f"moon-line-{index + 1:02}.png"
        title = f"Moon {index + 1:02}" + (" (soft focus)" if index == ALIGN_BAD_INDEX else "")
        frames.append(frame_record(library, source, raster.png(source), title, filename))
    settings = model.settings({"circumference": 324, "height_mm": 50, "align_max_shift_mm": 2.5})
    draft = library.create("Thin moon · camera jitter sample", settings, frames, None, synthetic=True)
    draft_alignment = {
        "reference_frame": frames[0]["id"],
        "space": "cell",
        "max_shift_mm": 2.5,
        "regions": [
            {"box": [.68, .10, .88, .24], "label": "Thin moon"},
            {"box": [.02, .82, .98, .90], "label": "Ground line"},
        ],
    }
    # Persist the starter marks directly (library.create already wrote revision 1).
    key = draft["id"]

    def seed(saved):
        saved["alignment"] = draft_alignment

    return library.change(key, draft["revision"], seed)
