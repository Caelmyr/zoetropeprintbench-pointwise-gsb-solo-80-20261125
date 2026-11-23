"""Millimetre print geometry for a drum with a single uniform picture band."""

from dataclasses import dataclass
from math import floor

from PIL import Image, ImageDraw

from image_lab.validation import Invalid

from . import raster

CUT_INK = (47, 54, 51)
SOFT_INK = (116, 128, 121)
TEAL = (21, 110, 90)
ORANGE = (218, 92, 48)


@dataclass(frozen=True)
class Cell:
    number: int
    start: float
    end: float

    @property
    def center(self):
        return (self.start + self.end) / 2

    @property
    def width(self):
        return self.end - self.start


@dataclass(frozen=True)
class Track:
    cells: tuple
    circumference: float
    height: float
    dpi: int
    join_mm: float

    @property
    def pixels_per_mm(self):
        return self.dpi / 25.4

    def x(self, mm):
        return round(mm * self.pixels_per_mm)

    @property
    def pitch(self):
        return self.circumference / len(self.cells)

    @property
    def exact_image_size(self):
        return self.x(self.circumference + self.join_mm), self.x(self.height)


def uniform_track(frame_count, settings):
    if frame_count < 2:
        raise Invalid("Load at least two frames to make a picture ribbon.")
    circumference = settings["circumference"]
    pitch = circumference / frame_count
    if pitch < 8:
        raise Invalid("Each frame must be at least 8 mm wide. Reduce the frame count or increase circumference.")
    if settings["action_margin"] * 2 >= min(pitch, settings["height_mm"]):
        raise Invalid("The quiet margin must leave a visible area within every frame.")
    cells = tuple(Cell(index, index * pitch, (index + 1) * pitch) for index in range(frame_count))
    return Track(cells, circumference, settings["height_mm"], settings["dpi"], settings["join_mm"])


def ordinal_slots(track, slot_width, phase):
    if slot_width >= track.pitch / 2:
        raise Invalid("Viewing slots must be narrower than half the distance between centers.")
    return [((cell.center - track.circumference / 2 + phase) % track.circumference, slot_width) for cell in track.cells]


def paint_cell(source, frame, track, cell, width, height, stock=None):
    return raster.place(
        source, frame["transform"], (width, height), (cell.width, track.height), stock,
    )


def picture_ribbon(track, frames, source, stock):
    band = Image.new("RGB", track.exact_image_size, raster.PAPER[stock])
    height = band.height
    for frame, cell in zip(frames, track.cells, strict=True):
        left, right = track.x(cell.start), track.x(cell.end)
        picture, _, _ = paint_cell(source(frame), frame, track, cell, right - left, height, raster.PAPER[stock])
        band.paste(picture.convert("RGB"), (left, 0))
    return band


def scale_ruler(track):
    width = track.x(114)
    image = Image.new("RGB", (width, track.x(32)), "white")
    draw = ImageDraw.Draw(image)
    x_start = 7
    y_axis = track.x(14)
    draw.line((track.x(x_start), y_axis, track.x(x_start + 100), y_axis), fill=CUT_INK, width=2)
    for mm in range(101):
        x = track.x(x_start + mm)
        tall = 5 if mm % 10 == 0 else 3 if mm % 5 == 0 else 1.5
        draw.line((x, y_axis, x, y_axis + track.x(tall)), fill=CUT_INK, width=1)
        if mm % 10 == 0:
            draw.text((x, y_axis + track.x(7)), str(mm), fill=CUT_INK, anchor="mt")
    draw.text((track.x(7), track.x(4)), "100 mm print check", fill=CUT_INK)
    return image


def cut_guide(track):
    width, drawn_height = track.exact_image_size
    y_top = track.x(5)
    y_low = y_top + drawn_height
    image = Image.new("RGB", (width, y_low + track.x(8)), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, y_top, track.x(track.circumference) - 1, y_low), outline=CUT_INK, width=2)
    for cell in track.cells:
        left = track.x(cell.start)
        center = track.x(cell.center)
        draw.line((left, y_top, left, y_low), fill=SOFT_INK, width=1)
        draw.line((center, 2, center, y_top - 1), fill=TEAL, width=2)
        draw.text((center, y_low + track.x(2)), str(cell.number + 1), fill=CUT_INK, anchor="mt")
    if track.join_mm:
        start = track.x(track.circumference)
        draw.rectangle((start, y_top, width - 1, y_low), outline=ORANGE, width=2)
        for y in range(y_top, y_low, max(5, track.x(3))):
            draw.line((start, y, width - 1, min(y_low, y + track.x(track.join_mm))), fill=ORANGE)
    return image


def slot_ruler(track, width_mm, phase, direction):
    band = Image.new("RGB", (track.x(track.circumference), track.x(23)), "white")
    draw = ImageDraw.Draw(band)
    y_start, y_end = track.x(4), track.x(17)
    draw.rectangle((0, y_start, band.width - 1, y_end), outline=CUT_INK, width=2)
    slots = ordinal_slots(track, width_mm, phase)
    for index, (center, slot) in enumerate(slots):
        x0, x1 = center - slot / 2, center + slot / 2
        for wrap in (-track.circumference, 0, track.circumference):
            left, right = max(0, track.x(x0 + wrap)), min(band.width, track.x(x1 + wrap))
            if left < right:
                draw.rectangle((left, y_start, right - 1, y_end), fill=CUT_INK)
        draw.text((track.x(center), y_end + track.x(1)), str(index + 1), fill=TEAL, anchor="mt")
    draw.text((4, 2), direction, fill=SOFT_INK)
    return band, slots


def join_preview(image, track):
    """Face the two active edges as they will meet after the tab is glued underneath."""
    span = min(floor(track.x(track.pitch)), image.width // 2)
    end = track.x(track.circumference)
    joined = Image.new("RGB", (span * 2, image.height + 25), "white")
    joined.paste(image.crop((end - span, 0, end, image.height)), (0, 0))
    joined.paste(image.crop((0, 0, span, image.height)), (span, 0))
    draw = ImageDraw.Draw(joined)
    draw.line((span, image.height, span, joined.height), fill=ORANGE, width=2)
    draw.text((span + 4, image.height + 5), "active join", fill=CUT_INK)
    return joined
