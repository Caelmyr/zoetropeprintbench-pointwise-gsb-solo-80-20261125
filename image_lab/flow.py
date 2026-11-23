import cv2
import numpy as np
from .arrays import luminance, normalize
from . import validation as v


def settings(body):
    body = v.object_value(body, '局部配准')
    return {
        'enabled': v.boolean(body.get('enabled', False), '启用局部光流'),
        'strength': v.number(body.get('strength', 1), '光流修正比例', 0, 1),
        'maximum': v.number(body.get('maximum', 12), '局部最大位移', 1, 64),
        'window': v.integer(body.get('window', 21), '光流窗口', 7, 51),
        'levels': v.integer(body.get('levels', 3), '光流层数', 1, 5),
    }


def estimate(reference, moving, config):
    a = normalize(luminance(reference), 1, 99)
    b = normalize(luminance(moving), 1, 99)
    flow = cv2.calcOpticalFlowFarneback(a * 255, b * 255, None, .5, config['levels'], config['window'] | 1, 3, 5, 1.2, 0)
    magnitude = np.linalg.norm(flow, axis=2)
    limited = np.minimum(1., config['maximum'] / np.maximum(magnitude, 1e-5))
    flow *= limited[:, :, None] * config['strength']
    return flow.astype(np.float32)


def warp_flow(image, flow):
    h, w = image.shape[:2]
    yy, xx = np.indices((h, w), dtype=np.float32)
    return cv2.remap(image, xx + flow[:, :, 0], yy + flow[:, :, 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def refine(prepared, config, progress=None, cancelled=None):
    if not config['enabled']:
        return prepared
    index = prepared['reference_index']
    reference = prepared['images'][index]
    diagnostics = []
    for current in range(len(prepared['images'])):
        if cancelled and cancelled():
            raise InterruptedError('局部光流取消')
        if current == index:
            diagnostics.append({'reference': True})
            continue
        flow = estimate(reference, prepared['images'][current], config)
        prepared['images'][current] = warp_flow(prepared['images'][current], flow)
        prepared['validities'][current] = warp_flow(prepared['validities'][current], flow)
        magnitude = np.linalg.norm(flow, axis=2)
        diagnostics.append({'mean_displacement': float(magnitude.mean()), 'p95_displacement': float(np.percentile(magnitude, 95))})
        if progress:
            progress(.3 + .08 * (current + 1) / len(prepared['images']), '局部光流修正')
    prepared['flow_report'] = diagnostics
    return prepared


def flow_visualization(flow):
    magnitude, angle = cv2.cartToPolar(flow[:, :, 0], flow[:, :, 1])
    hsv = np.zeros((*magnitude.shape, 3), np.float32)
    hsv[:, :, 0] = angle * 180 / np.pi
    hsv[:, :, 1] = 1
    hsv[:, :, 2] = np.clip(magnitude / max(float(np.percentile(magnitude, 95)), .1), 0, 1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
