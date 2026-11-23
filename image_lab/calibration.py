import cv2
import numpy as np
from .arrays import resize, srgb_decode, srgb_encode, luminance, blur
from . import validation as v


def dark_subtract(image, dark, scale=1):
    return np.maximum(image - dark * scale, 0).astype(np.float32)


def flat_correct(image, flat, strength=1):
    gray = luminance(flat)
    reference = np.median(gray[gray > .02]) if np.any(gray > .02) else 1
    correction = reference / np.maximum(gray, reference * .05)
    correction = 1 + (correction - 1) * strength
    return np.clip(image * correction[:, :, None], 0, 1).astype(np.float32)


def bad_pixel_mask(dark, threshold=.05):
    smooth = cv2.medianBlur(dark.astype(np.float32), 3)
    delta = np.max(np.abs(dark - smooth), axis=2)
    return (delta > threshold).astype(np.float32)


def repair_pixels(image, bad):
    result = image.copy()
    for channel in range(3):
        median = cv2.medianBlur(image[:, :, channel].astype(np.float32), 3)
        result[:, :, channel] = np.where(bad > .5, median, image[:, :, channel])
    return result


def apply_calibration(image, settings, loader):
    if not settings['enabled']:
        return image.copy(), {'enabled': False}
    h, w = image.shape[:2]
    linear = srgb_decode(image)
    report = {'enabled': True, 'hot_pixels': 0, 'dark': False, 'flat': False}
    dark = None
    if settings['dark']:
        dark = srgb_decode(resize(loader(settings['dark']), w, h))
        linear = dark_subtract(linear, dark, settings['dark_scale'])
        report['dark'] = True
    if settings['flat']:
        flat = srgb_decode(resize(loader(settings['flat']), w, h))
        if dark is not None:
            flat = dark_subtract(flat, dark, settings['dark_scale'])
        linear = flat_correct(linear, flat, settings['flat_strength'])
        report['flat'] = True
    if settings['hot_correct'] and dark is not None:
        bad = bad_pixel_mask(dark, settings['hot_threshold'])
        linear = repair_pixels(linear, bad)
        report['hot_pixels'] = int(bad.sum())
    return np.clip(srgb_encode(linear), 0, 1), report


def calibration_report(image, kind='dark'):
    gray = luminance(image)
    center = gray[gray.shape[0] // 4:gray.shape[0] * 3 // 4, gray.shape[1] // 4:gray.shape[1] * 3 // 4]
    report = {'kind': kind, 'mean': float(gray.mean()), 'std': float(gray.std()), 'center_mean': float(center.mean()), 'corner_means': [float(gray[:32, :32].mean()), float(gray[:32, -32:].mean()), float(gray[-32:, :32].mean()), float(gray[-32:, -32:].mean())]}
    if kind == 'dark':
        report['hot_pixel_count'] = int(bad_pixel_mask(image).sum())
        report['warning'] = '暗场应与拍摄温度、曝光和增益一致；JPEG暗场只能近似校正。'
    else:
        report['minimum_to_center_ratio'] = float(gray.min() / max(center.mean(), 1e-6))
        report['warning'] = '平场需要均匀照明，过曝或接近全黑的平场不可可靠校正。'
    return report
