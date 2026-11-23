import numpy as np
import cv2
from scipy import ndimage as ndi
from .validation import Invalid


def finite_rgb(image):
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 1:
        raise Invalid('图像需要H×W×3格式')
    if not np.isfinite(image).all():
        raise Invalid('图像包含非有限值')
    return image


def finite_mask(mask, shape=None):
    mask = np.asarray(mask, dtype=np.float32)
    if mask.ndim != 2 or (shape and mask.shape != shape):
        raise Invalid('遮罩尺寸错误')
    if not np.isfinite(mask).all():
        raise Invalid('遮罩包含无效值')
    return np.clip(mask, 0, 1)


def luminance(image):
    return (image[:, :, 0] * .2126 + image[:, :, 1] * .7152 + image[:, :, 2] * .0722).astype(np.float32)


def srgb_decode(image):
    image = np.maximum(image, 0)
    return np.where(image <= .04045, image / 12.92, ((image + .055) / 1.055) ** 2.4).astype(np.float32)


def srgb_encode(image):
    image = np.maximum(image, 0)
    return np.where(image <= .0031308, image * 12.92, 1.055 * image ** (1 / 2.4) - .055).astype(np.float32)


def safe_divide(a, b, default=0):
    a, b = np.broadcast_arrays(a, b)
    return np.divide(a, b, out=np.full(a.shape, default, dtype=np.float32), where=np.abs(b) > 1e-8)


def normalize(image, low=0, high=100):
    a, b = np.percentile(image, [low, high])
    if b - a < 1e-8:
        return np.zeros_like(image, dtype=np.float32)
    return np.clip((image - a) / (b - a), 0, 1).astype(np.float32)


def resize(image, width, height, down=True):
    if image.shape[:2] == (height, width):
        return image.copy()
    interpolation = cv2.INTER_AREA if down and (width < image.shape[1] or height < image.shape[0]) else cv2.INTER_LINEAR
    return cv2.resize(image, (int(width), int(height)), interpolation=interpolation).astype(np.float32)


def fit_size(shape, maximum):
    h, w = shape[:2]
    ratio = min(1., maximum / max(w, h))
    return max(1, round(w * ratio)), max(1, round(h * ratio))


def letterbox(image, width, height):
    source_h, source_w = image.shape[:2]
    ratio = min(width / source_w, height / source_h)
    w, h = max(1, round(source_w * ratio)), max(1, round(source_h * ratio))
    x, y = (width - w) // 2, (height - h) // 2
    channels = () if image.ndim == 2 else (image.shape[2],)
    result = np.zeros((height, width) + channels, dtype=np.float32)
    valid = np.zeros((height, width), dtype=np.float32)
    result[y:y + h, x:x + w] = resize(image, w, h)
    valid[y:y + h, x:x + w] = 1
    return result, valid


def blur(image, sigma):
    if sigma <= 0:
        return image.copy()
    return ndi.gaussian_filter(image, (sigma, sigma, 0) if image.ndim == 3 else sigma, mode='reflect').astype(np.float32)


def gradients(image):
    gray = luminance(image) if image.ndim == 3 else image
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 8
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) / 8
    return dx, dy


def smoothstep(low, high, image):
    value = np.clip((image - low) / max(high - low, 1e-7), 0, 1)
    return value * value * (3 - 2 * value)


def normalize_weights(weights, fallback=None):
    weights = np.maximum(np.asarray(weights, dtype=np.float32), 0)
    total = weights.sum(axis=0)
    result = safe_divide(weights, total[None])
    missing = total <= 1e-8
    if np.any(missing):
        if fallback is None:
            result[:, missing] = 1 / len(weights)
        else:
            result[:, missing] = 0
            result[fallback, missing] = 1
    return result


def weighted_rgb(images, weights):
    result = np.zeros_like(images[0], dtype=np.float32)
    for image, weight in zip(images, weights):
        result += image * weight[:, :, None]
    return result


def geometric_mean(values, valid=None):
    logarithm = np.log(np.maximum(values, 1e-6))
    if valid is not None:
        selected = logarithm[valid > .5]
        if not selected.size:
            return 0.
        return float(np.exp(selected.mean()))
    return float(np.exp(logarithm.mean()))


def checkerboard(shape, cell=16):
    y, x = np.indices(shape[:2])
    gray = np.where(((x // cell + y // cell) % 2) == 0, .16, .22).astype(np.float32)
    return np.repeat(gray[:, :, None], 3, axis=2)


def composite_valid(image, valid):
    alpha = np.clip(valid, 0, 1)[:, :, None]
    return np.clip(image, 0, 1) * alpha + checkerboard(image.shape) * (1 - alpha)
