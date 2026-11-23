from copy import deepcopy
import numpy as np
from . import model
from . import validation as v
from .statistics import image_report
from .comparison import compare
from .arrays import resize, luminance


def validate_region(region):
    v.identifier(region.get('id'))
    v.text(region.get('name'), '区域名称', 80, False)
    bounds = v.vector(region.get('bounds'), '区域坐标', 4, 0, 1)
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise v.Invalid('区域需要正的宽度和高度')
    v.color(region.get('color', '#e0b77a'))
    v.text(region.get('notes', ''), '区域备注', 1000)
    return region


def pixel_bounds(region, shape):
    h, w = shape[:2]
    x0, y0, x1, y1 = region['bounds']
    left = min(w - 1, int(x0 * w))
    top = min(h - 1, int(y0 * h))
    right = max(left + 1, min(w, int(np.ceil(x1 * w))))
    bottom = max(top + 1, min(h, int(np.ceil(y1 * h))))
    return [left, top, right, bottom]


def extract(image, region):
    x0, y0, x1, y1 = pixel_bounds(region, image.shape)
    return image[y0:y1, x0:x1].copy()


class Regions:
    def __init__(self, store, assets, results):
        self.store = store
        self.assets = assets
        self.results = results

    def get(self, key, region_id):
        v.identifier(region_id)
        project = self.store.get(key)
        result = next((r for r in project.get('regions', []) if r['id'] == region_id), None)
        if result is None:
            raise v.Invalid('检测区域不存在')
        return result

    def add(self, key, revision, body):
        region = {'id': model.uid(), 'name': v.text(body.get('name', '检测区域'), '区域名称', 80, False), 'bounds': body.get('bounds', [.25, .25, .75, .75]), 'color': body.get('color', '#e0b77a'), 'notes': body.get('notes', '')}
        validate_region(region)
        with self.store.edit(key, revision, '添加质量检测区域') as project:
            regions = project.setdefault('regions', [])
            if len(regions) >= 32:
                raise v.Invalid('最多32个检测区域')
            regions.append(region)
        return {'project': self.store.get(key), 'region_id': region['id']}

    def update(self, key, revision, region_id, body):
        with self.store.edit(key, revision, '编辑质量检测区域') as project:
            current = next((r for r in project.get('regions', []) if r['id'] == region_id), None)
            if current is None:
                raise v.Invalid('区域不存在')
            for field in ['name', 'bounds', 'color', 'notes']:
                if field in body:
                    current[field] = deepcopy(body[field])
            validate_region(current)
        return self.store.get(key)

    def remove(self, key, revision, region_id):
        self.get(key, region_id)
        with self.store.edit(key, revision, '移除质量检测区域') as project:
            project['regions'] = [r for r in project.get('regions', []) if r['id'] != region_id]
        return self.store.get(key)

    def analyze(self, key, region_id, result_id=None):
        region = self.get(key, region_id)
        fields, meta = self.results.fields(key, result_id, not bool(result_id))
        image = extract(fields['image'], region)
        valid = extract(fields['valid'], region)
        report = image_report(image, valid)
        report['region'] = region
        report['pixel_bounds'] = pixel_bounds(region, fields['image'].shape)
        report['result_id'] = meta['id']
        report['auxiliaries'] = {}
        for name, field in fields.items():
            if field.ndim == 2:
                cropped = extract(field, region)
                report['auxiliaries'][name] = {'mean': float(cropped.mean()), 'std': float(cropped.std()), 'minimum': float(cropped.min()), 'maximum': float(cropped.max())}
        return report

    def across_frames(self, key, region_id):
        from .preparation import prepare
        region = self.get(key, region_id)
        prepared = prepare(self.store.get(key), self.assets)
        reports = []
        for image, frame, valid in zip(prepared['images'], prepared['frames'], prepared['validities']):
            cropped = extract(image, region)
            support = extract(valid, region)
            reports.append({'frame_id': frame['id'], 'name': frame['name'], 'report': image_report(cropped, support)})
        reference = reports[prepared['reference_index']]
        ranked = sorted(reports, key=lambda row: row['report']['focus']['laplacian_variance'], reverse=True)
        return {'region': region, 'frames': reports, 'sharpest_frame': ranked[0]['frame_id'], 'reference': reference['frame_id'], 'note': '不同曝光会影响清晰度数值，先检查局部截断情况。'}

    def annotate(self, key, image):
        from PIL import Image, ImageDraw
        raster = Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8))
        draw = ImageDraw.Draw(raster)
        for index, region in enumerate(self.store.get(key).get('regions', [])):
            x0, y0, x1, y1 = pixel_bounds(region, image.shape)
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=region['color'], width=2)
            draw.rectangle((x0, y0, x0 + 28, y0 + 20), fill=region['color'])
            draw.text((x0 + 5, y0 + 3), str(index + 1), fill='#14232d')
        return np.asarray(raster, np.float32) / 255
