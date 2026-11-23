import numpy as np
from . import validation as v
from .arrays import srgb_decode, srgb_encode, safe_divide


def settings(body):
    body = v.object_value(body, '亚像素重建')
    return {
        'scale': v.integer(body.get('scale', 2), '重建倍率', 1, 3),
        'linear': v.boolean(body.get('linear', True), '线性空间重建'),
    }


def deposit(image, valid, matrix, width, height, scale, sum_rgb, sum_weight, frame_weight):
    source_h, source_w = image.shape[:2]
    yy, xx = np.indices((source_h, source_w), dtype=np.float64)
    coordinates = np.stack([xx.ravel(), yy.ravel(), np.ones(source_h * source_w)])
    transformed = matrix @ coordinates
    denominator = transformed[2]
    x = transformed[0] / np.where(np.abs(denominator) > 1e-8, denominator, 1e-8) * scale
    y = transformed[1] / np.where(np.abs(denominator) > 1e-8, denominator, 1e-8) * scale
    x += (scale - 1) / 2
    y += (scale - 1) / 2
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    colors = image.reshape(-1, 3)
    raw_weight = valid.ravel() * frame_weight
    for dx, dy, fraction in [(0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)]:
        px, py = x0 + dx, y0 + dy
        selected = (px >= 0) & (px < width) & (py >= 0) & (py < height) & (raw_weight > .001)
        weights = raw_weight[selected] * fraction[selected]
        indices = (py[selected], px[selected])
        np.add.at(sum_weight, indices, weights)
        for channel in range(3):
            np.add.at(sum_rgb[:, :, channel], indices, colors[selected, channel] * weights)


def reconstruct(prepared, config, progress=None, cancelled=None):
    scale = config['scale']
    width, height = prepared['size']
    width, height = width * scale, height * scale
    if width * height > 12000000:
        raise v.Invalid('亚像素重建输出超过1200万像素')
    rgb = np.zeros((height, width, 3), np.float32)
    weights = np.zeros((height, width), np.float32)
    for index, (image, valid, matrix, frame) in enumerate(zip(prepared['images'], prepared['validities'], prepared['matrices'], prepared['frames'])):
        if cancelled and cancelled():
            raise InterruptedError('亚像素重建取消')
        source = srgb_decode(image) if config['linear'] else image
        deposit(source, valid, matrix, width, height, scale, rgb, weights, frame['weight'])
        if progress:
            progress(.4 + .35 * (index + 1) / len(prepared['images']), '亚像素投影累积')
    image = safe_divide(rgb, weights[:, :, None])
    if config['linear']:
        image = srgb_encode(image)
    valid = (weights > .001).astype(np.float32)
    from .arrays import blur
    unsupported = valid < .5
    if np.any(unsupported):
        interpolation = safe_divide(blur(image * valid[:, :, None], 1), blur(valid, 1)[:, :, None])
        image[unsupported] = interpolation[unsupported]
    fields = {'image': np.clip(image, 0, 1), 'valid': valid, 'sample_density': np.clip(weights / max(float(weights.max()), 1), 0, 1)}
    report = {'mode': 'drizzle', 'scale': scale, 'sampled_fraction': float(valid.mean()), 'mean_sample_weight': float(weights.mean()), 'note': '原生工作像素前向投影与双线性沉积；需要真实亚像素位移，不能保证获得倍率对应的有效分辨率。孔洞插值只用于预览。'}
    return fields, report
