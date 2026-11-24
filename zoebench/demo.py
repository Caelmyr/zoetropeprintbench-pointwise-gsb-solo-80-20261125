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
    return library.create("Reed bird · sample drum", model.settings({"cell_width": 32}), frames, slot_scan, synthetic=True)
