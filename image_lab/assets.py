import hashlib
import io
import os
from pathlib import Path
import numpy as np
from . import validation as v
from . import model
from .arrays import finite_rgb, finite_mask
from .codecs import decode_image, thumbnail, png_bytes, visualization


class Assets:
    def __init__(self, store):
        self.store = store
        self.root = store.assets

    def path(self, asset_id, suffix='.npz'):
        v.sha(asset_id)
        return self.root / (asset_id + suffix)

    def metadata(self, asset_id):
        result = self.store.read(self.path(asset_id, '.json'))
        if result is None:
            raise v.Invalid('素材不存在')
        return result

    def field(self, asset_id):
        self.metadata(asset_id)
        with np.load(self.path(asset_id), allow_pickle=False) as data:
            return data['image'].copy()

    def put(self, image, name, metadata=None, kind='image'):
        name = v.text(name, '素材名称', 180, False)
        if kind == 'mask':
            image = finite_mask(image)
        else:
            image = finite_rgb(image)
        if max(image.shape[:2]) > 4096 or image.shape[0] * image.shape[1] > 12000000:
            raise v.Invalid('素材尺寸超过限制')
        if image.min() < 0 or image.max() > 1:
            raise v.Invalid('素材需归一化至0–1')
        image = np.ascontiguousarray(image, dtype=np.float32)
        asset_id = hashlib.sha256(kind.encode() + str(image.shape).encode() + image.tobytes()).hexdigest()
        with self.store.lock:
            if not self.path(asset_id).exists():
                temporary = self.root / (model.uid() + '.npz')
                np.savez_compressed(temporary, image=image)
                os.replace(temporary, self.path(asset_id))
                self.path(asset_id, '.jpg').write_bytes(thumbnail(visualization(image), 260))
                item = {'id': asset_id, 'name': name, 'kind': kind, 'width': image.shape[1], 'height': image.shape[0], 'created_at': v.timestamp(), 'dtype': 'float32', **(metadata or {})}
                self.store.write(self.path(asset_id, '.json'), item)
        return self.metadata(asset_id)

    def import_image(self, raw, name):
        image, metadata = decode_image(raw)
        metadata['source_sha256'] = hashlib.sha256(raw).hexdigest()
        metadata['source_bytes'] = len(raw)
        return self.put(image, name, metadata)

    def list(self, kind=None):
        with self.store.lock:
            result = [self.store.read(path) for path in self.root.glob('*.json')]
        if kind:
            result = [item for item in result if item['kind'] == kind]
        return sorted(result, key=lambda item: item['created_at'], reverse=True)

    def full_png(self, asset_id, bits=8):
        return png_bytes(self.field(asset_id), bits)

    def verify(self, asset_id):
        meta = self.metadata(asset_id)
        image = self.field(asset_id)
        calculated = hashlib.sha256(meta['kind'].encode() + str(image.shape).encode() + np.ascontiguousarray(image, dtype=np.float32).tobytes()).hexdigest()
        return {'id': asset_id, 'hash_valid': calculated == asset_id, 'shape': list(image.shape), 'finite': bool(np.isfinite(image).all()), 'minimum': float(image.min()), 'maximum': float(image.max())}
