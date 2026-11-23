import numpy as np
from .arrays import srgb_decode, luminance, safe_divide, normalize_weights
from .metadata import exposure_seconds
from .ghosts import suppress_ghosts, rejection_statistics
from .validation import Invalid


def response_decode(image, response='srgb', gamma=2.2):
    if response == 'srgb':
        return srgb_decode(image)
    if response == 'gamma':
        return np.maximum(image, 0) ** gamma
    return image.astype(np.float32)


def radiance_weight(image, kind, low, high):
    gray = luminance(image)
    if kind == 'triangle':
        value = np.maximum(0, 1 - np.abs(gray * 2 - 1))
    elif kind == 'gaussian':
        value = np.exp(-((gray - .5) / .23) ** 2)
    else:
        value = np.ones_like(gray)
    value *= (gray > low) & (gray < high)
    return value.astype(np.float32)


def merge_hdr(prepared, settings):
    images = prepared['images']
    frames = prepared['frames']
    validities = prepared['validities']
    times = np.asarray([exposure_seconds(frame) for frame in frames], np.float32)
    if len(images) < 2:
        raise Invalid('HDR至少需要两张不同曝光照片')
    if np.max(times) / np.min(times) < 1.05:
        raise Invalid('曝光时间相同，请先填写不同曝光比例或使用曝光融合')
    times /= np.median(times)
    raw_weights = [radiance_weight(image, settings['weight'], settings['clip_low'], settings['clip_high']) * valid * frame['weight'] for image, valid, frame in zip(images, validities, frames)]
    raw_sum = np.sum(raw_weights, axis=0)
    weights = normalize_weights(raw_weights, prepared['reference_index'])
    weights, motion = suppress_ghosts(weights, images, validities, settings['ghost'], .15, prepared['reference_index'])
    response_report = None
    if settings['response'] == 'learned':
        from .response import estimate_response, decode_with_response
        response_report = estimate_response(prepared)
        linear = [decode_with_response(image, response_report['log_response']) / exposure for image, exposure in zip(images, times)]
    else:
        linear = [response_decode(image, settings['response'], settings['gamma']) / exposure for image, exposure in zip(images, times)]
    logarithmic = np.zeros_like(images[0])
    for field, weight in zip(linear, weights):
        logarithmic += np.log(np.maximum(field, 1e-8)) * weight[:, :, None]
    radiance = np.exp(logarithmic).astype(np.float32)
    valid = np.max(validities, axis=0)
    radiance[valid < .001] = 0
    unsupported = (raw_sum < 1e-8) & (valid > .5)
    gray = luminance(radiance)
    positive = gray[(gray > 1e-7) & (valid > .5)]
    stops = float(np.log2(np.percentile(positive, 99.5) / max(np.percentile(positive, .5), 1e-8))) if positive.size else 0
    fields = {'linear': radiance, 'valid': valid, 'ghost': motion, 'unreliable': unsupported.astype(np.float32), 'contribution': np.argmax(weights, axis=0).astype(np.float32) / max(len(images) - 1, 1), 'weights': weights}
    report = {'mode': 'hdr', 'relative_exposures': times.tolist(), 'dynamic_range_stops_robust': stops, 'unreliable_fraction': float(unsupported.mean()), 'ghost': rejection_statistics(motion), 'response': settings['response'], 'algorithm': '已知响应曲线的对数辐射加权合并；不声称求解相机真实响应曲线。'}
    report['response_calibration'] = response_report
    return fields, report


def response_curve(settings, samples=128):
    x = np.linspace(0, 1, samples, dtype=np.float32)
    image = np.repeat(x[None, :, None], 3, axis=2)
    linear = response_decode(image, settings['response'], settings['gamma'])[0, :, 0]
    weight = radiance_weight(image, settings['weight'], settings['clip_low'], settings['clip_high'])[0]
    return {'encoded': x.tolist(), 'linear': linear.tolist(), 'weight': weight.tolist()}
