import numpy as np
import cv2
from .arrays import luminance, gradients, blur


def histogram(image, bins=64, mask=None):
    fields = [image] if image.ndim == 2 else [image[:, :, i] for i in range(image.shape[2])]
    result = []
    for field in fields:
        values = field[mask > .5] if mask is not None else field.ravel()
        counts, edges = np.histogram(values, bins=bins, range=(0, 1))
        result.append({'counts': counts.tolist(), 'fraction': (counts / max(counts.sum(), 1)).tolist()})
    return {'bins': bins, 'centers': ((edges[:-1] + edges[1:]) / 2).tolist(), 'channels': result}


def describe(image, mask=None):
    gray = luminance(image) if image.ndim == 3 else image
    values = gray[mask > .5] if mask is not None else gray.ravel()
    if not values.size:
        return {'pixels': 0, 'mean': 0, 'std': 0, 'percentiles': [], 'clip_black': 0, 'clip_white': 0}
    return {'pixels': len(values), 'mean': float(values.mean()), 'std': float(values.std()), 'minimum': float(values.min()), 'maximum': float(values.max()), 'percentiles': np.percentile(values, [1, 5, 25, 50, 75, 95, 99]).tolist(), 'clip_black': float(np.mean(values <= 1 / 255)), 'clip_white': float(np.mean(values >= 254 / 255)), 'entropy_bits': entropy(values)}


def entropy(values, bins=256):
    counts, _ = np.histogram(values, bins=bins, range=(0, 1))
    fractions = counts[counts > 0] / max(counts.sum(), 1)
    return float(-np.sum(fractions * np.log2(fractions)))


def focus_statistics(image):
    gray = luminance(image)
    dx, dy = gradients(gray)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    gradient = np.hypot(dx, dy)
    return {'laplacian_variance': float(laplacian.var()), 'tenengrad_mean': float(np.mean(dx * dx + dy * dy)), 'gradient_p95': float(np.percentile(gradient, 95)), 'edge_fraction': float(np.mean(gradient > .05)), 'modified_laplacian': float(np.mean(np.abs(np.diff(gray, n=2, axis=0))))}


def noise_estimate(image):
    gray = luminance(image)
    dx, dy = gradients(gray)
    flat = np.hypot(dx, dy) < np.percentile(np.hypot(dx, dy), 35)
    residual = gray - blur(gray, 1)
    values = residual[flat]
    median = np.median(values) if values.size else 0
    sigma = np.median(np.abs(values - median)) / .67449 if values.size else 0
    mean = float(gray[flat].mean()) if np.any(flat) else 0
    snr = 20 * np.log10(max(mean, 1e-8) / max(sigma, 1e-8))
    return {'sigma_approx': float(sigma), 'snr_db_approx': float(snr), 'flat_fraction': float(flat.mean()), 'note': '局部平坦区域MAD估计，纹理/去噪/压缩会影响结果，不能替代标定噪声测量。'}


def channel_statistics(image):
    result = []
    for index, label in enumerate(['R', 'G', 'B']):
        field = image[:, :, index]
        result.append({'channel': label, 'mean': float(field.mean()), 'std': float(field.std()), 'clip_low': float(np.mean(field < .004)), 'clip_high': float(np.mean(field > .996))})
    return result


def image_report(image, valid=None):
    return {'luminance': describe(image, valid), 'histogram': histogram(image, mask=valid), 'focus': focus_statistics(image), 'noise': noise_estimate(image), 'channels': channel_statistics(image), 'size': [image.shape[1], image.shape[0]]}


def grid_statistics(image, columns=6, rows=4):
    h, w = image.shape[:2]
    result = []
    for row in range(rows):
        for column in range(columns):
            x0, x1 = round(column * w / columns), round((column + 1) * w / columns)
            y0, y1 = round(row * h / rows), round((row + 1) * h / rows)
            if x1 <= x0 or y1 <= y0:
                continue
            tile = image[y0:y1, x0:x1]
            result.append({'column': column, 'row': row, 'bounds': [x0, y0, x1, y1], 'mean': float(luminance(tile).mean()), 'sharpness': focus_statistics(tile)['laplacian_variance'], 'clip_fraction': float(np.mean(np.max(tile, axis=2) > .996))})
    return {'columns': columns, 'rows': rows, 'cells': result}
