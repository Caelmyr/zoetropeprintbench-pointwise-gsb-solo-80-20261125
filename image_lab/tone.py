import numpy as np
from .arrays import luminance, geometric_mean, srgb_encode, blur, safe_divide


def reinhard(luma, key=.18, white=4):
    scale = key / max(geometric_mean(luma), 1e-6)
    mapped = luma * scale
    return mapped * (1 + mapped / (white * white)) / (1 + mapped)


def filmic(value):
    a, b, c, d, e = 2.51, .03, 2.43, .59, .14
    return np.clip(value * (a * value + b) / (value * (c * value + d) + e), 0, 1)


def local_mapping(luma, radius=12, detail=1, key=.18):
    logarithm = np.log(np.maximum(luma, 1e-6))
    base = blur(logarithm, radius)
    residual = logarithm - base
    span = max(float(np.percentile(base, 99) - np.percentile(base, 1)), 1e-5)
    compression = min(1., np.log(4) / span)
    mapped = np.exp((base - np.median(base)) * compression + residual * detail) * key
    return mapped / (1 + mapped)


def drago_mapping(luma, key=.18, white=4):
    maximum = max(float(np.percentile(luma, 99.9)), 1e-5)
    scaled = luma / max(geometric_mean(luma), 1e-5) * key
    top = np.log1p(scaled)
    denominator = np.log(2 + 8 * np.maximum(luma / maximum, 0) ** .85)
    mapped = top / np.maximum(denominator, 1e-6)
    return mapped / max(float(np.percentile(mapped, 99.9)), 1)


def tone_map(linear, settings):
    image = np.maximum(linear, 0) * 2 ** settings['exposure']
    gray = luminance(image)
    operator = settings['operator']
    if operator == 'reinhard':
        mapped = reinhard(gray, settings['key'], settings['white'])
    elif operator == 'local':
        mapped = local_mapping(gray, settings['local_radius'], settings['detail'], settings['key'])
    elif operator == 'drago':
        mapped = drago_mapping(gray, settings['key'], settings['white'])
    elif operator == 'filmic':
        mapped = filmic(gray)
    else:
        mapped = gray
    ratio = safe_divide(image, gray[:, :, None])
    ratio = np.maximum(ratio, 0) ** settings['saturation']
    result = ratio * mapped[:, :, None]
    return np.clip(srgb_encode(result), 0, 1).astype(np.float32)


def tone_curve(settings, minimum=-10, maximum=8, samples=128):
    stops = np.linspace(minimum, maximum, samples, dtype=np.float32)
    values = 2 ** stops
    image = np.broadcast_to(values[None, :, None], (2, samples, 3)).copy()
    output = tone_map(image, settings)[0, :, 0]
    return {'input_stops': stops.tolist(), 'display': output.tolist(), 'operator': settings['operator']}
