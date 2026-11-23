import numpy as np
from .arrays import srgb_decode, srgb_encode, normalize_weights, weighted_rgb, safe_divide


def sigma_clip(stack, valid, sigma=2, iterations=2):
    accepted = valid[:, :, :, None] > .5
    accepted = np.broadcast_to(accepted, stack.shape).copy()
    for _ in range(iterations):
        count = accepted.sum(axis=0)
        mean = safe_divide(np.sum(stack * accepted, axis=0), count)
        variance = safe_divide(np.sum((stack - mean) ** 2 * accepted, axis=0), count)
        deviation = np.sqrt(variance)
        reject = np.abs(stack - mean) > sigma * np.maximum(deviation, 1e-5)
        candidate = accepted & ~reject
        empty = candidate.sum(axis=0) == 0
        candidate[:, empty] = accepted[:, empty]
        accepted = candidate
    mean = safe_divide(np.sum(stack * accepted, axis=0), accepted.sum(axis=0))
    rejected = 1 - accepted.mean(axis=(0, 3))
    return mean, rejected.astype(np.float32), accepted.mean(axis=3).astype(np.float32)


def denoise_stack(prepared, settings):
    source = prepared['images']
    images = [srgb_decode(image) for image in source] if settings['linear'] else source
    stack = np.stack(images)
    valid = np.stack(prepared['validities'])
    weights = valid * np.asarray([f['weight'] for f in prepared['frames']])[:, None, None]
    rejected = np.zeros_like(valid[0])
    method = settings['method']
    if method == 'sigma':
        image, rejected, accepted = sigma_clip(stack, weights, settings['sigma'], settings['iterations'])
        weights = accepted * weights
    elif method == 'median':
        masked = np.ma.array(stack, mask=np.broadcast_to((valid < .5)[:, :, :, None], stack.shape))
        image = np.ma.median(masked, axis=0).filled(0).astype(np.float32)
    elif method == 'trimmed':
        masked = np.where(valid[:, :, :, None] > .5, stack, np.nan)
        lo = np.nanquantile(masked, settings['trim'], axis=0)
        hi = np.nanquantile(masked, 1 - settings['trim'], axis=0)
        accepted = (stack >= lo) & (stack <= hi) & (valid[:, :, :, None] > .5)
        image = safe_divide((stack * accepted).sum(axis=0), accepted.sum(axis=0))
        rejected = 1 - accepted.mean(axis=(0, 3))
        weights = accepted.mean(axis=3) * weights
    else:
        if method == 'mean':
            weights = valid
        image = weighted_rgb(images, normalize_weights(weights))
    variance = np.sum((stack - image[None]) ** 2 * valid[:, :, :, None], axis=0)
    variance = safe_divide(variance, valid.sum(axis=0)[:, :, None])
    if settings['linear']:
        image = srgb_encode(image)
    coverage = valid.max(axis=0)
    image[coverage < .001] = 0
    effective = safe_divide(np.sum(weights, axis=0) ** 2, np.sum(weights ** 2, axis=0))
    fields = {'image': np.clip(image, 0, 1), 'valid': coverage, 'variance': variance.mean(axis=2), 'rejection': rejected, 'effective_frames': effective / len(images)}
    report = {'mode': 'denoise', 'method': method, 'linear_space': settings['linear'], 'mean_effective_frames': float(effective.mean()), 'ideal_noise_reduction_factor': float(np.sqrt(np.maximum(effective.mean(), 1))), 'mean_rejected': float(rejected.mean()), 'note': '理论降噪因子假设独立同分布噪声，配准误差、压缩与运动会降低实际收益。'}
    return fields, report
