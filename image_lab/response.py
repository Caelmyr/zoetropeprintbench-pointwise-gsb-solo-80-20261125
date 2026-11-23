import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import lsqr
from .arrays import luminance
from .metadata import exposure_seconds
from . import validation as v


def triangle(index):
    value = np.asarray(index, dtype=np.float64)
    return np.minimum(value, 255 - value) / 127.5


def sample_locations(images, validities, count=96):
    h, w = images[0].shape[:2]
    gray = np.mean([luminance(image) for image in images], axis=0)
    valid = np.min(validities, axis=0) > .99
    yy, xx = np.where(valid & (gray > .03) & (gray < .97))
    if len(xx) < 24:
        raise v.Invalid('响应标定缺少稳定的共同有效像素')
    brightness = gray[yy, xx]
    order = np.argsort(brightness)
    indices = order[np.linspace(0, len(order) - 1, min(count, len(order))).astype(int)]
    return yy[indices], xx[indices]


def solve_channel(samples, log_times, smoothness=30):
    frame_count, sample_count = samples.shape
    rows, cols, values, targets = [], [], [], []
    row_index = 0
    for sample in range(sample_count):
        for frame in range(frame_count):
            intensity = int(samples[frame, sample])
            weight = float(triangle(intensity))
            if weight <= 0:
                continue
            rows.extend([row_index, row_index])
            cols.extend([intensity, 256 + sample])
            values.extend([weight, -weight])
            targets.append(weight * log_times[frame])
            row_index += 1
    rows.append(row_index)
    cols.append(128)
    values.append(1.)
    targets.append(0.)
    row_index += 1
    for intensity in range(1, 255):
        weight = smoothness * float(triangle(intensity))
        rows.extend([row_index] * 3)
        cols.extend([intensity - 1, intensity, intensity + 1])
        values.extend([weight, -2 * weight, weight])
        targets.append(0.)
        row_index += 1
    matrix = coo_matrix((values, (rows, cols)), shape=(row_index, 256 + sample_count)).tocsr()
    solution = lsqr(matrix, np.asarray(targets), atol=1e-6, btol=1e-6, iter_lim=1200)
    curve = solution[0][:256]
    monotonic = np.maximum.accumulate(curve)
    monotonic -= monotonic[128]
    return monotonic.astype(np.float32), {'iterations': int(solution[2]), 'residual_norm': float(solution[3]), 'condition_estimate': float(solution[6]), 'constraints': row_index, 'samples': sample_count}


def estimate_response(prepared, count=96, smoothness=30):
    images = prepared['images']
    if len(images) < 3:
        raise v.Invalid('响应曲线估计至少需要3张不同曝光、已配准照片')
    times = np.asarray([exposure_seconds(frame) for frame in prepared['frames']])
    if times.max() / times.min() < 2:
        raise v.Invalid('响应曲线估计需要至少一档曝光跨度')
    yy, xx = sample_locations(images, prepared['validities'], count)
    times /= np.median(times)
    encoded = np.rint(np.stack(images)[:, yy, xx] * 255).astype(np.uint8)
    curves, reports = [], []
    for channel in range(3):
        curve, report = solve_channel(encoded[:, :, channel], np.log(times), smoothness)
        curves.append(curve)
        reports.append(report)
    return {'log_response': np.stack(curves).tolist(), 'reports': reports, 'samples': len(xx), 'note': '以g(128)=0固定相对尺度，平滑最小二乘并施加单调性；曝光、配准和无运动假设不成立时估计可能失真。'}


def decode_with_response(image, curves):
    x = np.arange(256, dtype=np.float32)
    result = np.empty_like(image)
    for channel in range(3):
        logarithm = np.interp(image[:, :, channel] * 255, x, curves[channel])
        result[:, :, channel] = np.exp(np.clip(logarithm, -20, 20))
    return result
