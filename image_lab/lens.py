import cv2
import numpy as np
from . import validation as v


def settings(body):
    body = v.object_value(body, '镜头校正')
    return {
        'enabled': v.boolean(body.get('enabled', False), '镜头校正开关'),
        'k1': v.number(body.get('k1', 0), '径向畸变k1', -.5, .5),
        'k2': v.number(body.get('k2', 0), '径向畸变k2', -.5, .5),
        'p1': v.number(body.get('p1', 0), '切向畸变p1', -.05, .05),
        'p2': v.number(body.get('p2', 0), '切向畸变p2', -.05, .05),
        'center_x': v.number(body.get('center_x', .5), '光心X', .2, .8),
        'center_y': v.number(body.get('center_y', .5), '光心Y', .2, .8),
        'focal': v.number(body.get('focal', 1), '焦距/长边', .3, 4),
        'red_scale': v.number(body.get('red_scale', 1), '红通道径向缩放', .98, 1.02),
        'blue_scale': v.number(body.get('blue_scale', 1), '蓝通道径向缩放', .98, 1.02),
        'vignette': v.number(body.get('vignette', 0), '亮度暗角补偿', 0, 2),
    }


def camera_matrix(width, height, config):
    focal = max(width, height) * config['focal']
    return np.array([[focal, 0, width * config['center_x']], [0, focal, height * config['center_y']], [0, 0, 1]], np.float64)


def correction_maps(width, height, config):
    matrix = camera_matrix(width, height, config)
    distortion = np.asarray([config['k1'], config['k2'], config['p1'], config['p2'], 0], np.float64)
    return cv2.initUndistortRectifyMap(matrix, distortion, None, matrix, (width, height), cv2.CV_32FC1)


def correct(image, config):
    if not config['enabled']:
        return image.copy(), np.ones(image.shape[:2], np.float32)
    h, w = image.shape[:2]
    map_x, map_y = correction_maps(w, h, config)
    channels = []
    cx, cy = w * config['center_x'], h * config['center_y']
    for index, scale in enumerate([config['red_scale'], 1, config['blue_scale']]):
        x = (map_x - cx) * scale + cx
        y = (map_y - cy) * scale + cy
        channels.append(cv2.remap(image[:, :, index], x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT))
    output = np.stack(channels, axis=2)
    valid = ((map_x >= 1) & (map_x <= w - 2) & (map_y >= 1) & (map_y <= h - 2)).astype(np.float32)
    if config['vignette'] > 0:
        y, x = np.indices((h, w), dtype=np.float32)
        radial = ((x - cx) / max(w / 2, 1)) ** 2 + ((y - cy) / max(h / 2, 1)) ** 2
        output *= (1 + config['vignette'] * radial ** 2)[:, :, None]
    return np.clip(output, 0, 1).astype(np.float32), valid


def grid_preview(shape, config):
    h, w = shape
    canvas = np.full((h, w, 3), .12, np.float32)
    for x in np.linspace(0, w - 1, 13).astype(int):
        canvas[:, max(0, x - 1):x + 1] = [.7, .8, .8]
    for y in np.linspace(0, h - 1, 9).astype(int):
        canvas[max(0, y - 1):y + 1] = [.7, .8, .8]
    return correct(canvas, config)[0]
