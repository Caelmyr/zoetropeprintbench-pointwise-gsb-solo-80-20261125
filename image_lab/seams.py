import cv2
import numpy as np
from scipy import ndimage as ndi
from .arrays import luminance, gradients


def seam_cost(first, second):
    a, b = luminance(first), luminance(second)
    dx_a, dy_a = gradients(a)
    dx_b, dy_b = gradients(b)
    color = np.mean(np.abs(first - second), axis=2)
    gradient = np.abs(dx_a - dx_b) + np.abs(dy_a - dy_b)
    return (color + gradient * .5 + .001).astype(np.float32)


def vertical_path(cost, support):
    h, w = cost.shape
    current = np.where(support[0], cost[0], 1e6).astype(np.float64)
    backtrack = np.zeros((h, w), np.int8)
    for row in range(1, h):
        left = np.pad(current[:-1], (1, 0), constant_values=1e8)
        right = np.pad(current[1:], (0, 1), constant_values=1e8)
        candidates = np.stack([left, current, right])
        choice = np.argmin(candidates, axis=0)
        current = candidates[choice, np.arange(w)] + np.where(support[row], cost[row], 1e6)
        backtrack[row] = choice.astype(np.int8) - 1
    x = int(np.argmin(current))
    result = np.zeros(h, np.int32)
    result[-1] = x
    for row in range(h - 1, 0, -1):
        x = int(np.clip(x + backtrack[row, x], 0, w - 1))
        result[row - 1] = x
    return result, float(current[result[-1]])


def seam_selector(first, second, first_mask, second_mask, feather=12):
    overlap = (first_mask > .99) & (second_mask > .99)
    yy, xx = np.where(overlap)
    if not len(xx):
        return (second_mask > 0).astype(np.float32), {'overlap': 0, 'seam': False}
    x0, x1, y0, y1 = xx.min(), xx.max() + 1, yy.min(), yy.max() + 1
    bounds_cost = seam_cost(first[y0:y1, x0:x1], second[y0:y1, x0:x1])
    support = overlap[y0:y1, x0:x1]
    horizontal = (x1 - x0) > (y1 - y0)
    cost = bounds_cost.T if horizontal else bounds_cost
    valid = support.T if horizontal else support
    path, energy = vertical_path(cost, valid)
    selector = np.zeros_like(first_mask)
    if horizontal:
        center_first = np.mean(np.where(first_mask > .5)[0])
        center_second = np.mean(np.where(second_mask > .5)[0])
        for index, cut in enumerate(path):
            x = x0 + index
            if center_second >= center_first:
                selector[y0 + cut:, x] = 1
            else:
                selector[:y0 + cut + 1, x] = 1
    else:
        center_first = np.mean(np.where(first_mask > .5)[1])
        center_second = np.mean(np.where(second_mask > .5)[1])
        for index, cut in enumerate(path):
            y = y0 + index
            if center_second >= center_first:
                selector[y, x0 + cut:] = 1
            else:
                selector[y, :x0 + cut + 1] = 1
    if feather > 0:
        selector = ndi.gaussian_filter(selector, feather / 3)
    selector = np.where(first_mask < .01, 1, selector)
    selector = np.where(second_mask < .01, 0, selector)
    return selector.astype(np.float32), {'overlap': int(overlap.sum()), 'seam': True, 'orientation': 'horizontal' if horizontal else 'vertical', 'energy': energy, 'path_length': len(path)}


def seam_blend(images, masks, feather=12):
    image = images[0].copy()
    support = masks[0].copy()
    reports = []
    for current, valid in zip(images[1:], masks[1:]):
        selector, report = seam_selector(image, current, support, valid, feather)
        image = image * (1 - selector[:, :, None]) + current * selector[:, :, None]
        support = np.maximum(support, valid)
        reports.append(report)
    return image, reports
