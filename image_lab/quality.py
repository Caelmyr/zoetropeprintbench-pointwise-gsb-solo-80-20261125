import numpy as np
from .arrays import luminance, fit_size, resize
from .statistics import focus_statistics, noise_estimate, describe
from .geometry import pixel_matrix, warp
from .registration import alignment_quality
from . import model


def rank_frames(project, assets):
    result = []
    reference = model.find_frame(project, project['reference']) if project['reference'] else None
    ref_image = None
    if reference:
        original = assets.field(reference['asset'])
        w, h = fit_size(original.shape, min(project['settings']['size'], 512))
        ref_image = resize(original, w, h)
    for frame in project['frames']:
        original = assets.field(frame['asset'])
        w, h = fit_size(original.shape, 512)
        image = resize(original, w, h)
        focus = focus_statistics(image)
        noise = noise_estimate(image)
        brightness = describe(image)
        clipping = brightness['clip_black'] + brightness['clip_white']
        score = np.log1p(focus['laplacian_variance'] * 1000) * max(0, 1 - clipping) / (1 + noise['sigma_approx'] * 10)
        alignment = None
        if ref_image is not None:
            height, width = ref_image.shape[:2]
            current = resize(image, width, height)
            matrix = pixel_matrix(frame['matrix'], width, height)
            alignment = alignment_quality(ref_image, current, matrix)
        result.append({'id': frame['id'], 'name': frame['name'], 'enabled': frame['enabled'], 'score_heuristic': float(score), 'focus': focus, 'noise': noise, 'exposure': brightness, 'alignment': alignment})
    ordered = sorted(result, key=lambda row: row['score_heuristic'], reverse=True)
    return {'frames': ordered, 'suggested_reference': ordered[0]['id'] if ordered else None, 'note': '排序是基于清晰度、截断和噪声的启发式，曝光包围与景深序列不应仅按总分丢弃照片。'}


def stack_preflight(project, assets):
    errors, warnings = [], []
    frames = [f for f in project['frames'] if f['enabled']]
    if not frames:
        errors.append('未启用照片')
    if project['reference'] not in [f['id'] for f in frames]:
        errors.append('参考帧未启用或不存在')
    if frames and all(f['weight'] <= 0 for f in frames):
        errors.append('全部启用帧的权重为零')
    mode = project['settings']['mode']
    if mode in ('fusion', 'hdr', 'focus', 'denoise', 'panorama') and len(frames) < 2:
        warnings.append('当前只有一帧，多帧算法无法增加场景信息')
    if mode == 'hdr':
        times = [f['exposure'] * 2 ** f['ev'] for f in frames]
        if len(times) < 2 or max(times) / max(min(times), 1e-9) < 1.05:
            errors.append('HDR需要不同的有效曝光时间')
    ratios = []
    for frame in frames:
        meta = assets.metadata(frame['asset'])
        ratios.append(meta['width'] / meta['height'])
        if frame['weight'] == 0:
            warnings.append(frame['name'] + '权重为零')
        if frame['mask']:
            meta_mask = assets.metadata(frame['mask'])
            if meta_mask['kind'] != 'mask':
                errors.append(frame['name'] + '遮罩格式错误')
    if ratios and max(ratios) - min(ratios) > .05:
        warnings.append('照片宽高比不同，工作画布将等比缩放并留空，建议先裁齐')
    calibration = project['settings']['calibration']
    if calibration['enabled'] and not calibration['dark'] and not calibration['flat']:
        warnings.append('已启用校正但未选择暗场或平场')
    pixels = project['settings']['size'] ** 2 * len(frames)
    return {'ready': not errors, 'errors': errors, 'warnings': warnings, 'enabled_frames': len(frames), 'frame_pixels_upper': pixels, 'float_stack_bytes_upper': pixels * 3 * 4, 'mode': mode}


def clipping_map(image, low=.004, high=.996):
    low_mask = np.min(image, axis=2) < low
    high_mask = np.max(image, axis=2) > high
    output = image * .3
    output[low_mask] = [.12, .32, 1]
    output[high_mask] = [1, .18, .08]
    return output


def zebra_map(image, threshold=.98):
    gray = luminance(image)
    y, x = np.indices(gray.shape)
    stripes = (x + y) % 12 < 6
    result = image.copy()
    result[(gray >= threshold) & stripes] = [1, .2, .1]
    result[(gray >= threshold) & ~stripes] = [.2, .05, .05]
    return result


def focus_peaking(image, threshold=.04):
    from .arrays import gradients
    dx, dy = gradients(image)
    selected = np.hypot(dx, dy) > threshold
    result = image * .65
    result[selected] = [.2, 1, .6]
    return result
