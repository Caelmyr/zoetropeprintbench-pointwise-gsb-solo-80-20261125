import numpy as np
from .arrays import luminance, normalize, safe_divide
from .metadata import exposure_seconds


def temporal_report(prepared):
    stack = np.stack([luminance(image) for image in prepared['images']])
    valid = np.stack(prepared['validities']) > .99
    count = valid.sum(axis=0)
    mean = safe_divide((stack * valid).sum(axis=0), count)
    variance = safe_divide(((stack - mean[None]) ** 2 * valid).sum(axis=0), count)
    common = np.all(valid, axis=0)
    frame_means = []
    for index, frame in enumerate(prepared['frames']):
        gray = stack[index]
        selected = gray[common]
        frame_means.append({'id': frame['id'], 'name': frame['name'], 'mean': float(selected.mean()) if selected.size else 0, 'median': float(np.median(selected)) if selected.size else 0, 'exposure': exposure_seconds(frame)})
    matrix = np.eye(len(stack), dtype=np.float32)
    for i in range(len(stack)):
        for j in range(i + 1, len(stack)):
            selected = valid[i] & valid[j]
            a, b = stack[i][selected], stack[j][selected]
            if len(a) > 1 and a.std() > 1e-7 and b.std() > 1e-7:
                correlation = np.mean((a - a.mean()) * (b - b.mean())) / (a.std() * b.std())
            else:
                correlation = 0
            matrix[i, j] = matrix[j, i] = correlation
    report = {'frames': frame_means, 'correlation_matrix': matrix.tolist(), 'common_fraction': float(common.mean()), 'mean_temporal_variance': float(variance[common].mean()) if common.any() else 0, 'variance_p95': float(np.percentile(variance[common], 95)) if common.any() else 0, 'note': '时域差异同时包含曝光、运动、配准误差和噪声，不能全部解释为随机噪声。'}
    return report, {'temporal_mean': mean, 'temporal_variance': normalize(variance, 0, 99), 'coverage': count.astype(np.float32) / len(stack)}


def pixel_trace(prepared, point, radius=2):
    width, height = prepared['size']
    x = min(width - 1, max(0, round(point[0] * (width - 1))))
    y = min(height - 1, max(0, round(point[1] * (height - 1))))
    x0, x1 = max(0, x - radius), min(width, x + radius + 1)
    y0, y1 = max(0, y - radius), min(height, y + radius + 1)
    result = []
    for frame, image, valid in zip(prepared['frames'], prepared['images'], prepared['validities']):
        values = image[y0:y1, x0:x1]
        result.append({'frame_id': frame['id'], 'name': frame['name'], 'rgb_mean': values.mean(axis=(0, 1)).tolist(), 'rgb_std': values.std(axis=(0, 1)).tolist(), 'support_fraction': float(valid[y0:y1, x0:x1].mean()), 'effective_exposure': exposure_seconds(frame)})
    return {'pixel': [x, y], 'bounds': [x0, y0, x1, y1], 'frames': result}
