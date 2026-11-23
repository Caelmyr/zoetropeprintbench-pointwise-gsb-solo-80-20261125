import cv2
import numpy as np
from .arrays import normalize_weights, safe_divide


def level_count(shape, desired):
    limit = int(np.floor(np.log2(max(2, min(shape[:2])))))
    return max(1, min(desired, limit))


def gaussian_pyramid(image, levels):
    result = [image.astype(np.float32)]
    for _ in range(1, level_count(image.shape, levels)):
        result.append(cv2.pyrDown(result[-1]))
    return result


def laplacian_pyramid(image, levels):
    gaussian = gaussian_pyramid(image, levels)
    result = []
    for first, second in zip(gaussian, gaussian[1:]):
        up = cv2.pyrUp(second, dstsize=(first.shape[1], first.shape[0]))
        result.append(first - up)
    result.append(gaussian[-1])
    return result


def reconstruct(pyramid):
    current = pyramid[-1].copy()
    for level in reversed(pyramid[:-1]):
        current = cv2.pyrUp(current, dstsize=(level.shape[1], level.shape[0])) + level
    return current.astype(np.float32)


def multiband(images, weights, levels=5, validity=None):
    count = level_count(images[0].shape, levels)
    normalized = normalize_weights(weights)
    image_pyramids = []
    weight_pyramids = []
    for image, weight in zip(images, normalized):
        if validity is not None:
            index = len(image_pyramids)
            valid = validity[index]
            support = cv2.GaussianBlur(valid, (0, 0), 1)
            numerator = cv2.GaussianBlur(image * valid[:, :, None], (0, 0), 1)
            extended = safe_divide(numerator, support[:, :, None])
            image = np.where(valid[:, :, None] > .01, image, extended)
        image_pyramids.append(laplacian_pyramid(image, count))
        weight_pyramids.append(gaussian_pyramid(weight, count))
    fused = []
    for level in range(count):
        maps = normalize_weights([p[level] for p in weight_pyramids])
        value = np.zeros_like(image_pyramids[0][level])
        for image, weight in zip(image_pyramids, maps):
            value += image[level] * (weight[:, :, None] if value.ndim == 3 else weight)
        fused.append(value)
    result = reconstruct(fused)
    coverage = np.sum(weights, axis=0) > 1e-8
    result[~coverage] = 0
    return result


def pyramid_energy(image, levels=6):
    result = []
    for index, level in enumerate(laplacian_pyramid(image, levels)):
        result.append({'level': index, 'width': level.shape[1], 'height': level.shape[0], 'mean_abs': float(np.mean(np.abs(level))), 'rms': float(np.sqrt(np.mean(level ** 2))), 'maximum_abs': float(np.max(np.abs(level)))})
    return result
