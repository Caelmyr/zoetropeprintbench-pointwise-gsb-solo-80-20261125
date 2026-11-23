import math
import cv2
import numpy as np
from . import model
from . import validation as v
from .arrays import resize, blur, luminance
from .focus import focus_measure


def interpolate_points(points, width, height, spacing):
    points = [v.vector(point, '笔画点', 2, 0, 1) for point in v.list_value(points, '笔画', 2000)]
    if not points:
        raise v.Invalid('笔画不能为空')
    points = [[p[0] * (width - 1), p[1] * (height - 1)] for p in points]
    result = [points[0]]
    for a, b in zip(points, points[1:]):
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, math.ceil(distance / max(spacing, .5)))
        if len(result) + steps > 10000:
            raise v.Invalid('笔画过长，请分笔绘制')
        for index in range(1, steps + 1):
            t = index / steps
            result.append([a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t])
    return result


def paint(mask, points, radius, value, opacity, hardness):
    result = mask.copy()
    h, w = result.shape
    for cx, cy in interpolate_points(points, w, h, radius * .3):
        left, right = max(0, math.floor(cx - radius)), min(w, math.ceil(cx + radius + 1))
        top, bottom = max(0, math.floor(cy - radius)), min(h, math.ceil(cy + radius + 1))
        y, x = np.mgrid[top:bottom, left:right]
        distance = np.hypot(x - cx, y - cy) / radius
        weight = np.clip((1 - distance) / max(1 - hardness, .001), 0, 1)
        weight = weight * weight * (3 - 2 * weight) * opacity
        current = result[top:bottom, left:right]
        result[top:bottom, left:right] = current * (1 - weight) + value * weight
    return result.astype(np.float32)


def polygon_mask(shape, points, feather=0, inverted=False):
    points = [v.vector(item, '多边形点', 2, 0, 1) for item in v.list_value(points, '多边形', 128)]
    if len(points) < 3:
        raise v.Invalid('多边形至少3个点')
    h, w = shape
    coordinates = np.rint(np.asarray(points) * [w - 1, h - 1]).astype(np.int32)
    mask = np.zeros(shape, np.float32)
    cv2.fillPoly(mask, [coordinates], 1)
    if feather > 0:
        mask = blur(mask, feather)
    return 1 - mask if inverted else mask


class Masks:
    def __init__(self, store, assets):
        self.store = store
        self.assets = assets

    def field(self, project, frame_id):
        frame = model.find_frame(project, frame_id)
        if frame['mask']:
            return self.assets.field(frame['mask'])
        reference = model.find_frame(project, project['reference'])
        meta = self.assets.metadata(reference['asset'])
        ratio = min(1, project['settings']['size'] / max(meta['width'], meta['height']))
        return np.ones((round(meta['height'] * ratio), round(meta['width'] * ratio)), np.float32)

    def store_mask(self, project, frame_id, field):
        asset = self.assets.put(np.clip(field, 0, 1), '排除遮罩 · ' + frame_id[:8], kind='mask')
        model.find_frame(project, frame_id)['mask'] = asset['id']

    def stroke(self, key, revision, frame_id, body):
        radius = v.number(body.get('radius', 16), '笔刷半径', 1, 160)
        value = v.number(body.get('value', 0), '遮罩值', 0, 1)
        opacity = v.number(body.get('opacity', .8), '笔刷不透明度', .01, 1)
        hardness = v.number(body.get('hardness', .5), '笔刷硬度', 0, 1)
        with self.store.edit(key, revision, '绘制照片排除遮罩') as project:
            original = self.field(project, frame_id)
            output = paint(original, body.get('points'), radius, value, opacity, hardness)
            self.store_mask(project, frame_id, output)
        return self.store.get(key)

    def fill(self, key, revision, frame_id, body):
        value = v.number(body.get('value', 1), '填充值', 0, 1)
        with self.store.edit(key, revision, '填充照片遮罩') as project:
            field = self.field(project, frame_id)
            field[:] = value
            self.store_mask(project, frame_id, field)
        return self.store.get(key)

    def process(self, key, revision, frame_id, body):
        operation = v.choice(body.get('operation'), '遮罩处理', ['invert', 'blur', 'expand', 'shrink', 'threshold', 'clear'])
        radius = v.integer(body.get('radius', 3), '处理半径', 1, 32)
        with self.store.edit(key, revision, '处理照片遮罩') as project:
            if operation == 'clear':
                model.find_frame(project, frame_id)['mask'] = None
            else:
                field = self.field(project, frame_id)
                if operation == 'invert':
                    field = 1 - field
                elif operation == 'blur':
                    field = blur(field, radius)
                elif operation == 'threshold':
                    field = (field > .5).astype(np.float32)
                else:
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
                    field = cv2.dilate(field, kernel) if operation == 'expand' else cv2.erode(field, kernel)
                self.store_mask(project, frame_id, field)
        return self.store.get(key)

    def automatic(self, key, revision, frame_id, body):
        kind = v.choice(body.get('kind', 'highlights'), '自动遮罩', ['highlights', 'shadows', 'focus', 'polygon'])
        low = v.number(body.get('low', .2), '低阈值', 0, .99)
        high = v.number(body.get('high', .8), '高阈值', .01, 1)
        inverted = v.boolean(body.get('inverted', False), '反相遮罩')
        feather = v.number(body.get('feather', 2), '羽化', 0, 32)
        with self.store.edit(key, revision, '自动生成照片遮罩') as project:
            original = self.field(project, frame_id)
            if kind == 'polygon':
                mask = polygon_mask(original.shape, body.get('points'), feather, inverted)
            else:
                frame = model.find_frame(project, frame_id)
                image = self.assets.field(frame['asset'])
                image = resize(image, original.shape[1], original.shape[0])
                if kind == 'focus':
                    from .arrays import normalize
                    field = normalize(focus_measure(image))
                else:
                    field = luminance(image)
                    if kind == 'shadows':
                        field = 1 - field
                selected = np.clip((field - low) / max(high - low, 1e-6), 0, 1)
                mask = blur(selected, feather)
                if inverted:
                    mask = 1 - mask
            self.store_mask(project, frame_id, mask)
        return self.store.get(key)
