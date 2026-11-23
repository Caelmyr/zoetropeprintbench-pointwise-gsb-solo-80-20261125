import numpy as np
import cv2
from .arrays import srgb_decode, srgb_encode, luminance
from . import validation as v


def patch_sample(image, bounds, inset=.15):
    x0, y0, x1, y1 = v.vector(bounds, '色块区域', 4, 0, 1)
    if x1 <= x0 or y1 <= y0:
        raise v.Invalid('色块区域大小无效')
    width, height = x1 - x0, y1 - y0
    x0, x1 = x0 + width * inset, x1 - width * inset
    y0, y1 = y0 + height * inset, y1 - height * inset
    h, w = image.shape[:2]
    left, right = int(x0 * w), max(int(x0 * w) + 1, int(x1 * w))
    top, bottom = int(y0 * h), max(int(y0 * h) + 1, int(y1 * h))
    patch = image[top:bottom, left:right]
    encoded = np.median(patch, axis=(0, 1))
    linear = np.median(srgb_decode(patch), axis=(0, 1))
    return {'rgb': encoded.tolist(), 'linear': linear.tolist(), 'std': patch.std(axis=(0, 1)).tolist(), 'pixels': patch.shape[0] * patch.shape[1], 'clipped_fraction': float(np.mean((patch < .004) | (patch > .996)))}


def feature_matrix(values, model='matrix'):
    values = np.asarray(values, np.float64)
    if model == 'affine':
        return np.concatenate([values, np.ones((len(values), 1))], axis=1)
    if model == 'polynomial':
        r, g, b = values.T
        return np.stack([r, g, b, r * g, r * b, g * b, r * r, g * g, b * b, np.ones_like(r)], axis=1)
    return values


def fit_color(source, target, model='matrix', regularization=.01):
    source = np.asarray(source, np.float64)
    target = np.asarray(target, np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise v.Invalid('色彩校正需要相同数量的RGB输入与目标色块')
    design = feature_matrix(source, model)
    if len(source) < design.shape[1]:
        raise v.Invalid('色块数量不足以求解当前校正模型')
    identity = np.zeros((design.shape[1], 3))
    identity[:3] = np.eye(3)
    penalty = np.sqrt(regularization)
    augmented = np.concatenate([design, np.eye(design.shape[1]) * penalty])
    targets = np.concatenate([target, identity * penalty])
    coefficients, residuals, rank, singular = np.linalg.lstsq(augmented, targets, rcond=None)
    predicted = design @ coefficients
    return {'model': model, 'coefficients': coefficients.tolist(), 'rmse_linear': float(np.sqrt(np.mean((predicted - target) ** 2))), 'rank': int(rank), 'condition': float(singular[0] / max(singular[-1], 1e-12)), 'predicted': predicted.tolist()}


def apply_color(image, calibration):
    linear = srgb_decode(image)
    h, w = image.shape[:2]
    flat = linear.reshape(-1, 3)
    result = np.empty_like(flat)
    coefficients = np.asarray(calibration['coefficients'], np.float64)
    for start in range(0, len(flat), 200000):
        features = feature_matrix(flat[start:start + 200000], calibration['model'])
        result[start:start + 200000] = features @ coefficients
    return np.clip(srgb_encode(result.reshape(h, w, 3)), 0, 1)


def color_error(predicted, target):
    a = np.asarray(predicted, np.float32)[None]
    b = np.asarray(target, np.float32)[None]
    lab_a = cv2.cvtColor(np.clip(a, 0, 1), cv2.COLOR_RGB2LAB)[0]
    lab_b = cv2.cvtColor(np.clip(b, 0, 1), cv2.COLOR_RGB2LAB)[0]
    delta = np.linalg.norm(lab_a - lab_b, axis=1)
    return {'delta_e76': delta.tolist(), 'mean': float(delta.mean()), 'maximum': float(delta.max()), 'note': '数值依赖目标色块、照明和输入色彩空间，非色度计标定。'}


def calibrate_patches(image, body):
    model = v.choice(body.get('model', 'matrix'), '色彩模型', ['matrix', 'affine', 'polynomial'])
    patches = v.list_value(body.get('patches'), '色块', 48)
    source, targets, reports = [], [], []
    for patch in patches:
        patch = v.object_value(patch, '色块')
        report = patch_sample(image, patch.get('bounds'))
        source.append(report['linear'])
        target = np.asarray(v.color(patch.get('target')), np.float32)[None, None]
        targets.append(srgb_decode(target)[0, 0].tolist())
        reports.append(report)
    regularization = v.number(body.get('regularization', .01), '正则强度', 0, 10)
    fitted = fit_color(source, targets, model, regularization)
    predicted = srgb_encode(np.asarray(fitted['predicted'], np.float32))
    target_encoded = srgb_encode(np.asarray(targets, np.float32))
    fitted['error'] = color_error(predicted, target_encoded)
    fitted['patches'] = reports
    return fitted


def cube_lut(calibration, size=17):
    size = v.integer(size, 'LUT边长', 5, 33)
    values = np.linspace(0, 1, size, dtype=np.float32)
    blue, green, red = np.meshgrid(values, values, values, indexing='ij')
    source = np.stack([red, green, blue], axis=3).reshape(1, -1, 3)
    mapped = apply_color(source, calibration).reshape(-1, 3)
    lines = ['TITLE "ScanWeave Color Calibration"', 'LUT_3D_SIZE ' + str(size), 'DOMAIN_MIN 0.0 0.0 0.0', 'DOMAIN_MAX 1.0 1.0 1.0']
    for r, g, b in mapped:
        lines.append(f'{r:.7f} {g:.7f} {b:.7f}')
    return ('\n'.join(lines) + '\n').encode()
