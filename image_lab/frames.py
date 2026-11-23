from copy import deepcopy
import numpy as np
from . import validation as v
from . import model
from .metadata import effective_exposure


class Frames:
    def __init__(self, store, assets):
        self.store = store
        self.assets = assets

    def add(self, key, revision, asset_ids):
        ids = v.list_value(asset_ids, '素材编号', 24)
        if not ids:
            raise v.Invalid('请选择图片素材')
        created = []
        with self.store.edit(key, revision, '加入照片帧') as project:
            if len(project['frames']) + len(ids) > 24:
                raise v.Invalid('工程最多24帧')
            for asset_id in ids:
                asset = self.assets.metadata(asset_id)
                if asset['kind'] != 'image':
                    raise v.Invalid('遮罩不能作为照片帧')
                current = model.frame(asset_id, asset['name'], effective_exposure(asset.get('exif', {})))
                project['frames'].append(current)
                created.append(current['id'])
            if project['reference'] is None:
                project['reference'] = created[0]
        return {'project': self.store.get(key), 'frame_ids': created}

    def update(self, key, revision, frame_id, body):
        allowed = {'revision', 'name', 'enabled', 'exposure', 'ev', 'weight', 'focus_distance', 'matrix', 'rating', 'label', 'notes'}
        if set(body) - allowed:
            raise v.Invalid('未知照片属性')
        with self.store.edit(key, revision, '编辑照片帧') as project:
            current = model.find_frame(project, frame_id)
            for field in allowed - {'revision'}:
                if field in body:
                    current[field] = deepcopy(body[field])
        return self.store.get(key)

    def reorder(self, key, revision, ids):
        ids = v.list_value(ids, '帧顺序', 24)
        with self.store.edit(key, revision, '调整照片顺序') as project:
            if len(ids) != len(project['frames']) or set(ids) != {f['id'] for f in project['frames']}:
                raise v.Invalid('排序必须恰好包含全部照片帧')
            lookup = {f['id']: f for f in project['frames']}
            project['frames'] = [lookup[key] for key in ids]
        return self.store.get(key)

    def remove(self, key, revision, ids):
        ids = set(v.identifier(item) for item in v.list_value(ids, '照片编号', 24))
        if not ids:
            raise v.Invalid('请选择要移除的帧')
        with self.store.edit(key, revision, '移除照片帧') as project:
            for frame_id in ids:
                model.find_frame(project, frame_id)
            project['frames'] = [f for f in project['frames'] if f['id'] not in ids]
            if project['reference'] in ids:
                project['reference'] = project['frames'][0]['id'] if project['frames'] else None
        return self.store.get(key)

    def reset_transforms(self, key, revision):
        with self.store.edit(key, revision, '重置全部变换') as project:
            for frame in project['frames']:
                frame['matrix'] = model.identity()
        return self.store.get(key)

    def manual_transform(self, key, revision, frame_id, body):
        dx = v.number(body.get('x', 0), '水平位移像素', -3000, 3000)
        dy = v.number(body.get('y', 0), '垂直位移像素', -3000, 3000)
        angle = v.number(body.get('angle', 0), '旋转角度', -180, 180)
        scale = v.number(body.get('scale', 1), '缩放', .2, 5)
        width = v.integer(body.get('width'), '参考画布宽', 32, 4096)
        height = v.integer(body.get('height'), '参考画布高', 32, 4096)
        import cv2
        affine = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
        affine[:, 2] += [dx, dy]
        pixel = np.vstack([affine, [0, 0, 1]])
        diagonal = np.diag([width, height, 1])
        normalized = np.linalg.inv(diagonal) @ pixel @ diagonal
        return self.update(key, revision, frame_id, {'matrix': normalized.tolist()})

    def auto_exposure(self, key, revision, mode='rank'):
        mode = v.choice(mode, '曝光估计', ['rank', 'luminance'])
        values = []
        from .arrays import luminance
        for frame in self.store.get(key)['frames']:
            image = self.assets.field(frame['asset'])
            gray = luminance(image)
            selected = gray[(gray > .02) & (gray < .98)]
            value = float(np.median(selected)) if selected.size else float(gray.mean())
            values.append((frame['id'], max(value, .001)))
        base = max(min(value for _, value in values), .001) if values else 1
        with self.store.edit(key, revision, '估算相对曝光（近似）') as project:
            if mode == 'rank':
                ordered = sorted(values, key=lambda item: item[1])
                exposures = {key: 2. ** i for i, (key, _) in enumerate(ordered)}
            else:
                exposures = {key: min(3600, value / base) for key, value in values}
            for frame in project['frames']:
                frame['exposure'] = exposures[frame['id']]
                frame['ev'] = 0
        return self.store.get(key)
