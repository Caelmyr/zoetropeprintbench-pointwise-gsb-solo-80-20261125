import numpy as np
import cv2
from . import validation as v
from .arrays import luminance, blur, srgb_decode, srgb_encode
from .finishing import white_balance, saturation


def adjustment_spec(body):
    body = v.object_value(body, '局部调整')
    shape = v.choice(body.get('shape', 'ellipse'), '局部区域形状', ['ellipse', 'linear', 'luminance'])
    center = v.vector(body.get('center', [.5, .5]), '区域中心', 2, 0, 1)
    extent = v.vector(body.get('extent', [.3, .3]), '区域半径', 2, .01, 2)
    return {
        'shape': shape,
        'center': center,
        'extent': extent,
        'angle': v.number(body.get('angle', 0), '局部旋转', -180, 180),
        'feather': v.number(body.get('feather', .4), '局部羽化', .001, 1),
        'invert': v.boolean(body.get('invert', False), '区域反相'),
        'low': v.number(body.get('low', .2), '亮度范围低点', 0, 1),
        'high': v.number(body.get('high', .8), '亮度范围高点', 0, 1),
        'exposure': v.number(body.get('exposure', 0), '局部曝光EV', -3, 3),
        'temperature': v.number(body.get('temperature', 0), '局部色温', -1, 1),
        'saturation': v.number(body.get('saturation', 1), '局部饱和度', 0, 2),
        'contrast': v.number(body.get('contrast', 1), '局部对比度', .2, 3),
        'clarity': v.number(body.get('clarity', 0), '局部清晰度', -1, 1),
    }


def region_weight(image, config):
    h, w = image.shape[:2]
    y, x = np.indices((h, w), dtype=np.float32)
    x = x / max(w - 1, 1) - config['center'][0]
    y = y / max(h - 1, 1) - config['center'][1]
    angle = np.deg2rad(config['angle'])
    rx = (x * np.cos(angle) + y * np.sin(angle)) / config['extent'][0]
    ry = (-x * np.sin(angle) + y * np.cos(angle)) / config['extent'][1]
    if config['shape'] == 'ellipse':
        distance = np.hypot(rx, ry)
        weight = np.clip((1 - distance) / config['feather'], 0, 1)
    elif config['shape'] == 'linear':
        weight = np.clip(.5 - rx / 2, 0, 1)
    else:
        gray = luminance(image)
        low, high = sorted([config['low'], config['high']])
        width = max((high - low) * config['feather'], .001)
        weight = np.minimum(np.clip((gray - low) / width, 0, 1), np.clip((high - gray) / width, 0, 1))
    weight = weight * weight * (3 - 2 * weight)
    return 1 - weight if config['invert'] else weight


def apply_adjustment(image, config):
    mask = region_weight(image, config)
    adjusted = srgb_decode(image) * 2 ** config['exposure']
    adjusted = white_balance(adjusted, config['temperature'])
    adjusted = srgb_encode(adjusted)
    adjusted = saturation(adjusted, config['saturation'])
    adjusted = (adjusted - .5) * config['contrast'] + .5
    if config['clarity']:
        gray = luminance(adjusted)
        adjusted += (gray - blur(gray, 8))[:, :, None] * config['clarity']
    image = image * (1 - mask[:, :, None]) + adjusted * mask[:, :, None]
    return np.clip(image, 0, 1).astype(np.float32), mask


def apply_all(image, configurations):
    output = image.copy()
    for configuration in configurations:
        output, _ = apply_adjustment(output, configuration)
    return output


def preview_overlay(image, config):
    mask = region_weight(image, config)
    overlay = np.broadcast_to([1., .3, .15], image.shape)
    return image * (1 - mask[:, :, None] * .4) + overlay * mask[:, :, None] * .4
