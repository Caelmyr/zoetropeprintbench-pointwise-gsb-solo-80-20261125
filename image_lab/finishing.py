import cv2
import numpy as np
from .arrays import luminance, blur, srgb_decode, srgb_encode


def white_balance(image, temperature=0, tint=0):
    gains = np.array([1 + .3 * temperature + .1 * tint, 1 - .15 * tint, 1 - .3 * temperature + .1 * tint], np.float32)
    return image * gains


def saturation(image, factor=1):
    gray = luminance(image)[:, :, None]
    return gray + (image - gray) * factor


def contrast(image, factor=1):
    return (image - .5) * factor + .5


def shadows_highlights(image, shadows=0, highlights=0):
    gray = np.clip(luminance(image), 0, 1)
    low = (1 - gray) ** 3
    high = gray ** 3
    shift = shadows * low * .35 + highlights * high * .35
    return image + shift[:, :, None]


def curve_adjust(image, points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return np.interp(image, xs, ys).astype(np.float32)


def clarity(image, amount):
    gray = luminance(image)
    residual = gray - blur(gray, 8)
    return image + residual[:, :, None] * amount


def sharpen(image, amount):
    return image + (image - blur(image, 1)) * amount


def vignette(image, amount):
    h, w = image.shape[:2]
    y, x = np.mgrid[:h, :w].astype(np.float32)
    radial = ((x - w / 2) / max(w / 2, 1)) ** 2 + ((y - h / 2) / max(h / 2, 1)) ** 2
    return image * np.clip(1 - amount * radial * .5, .1, 2)[:, :, None]


def clahe(image, amount):
    lab = cv2.cvtColor(np.clip(image, 0, 1).astype(np.float32), cv2.COLOR_RGB2LAB)
    encoded = np.rint(lab[:, :, 0] / 100 * 255).astype(np.uint8)
    mapped = cv2.createCLAHE(max(amount, .1), (8, 8)).apply(encoded)
    lab[:, :, 0] = mapped.astype(np.float32) / 255 * 100
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def finish_image(image, settings):
    output = srgb_decode(np.clip(image, 0, 1)) * 2 ** settings['exposure']
    output = white_balance(output, settings['temperature'], settings['tint'])
    output = srgb_encode(np.maximum(output, 0))
    output = shadows_highlights(output, settings['shadows'], settings['highlights'])
    output = contrast(output, settings['contrast'])
    output = saturation(output, settings['saturation'])
    output = np.clip((output - settings['black']) / max(settings['white'] - settings['black'], .01), 0, 1)
    output = output ** (1 / settings['gamma'])
    output = curve_adjust(output, settings['curve'])
    if settings['clahe'] > 0:
        output = clahe(np.clip(output, 0, 1), settings['clahe'])
    if settings['clarity']:
        output = clarity(output, settings['clarity'])
    if settings['sharpen']:
        output = sharpen(output, settings['sharpen'])
    if settings['vignette']:
        output = vignette(output, settings['vignette'])
    return np.clip(output, 0, 1).astype(np.float32)


def white_balance_estimate(image, method='grayworld', bounds=None):
    if bounds:
        x0, y0, x1, y1 = bounds
        image = image[y0:y1, x0:x1]
    linear = srgb_decode(image)
    if method == 'whitepatch':
        channels = np.percentile(linear, 95, axis=(0, 1))
    else:
        channels = linear.mean(axis=(0, 1))
    target = max(float(channels.mean()), 1e-6)
    gains = target / np.maximum(channels, 1e-6)
    return {'gains': gains.tolist(), 'method': method, 'temperature_suggestion': float(np.clip((gains[0] - gains[2]) / .6, -1, 1)), 'tint_suggestion': float(np.clip((1 - gains[1]) / .15, -1, 1)), 'note': '灰世界/白块估计依赖场景中性颜色假设，可作为手工调节起点。'}
