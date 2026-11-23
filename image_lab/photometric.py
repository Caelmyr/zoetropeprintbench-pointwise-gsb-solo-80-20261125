from copy import deepcopy
import numpy as np
import cv2
from . import model
from . import validation as v
from .arrays import srgb_decode, srgb_encode, luminance, resize


def robust_gain(reference, current, valid=None):
    a, b = luminance(srgb_decode(reference)), luminance(srgb_decode(current))
    selected = (a > .01) & (a < .8) & (b > .01) & (b < .8)
    if valid is not None:
        selected &= valid > .99
    if selected.sum() < 64:
        raise v.Invalid('中间亮度共同像素不足，无法匹配曝光')
    logarithms = np.log(a[selected]) - np.log(b[selected])
    center = np.median(logarithms)
    deviation = np.median(np.abs(logarithms - center))
    retained = np.abs(logarithms - center) <= max(.05, deviation * 3)
    gain = float(np.exp(np.median(logarithms[retained])))
    return {'gain': float(np.clip(gain, .125, 8)), 'raw_gain': gain, 'samples': int(retained.sum()), 'spread_stops': float(deviation / np.log(2)), 'clamped': gain < .125 or gain > 8}


def color_gains(reference, current):
    a, b = srgb_decode(reference), srgb_decode(current)
    selected = (np.min(a, axis=2) > .02) & (np.max(a, axis=2) < .9) & (np.min(b, axis=2) > .02) & (np.max(b, axis=2) < .9)
    if selected.sum() < 64:
        raise v.Invalid('色彩匹配的有效像素不足')
    ratio = np.median(a[selected] / np.maximum(b[selected], .001), axis=0)
    ratio /= max(float(np.mean(ratio)), 1e-6)
    return np.clip(ratio, .5, 2).astype(np.float32)


def match_plan(project, assets, color=False):
    from .preparation import prepare
    prepared = prepare(project, assets)
    reference_index = prepared['reference_index']
    reference = prepared['images'][reference_index]
    result = []
    for index, (image, frame, valid) in enumerate(zip(prepared['images'], prepared['frames'], prepared['validities'])):
        if index == reference_index:
            report = {'gain': 1., 'raw_gain': 1., 'samples': image.shape[0] * image.shape[1], 'spread_stops': 0, 'clamped': False}
        else:
            support = np.minimum(valid, prepared['validities'][reference_index])
            report = robust_gain(reference, image, support)
        gains = color_gains(reference, image) if color and index != reference_index else np.ones(3, np.float32)
        result.append({'frame_id': frame['id'], 'name': frame['name'], **report, 'color_gains': gains.tolist()})
    return {'reference': project['reference'], 'frames': result, 'note': '匹配生成新素材并保留原素材；HDR有效曝光同步乘以亮度增益。局部运动、色差与截断会影响估计。'}


def apply_match(store, assets, key, revision, body):
    color = v.boolean(body.get('color', False), '匹配色偏')
    plan = match_plan(store.get(key), assets, color)
    with store.edit(key, revision, '匹配多帧曝光与色偏') as project:
        for entry in plan['frames']:
            if entry['frame_id'] == project['reference']:
                continue
            frame = model.find_frame(project, entry['frame_id'])
            image = assets.field(frame['asset'])
            linear = srgb_decode(image) * entry['gain'] * np.asarray(entry['color_gains'])[None, None]
            corrected = np.clip(srgb_encode(linear), 0, 1).astype(np.float32)
            metadata = assets.metadata(frame['asset'])
            asset = assets.put(corrected, frame['name'] + ' · 匹配', {'derived_from': frame['asset'], 'operation': 'photometric-match', 'gain': entry['gain'], 'exif': metadata.get('exif', {})})
            frame['asset'] = asset['id']
            frame['exposure'] = min(3600, max(.000001, frame['exposure'] * entry['gain']))
    return {'project': store.get(key), 'report': plan}
