import cv2
import numpy as np
from .arrays import luminance, normalize_weights, weighted_rgb
from .pyramids import multiband
from .ghosts import suppress_ghosts, rejection_statistics


def contrast_weight(image):
    gray = luminance(image)
    return np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)) + 1e-4


def saturation_weight(image):
    return np.std(image, axis=2) + 1e-4


def exposure_weight(image, sigma=.22):
    value = np.exp(-.5 * ((image - .5) / sigma) ** 2)
    return np.prod(value, axis=2) + 1e-8


def calculate_weights(images, validities, frame_weights, settings):
    weights = []
    for image, valid, frame_weight in zip(images, validities, frame_weights):
        value = contrast_weight(image) ** settings['contrast']
        value *= saturation_weight(image) ** settings['saturation']
        value *= exposure_weight(image, settings['sigma']) ** settings['exposedness']
        value *= valid * frame_weight
        weights.append(value)
    normalized = normalize_weights(weights)
    normalized *= np.stack(validities)
    return normalize_weights(normalized)


def fuse(prepared, settings):
    images, validities = prepared['images'], prepared['validities']
    weights = calculate_weights(images, validities, [f['weight'] for f in prepared['frames']], settings)
    weights, ghost = suppress_ghosts(weights, images, validities, settings['ghost'], settings['ghost_threshold'], prepared['reference_index'])
    image = multiband(images, weights, settings['levels'], validities)
    valid = np.max(validities, axis=0)
    image[valid < .001] = 0
    contribution = np.argmax(weights, axis=0).astype(np.float32) / max(len(images) - 1, 1)
    entropy = -np.sum(weights * np.log2(np.maximum(weights, 1e-8)), axis=0) / max(np.log2(max(len(images), 2)), 1)
    return {'image': np.clip(image, 0, 1), 'valid': valid, 'contribution': contribution, 'weight_entropy': entropy.astype(np.float32), 'ghost': ghost, 'weights': weights}, {'mode': 'fusion', 'ghost': rejection_statistics(ghost), 'mean_weights': [float(w.mean()) for w in weights], 'algorithm': '对比度/饱和度/中灰权重 + 拉普拉斯多尺度曝光融合'}
