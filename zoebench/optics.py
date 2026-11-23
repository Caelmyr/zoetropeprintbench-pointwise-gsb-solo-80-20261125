"""Finite slit and shutter proofing for a cylindrical picture band."""

from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

from . import raster
from .layout import ORANGE, TEAL, ordinal_slots


def phase_steps(track, settings, frame):
    slot_center, slot_width = ordinal_slots(track, settings["slot_width"], settings["slot_phase"])[frame]
    travel = track.circumference * settings["rpm"] / 60 * settings["exposure_ms"] / 1000
    slit = ((np.arange(9) + .5) / 9 - .5) * slot_width
    shutter = (np.arange(13) + .5) / 13 * travel
    direction = 1 if settings["direction"] == "clockwise" else -1
    opposite = slot_center + track.circumference / 2
    centers = (opposite + slit[:, None] + direction * shutter[None, :]).ravel()
    return centers, travel


def continuous_band(image, track):
    """Exclude the gluing tab from the closed circumference."""
    width = track.x(track.circumference)
    band = image.crop((0, 0, width, image.height))
    ratio = min(1, 4096 / width, 420 / image.height)
    if ratio < 1:
        band = band.resize(
            (max(1, round(width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS,
        )
    return band


def slit_view(image, track, settings, frame):
    band = continuous_band(image, track)
    columns = max(1, round(band.width * track.pitch / track.circumference))
    offsets = ((np.arange(columns) + .5) / columns - .5) * track.pitch
    centers, travel = phase_steps(track, settings, frame)
    column_samples = (centers[:, None] + offsets) / track.circumference * band.width - .5
    first = np.floor(column_samples).astype(int)
    right_weight = column_samples - first
    first %= band.width
    second = (first + 1) % band.width
    values = raster.linear(raster.rgb_array(band))
    weights_a = np.zeros((columns, band.width), np.float32)
    column_idx = np.broadcast_to(np.arange(columns), first.shape)
    np.add.at(weights_a, (column_idx, first), 1 - right_weight)
    np.add.at(weights_a, (column_idx, second), right_weight)
    weights_a /= len(centers)
    integrated = np.einsum("hxc,wx->hwc", values, weights_a, optimize=True)

    frame_centers = np.floor((centers % track.circumference) / track.pitch).astype(int)
    owners = Counter(map(int, frame_centers))
    dominant = max(owners, key=lambda value: (owners[value], -value))
    wear = sum(
        (np.floor(((center + offsets) % track.circumference) / track.pitch) != frame).sum()
        for center in centers
    )
    summary = {
        "selected_frame": frame + 1,
        "dominant_center_frame": dominant + 1,
        "wrong_frame_area_percent": round(100 * int(wear) / (len(centers) * columns), 3),
        "travel_mm": round(travel, 3),
        "slit_width_mm": settings["slot_width"],
        "speed_rpm": settings["rpm"],
    }
    counts = [{"center_frame": index + 1, "shutter_share": round(value / len(centers), 5)}
              for index, value in sorted(owners.items())]
    return raster.rgb_image(raster.encode_linear(integrated)), summary, counts


def onion_image(previous, current, next_frame):
    """Distinguish the three silhouettes while retaining the original current frame."""
    current_values = raster.rgb_array(current)
    old = np.asarray(previous.getchannel("A"), dtype=np.float32) / 255
    now = np.asarray(current.getchannel("A"), dtype=np.float32) / 255
    upcoming = np.asarray(next_frame.getchannel("A"), dtype=np.float32) / 255
    background = np.zeros_like(current_values) + .98
    old_only = np.clip(old - now, 0, 1) * .72
    upcoming_only = np.clip(upcoming - now, 0, 1) * .72
    background = background * (1 - old_only[:, :, None]) + np.array([.93, .26, .35]) * old_only[:, :, None]
    background = background * (1 - upcoming_only[:, :, None]) + np.array([.08, .67, .59]) * upcoming_only[:, :, None]
    foreground = current_values * now[:, :, None]
    overlay = raster.rgb_image(background * (1 - now[:, :, None]) + foreground)
    compared = max(float((old + upcoming).sum()), 1)
    return overlay, {
        "previous_only_percent": round(100 * float(np.clip(old - now, 0, 1).sum()) / compared, 3),
        "next_only_percent": round(100 * float(np.clip(upcoming - now, 0, 1).sum()) / compared, 3),
    }


def sample_centroid(image):
    alpha = np.array(image.getchannel("A"), dtype=np.float64) / 255
    if alpha.sum() == 0:
        return None
    x = (alpha.sum(axis=0) * (np.arange(image.width) + .5)).sum() / alpha.sum()
    y = (alpha.sum(axis=1) * (np.arange(image.height) + .5)).sum() / alpha.sum()
    return float(x), float(y)


def motion_overlay(image, centres):
    draw = ImageDraw.Draw(image)
    if len(centres) > 1:
        for current, upcoming in zip(centres, centres[1:] + centres[:1]):
            if current is not None and upcoming is not None:
                draw.line((*current, *upcoming), fill=TEAL, width=2)
    for index, center in enumerate(centres):
        if center is None:
            continue
        x, y = center
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=ORANGE, outline="white")
        draw.text((x + 5, y - 10), str(index + 1), fill=TEAL, stroke_width=1, stroke_fill="white")
    return image
