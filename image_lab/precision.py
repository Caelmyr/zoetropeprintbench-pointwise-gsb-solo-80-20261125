import cv2
import numpy as np
from .codecs import png_bytes, tiff_bytes, radiance_bytes
from .arrays import luminance


def quantization_report(image, bits):
    encoded = png_bytes(image, bits)
    decoded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded.ndim == 3:
        decoded = decoded[:, :, ::-1]
    maximum = 65535 if bits == 16 else 255
    decoded = decoded.astype(np.float32) / maximum
    error = np.abs(decoded - image)
    return {'bits': bits, 'encoded_bytes': len(encoded), 'maximum_error': float(error.max()), 'mean_error': float(error.mean()), 'half_step': .5 / maximum, 'within_half_step': bool(error.max() <= .5 / maximum + 2e-7)}


def hdr_roundtrip(linear):
    raw = radiance_bytes(linear)
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)[:, :, ::-1]
    absolute = np.abs(decoded - linear)
    relevant = linear > .01
    relative = absolute[relevant] / linear[relevant] if relevant.any() else np.array([0])
    return {'format': 'RGBE', 'bytes': len(raw), 'maximum_absolute_error': float(absolute.max()), 'mean_relative_error_above_0_01': float(relative.mean()), 'relative_error_p95': float(np.percentile(relative, 95)), 'note': 'RGBE共享指数导致量化；NPZ保持float32原始数值。'}


def export_preflight(fields, meta):
    checks, warnings = [], []
    h, w = fields['image'].shape[:2]
    for name, field in fields.items():
        finite = bool(np.isfinite(field).all())
        shape_ok = field.shape[:2] == (h, w)
        checks.append({'name': name, 'finite': finite, 'same_size': shape_ok, 'dtype': str(field.dtype), 'bytes': field.nbytes})
    valid = fields['valid']
    coverage = float(np.mean(valid > .99))
    if coverage < .95:
        warnings.append('输出包含无效或低权重区域，建议检查有效覆盖图')
    if np.max(fields['linear']) > 1:
        warnings.append('线性辐射数据超过1，普通显示PNG不能完整保留HDR动态范围')
    gray = luminance(fields['image'])
    if np.mean(gray > .996) > .02:
        warnings.append('显示结果存在超过2%的高光截断')
    if np.mean(gray < .004) > .02:
        warnings.append('显示结果存在超过2%的暗部截断')
    quantization = [quantization_report(fields['image'], bits) for bits in (8, 16)]
    return {'ready': all(c['finite'] and c['same_size'] for c in checks), 'result_id': meta['id'], 'size': [w, h], 'checks': checks, 'warnings': warnings, 'quantization': quantization, 'hdr_roundtrip': hdr_roundtrip(fields['linear']), 'full_coverage_fraction': coverage}
