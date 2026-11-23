import numpy as np
from scipy import ndimage as ndi
from .arrays import luminance, blur, normalize_weights


def normalized_brightness(images):
    grays = []
    for image in images:
        gray = luminance(image)
        mean = np.median(gray[(gray > .02) & (gray < .98)]) if np.any((gray > .02) & (gray < .98)) else .5
        grays.append(np.clip(gray / max(mean, .02) * .4, 0, 2))
    return np.stack(grays)


def motion_maps(images, validities, threshold=.15):
    stack = normalized_brightness(images)
    valid = np.stack(validities) > .5
    masked = np.ma.array(stack, mask=~valid)
    median = np.ma.median(masked, axis=0).filled(0).astype(np.float32)
    differences = np.abs(stack - median[None])
    motion = np.clip((differences - threshold) / max(threshold, .01), 0, 1)
    motion *= valid
    return motion.astype(np.float32), differences.astype(np.float32)


def suppress_ghosts(weights, images, validities, strength, threshold, reference):
    if strength <= 0:
        return weights, np.zeros_like(weights[0])
    motion, differences = motion_maps(images, validities, threshold)
    affected = blur(np.max(motion, axis=0), 1.2) * strength
    reference_valid = validities[reference]
    best = np.argmin(np.where(np.stack(validities) > .5, differences, 1e6), axis=0)
    yy, xx = np.indices(best.shape)
    best = np.where(reference_valid > .99, reference, best)
    stable = np.zeros_like(weights)
    stable[best, yy, xx] = 1
    result = np.asarray(weights) * (1 - affected[None]) + stable * affected[None]
    result *= np.stack(validities)
    return normalize_weights(result), affected.astype(np.float32)


def rejection_statistics(motion):
    return {'affected_fraction': float(np.mean(motion > .25)), 'strong_fraction': float(np.mean(motion > .75)), 'mean_strength': float(motion.mean()), 'warning': '运动抑制依赖亮度归一化差异，曝光严重截断或视差可造成误判。'}
