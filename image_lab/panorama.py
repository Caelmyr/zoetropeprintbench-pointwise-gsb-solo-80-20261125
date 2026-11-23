import numpy as np
from .geometry import canvas_bounds, warp, feather_weight, support_bounds, largest_rectangle, crop_arrays
from .arrays import luminance, normalize_weights, weighted_rgb, safe_divide
from .pyramids import multiband
from .validation import Invalid


def estimate_gains(images, validities, reference=0):
    count = len(images)
    equations, targets = [], []
    for first in range(count):
        for second in range(first + 1, count):
            overlap = (validities[first] > .99) & (validities[second] > .99)
            a, b = luminance(images[first]), luminance(images[second])
            overlap &= (a > .03) & (a < .95) & (b > .03) & (b < .95)
            if overlap.sum() < 80:
                continue
            ratio = np.median(np.log(a[overlap]) - np.log(b[overlap]))
            equation = np.zeros(count)
            equation[first], equation[second] = 1, -1
            equations.append(equation)
            targets.append(-ratio)
    anchor = np.zeros(count)
    anchor[reference] = 10
    equations.append(anchor)
    targets.append(0)
    for index in range(count):
        regularizer = np.zeros(count)
        regularizer[index] = .01
        equations.append(regularizer)
        targets.append(0)
    logarithms, _, _, _ = np.linalg.lstsq(np.stack(equations), np.asarray(targets), rcond=None)
    return np.clip(np.exp(logarithms), .25, 4).astype(np.float32)


def stitch(prepared, settings, progress=None, cancelled=None):
    width, height = prepared['size']
    size, matrices, canvas = canvas_bounds(prepared['matrices'], width, height, settings['max_edge'])
    warped, masks = [], []
    for index, (image, valid, matrix) in enumerate(zip(prepared['images'], prepared['validities'], matrices)):
        if cancelled and cancelled():
            raise InterruptedError('拼接已取消')
        warped.append(warp(image, matrix, size))
        masks.append(warp(valid, matrix, size))
        if progress:
            progress(.4 + .2 * (index + 1) / len(matrices), '构建拼接画布')
    gains = estimate_gains(warped, masks, prepared['reference_index']) if settings['gain'] else np.ones(len(warped))
    warped = [np.clip(image * gain, 0, 1) for image, gain in zip(warped, gains)]
    weights = [feather_weight(mask, settings['feather']) * frame['weight'] for mask, frame in zip(masks, prepared['frames'])]
    seam_reports = []
    if settings['blend'] == 'seam':
        from .seams import seam_blend
        image, seam_reports = seam_blend(warped, masks, min(40, settings['feather']))
    elif settings['blend'] == 'overwrite':
        image = np.zeros_like(warped[0])
        for field, mask in zip(warped, masks):
            image[mask > .5] = field[mask > .5]
    elif settings['blend'] == 'multiband':
        image = multiband(warped, weights, settings['levels'], masks)
    else:
        image = weighted_rgb(warped, normalize_weights(weights))
    valid = np.max(masks, axis=0)
    normalized = normalize_weights(weights)
    contributions = np.argmax(normalized, axis=0).astype(np.float32) / max(len(warped) - 1, 1)
    coverage = np.sum(np.stack(masks) > .99, axis=0).astype(np.float32) / len(masks)
    fields = {'image': np.clip(image, 0, 1), 'valid': valid, 'contribution': contributions, 'coverage': coverage}
    bounds = [0, 0, size[0], size[1]]
    if settings['crop'] == 'bounds':
        bounds = support_bounds(valid)
    elif settings['crop'] == 'largest_rectangle':
        bounds = largest_rectangle(valid)
    fields = crop_arrays(fields, bounds)
    report = {'mode': 'panorama', 'canvas': canvas, 'original_canvas': list(size), 'crop': bounds, 'gain_compensation': gains.tolist(), 'blend': settings['blend'], 'note': '采用平面单应/仿射画布，不是球面全景；近景视差和滚动快门需要额外拍摄控制。'}
    report['seams'] = seam_reports
    return fields, report
