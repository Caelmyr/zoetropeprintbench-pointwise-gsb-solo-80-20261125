import numpy as np
from . import validation as v
from .arrays import luminance, srgb_decode, srgb_encode, safe_divide


def settings(body):
    body = v.object_value(body, '轨迹合成')
    return {
        'method': v.choice(body.get('method', 'lighten'), '轨迹混合', ['lighten', 'screen', 'average', 'darken']),
        'fade': v.number(body.get('fade', 0), '序列渐隐', 0, .95),
        'threshold': v.number(body.get('threshold', .05), '轨迹亮度阈值', 0, .8),
        'background': v.choice(body.get('background', 'median'), '背景来源', ['reference', 'median', 'black']),
    }


def combine(prepared, config):
    stack = np.stack([srgb_decode(image) for image in prepared['images']])
    valid = np.stack(prepared['validities'])
    reference = prepared['reference_index']
    if config['background'] == 'black':
        base = np.zeros_like(stack[0])
    elif config['background'] == 'median':
        base = np.median(stack, axis=0)
    else:
        base = stack[reference].copy()
    result = base.copy()
    counts = np.zeros_like(valid[0])
    for index, image in enumerate(stack):
        fade = (1 - config['fade']) ** (len(stack) - index - 1)
        bright = luminance(image) - luminance(base)
        selected = (bright > config['threshold']).astype(np.float32) * valid[index] * fade
        if config['method'] == 'lighten':
            target = np.maximum(result, image)
        elif config['method'] == 'screen':
            target = 1 - (1 - result) * (1 - image)
        elif config['method'] == 'darken':
            selected = (bright < -config['threshold']).astype(np.float32) * valid[index] * fade
            target = np.minimum(result, image)
        else:
            target = (result * index + image) / (index + 1)
            selected = valid[index] * fade
        result = result * (1 - selected[:, :, None]) + target * selected[:, :, None]
        counts += selected
    image = np.clip(srgb_encode(result), 0, 1)
    coverage = valid.max(axis=0)
    image[coverage < .001] = 0
    fields = {'image': image, 'valid': coverage, 'trail_density': counts / max(len(stack), 1)}
    return fields, {'mode': 'trails', 'method': config['method'], 'fade': config['fade'], 'note': '明亮/暗色轨迹按帧顺序合成；星轨通常保持相机固定并关闭跟星配准。'}
