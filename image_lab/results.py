import os
import numpy as np
from . import model
from . import validation as v
from .engine import process
from .codecs import png_bytes, thumbnail, visualization, float_archive
from .statistics import image_report


class Results:
    def __init__(self, store, assets):
        self.store = store
        self.assets = assets

    def directory(self, key, result_id):
        v.identifier(result_id)
        directory = self.store.directory(key) / 'results' / result_id
        if not (directory / 'result.json').exists():
            raise v.Invalid('合成结果不存在或尚未完成')
        return directory

    def metadata(self, key, result_id=None):
        project = self.store.get(key)
        result_id = result_id or project['current_result']
        if not result_id:
            raise v.Invalid('请先合成当前工程')
        directory = self.directory(key, result_id)
        metadata = self.store.read(directory / 'result.json')
        metadata['stale'] = metadata['source_signature'] != model.signature(project)
        return metadata

    def fields(self, key, result_id=None, require_current=True):
        metadata = self.metadata(key, result_id)
        if require_current and metadata['stale']:
            raise v.Invalid('结果已过期，请重新合成')
        with np.load(self.directory(key, metadata['id']) / 'fields.npz', allow_pickle=False) as data:
            fields = {name: data[name].copy() for name in data.files}
        return fields, metadata

    def run(self, project, progress=None, cancelled=None, activate=True):
        computed = process(project, self.assets, progress, cancelled)
        if cancelled and cancelled():
            raise InterruptedError('合成保存前取消')
        result_id = model.uid()
        directory = self.store.directory(project['id']) / 'results' / result_id
        directory.mkdir()
        fields = computed['fields']
        (directory / 'fields.npz').write_bytes(float_archive(fields))
        (directory / 'image.png').write_bytes(png_bytes(fields['image']))
        (directory / 'thumb.jpg').write_bytes(thumbnail(fields['image']))
        for name, field in fields.items():
            if name in ('image', 'linear', 'before_finishing'):
                continue
            (directory / (name + '.png')).write_bytes(png_bytes(visualization(field, 'heat' if name not in ('valid',) else 'gray')))
        metadata = {'id': result_id, 'project_id': project['id'], 'name': project['name'], 'source_revision': project['revision'], 'source_signature': computed['signature'], 'created_at': v.timestamp(), 'mode': project['settings']['mode'], 'frame_ids': computed['frame_ids'], 'fields': list(fields), 'width': fields['image'].shape[1], 'height': fields['image'].shape[0], 'report': computed['report'], 'statistics': image_report(fields['image'], fields['valid']), 'project': model.snapshot(project)}
        self.store.write(directory / 'result.json', metadata)
        metadata['activated'] = self.store.activate(project['id'], result_id, computed['signature']) if activate else False
        if progress:
            progress(.98, '结果已保存')
        return metadata

    def list(self, key):
        current = self.store.get(key)
        result = []
        for path in (self.store.directory(key) / 'results').glob('*/result.json'):
            meta = self.store.read(path)
            result.append({field: meta[field] for field in ['id', 'name', 'source_revision', 'source_signature', 'created_at', 'mode', 'width', 'height']} | {'current': current['current_result'] == meta['id'], 'stale': meta['source_signature'] != model.signature(current)})
        return sorted(result, key=lambda row: row['created_at'], reverse=True)

    def select(self, key, result_id):
        metadata = self.metadata(key, result_id)
        if not self.store.activate(key, result_id, metadata['source_signature']):
            raise v.Invalid('历史结果与当前设置不一致；请恢复对应版本后再选择')
        return self.store.get(key)

    def image_path(self, key, name='image', result_id=None):
        meta = self.metadata(key, result_id)
        if name not in meta['fields'] and name != 'thumb':
            raise v.Invalid('辅助图不存在')
        directory = self.directory(key, meta['id'])
        filename = name + ('.jpg' if name == 'thumb' else '.png')
        if not (directory / filename).exists():
            fields, _ = self.fields(key, meta['id'], False)
            if name == 'linear':
                from .tone import tone_map
                image = tone_map(fields['linear'], meta['project']['settings']['tone'])
            else:
                image = visualization(fields[name])
            return png_bytes(image)
        return directory / filename

    def integrity(self, key, result_id=None):
        fields, meta = self.fields(key, result_id, False)
        result = []
        shape = (meta['height'], meta['width'])
        for name, field in fields.items():
            result.append({'field': name, 'shape': list(field.shape), 'correct_size': field.shape[:2] == shape, 'finite': bool(np.isfinite(field).all()), 'minimum': float(field.min()), 'maximum': float(field.max())})
        return {'result_id': meta['id'], 'valid': all(row['correct_size'] and row['finite'] for row in result), 'fields': result}

    def verify_encoded(self, key, result_id=None):
        import hashlib
        import cv2
        fields, metadata = self.fields(key, result_id, False)
        directory = self.directory(key, metadata['id'])
        checks = []
        for filename in ['image.png', 'thumb.jpg', 'fields.npz', 'result.json']:
            path = directory / filename
            if not path.exists():
                checks.append({'file': filename, 'exists': False, 'valid': False})
                continue
            raw = path.read_bytes()
            item = {'file': filename, 'exists': True, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'valid': True}
            if filename == 'image.png':
                decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                if decoded is None or decoded.shape != fields['image'].shape:
                    item['valid'] = False
                else:
                    decoded = decoded[:, :, ::-1].astype(np.float32) / 255
                    error = np.abs(decoded - fields['image'])
                    item['maximum_error'] = float(error.max())
                    item['valid'] = bool(error.max() <= .5 / 255 + 2e-7)
            elif filename == 'thumb.jpg':
                decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                item['valid'] = decoded is not None
            checks.append(item)
        return {'result_id': metadata['id'], 'valid': all(row['valid'] for row in checks), 'files': checks}
