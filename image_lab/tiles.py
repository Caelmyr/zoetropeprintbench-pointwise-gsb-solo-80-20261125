import io
import zipfile
import cv2
import numpy as np
from . import validation as v
from .codecs import png_bytes
from .exporting import json_bytes
from .arrays import srgb_decode, srgb_encode


def image_levels(image, minimum=128):
    result = [image.copy()]
    while max(result[-1].shape[:2]) > minimum:
        current = result[-1]
        w, h = max(1, current.shape[1] // 2), max(1, current.shape[0] // 2)
        linear = srgb_decode(current)
        smaller = cv2.resize(linear, (w, h), interpolation=cv2.INTER_AREA)
        result.append(np.clip(srgb_encode(smaller), 0, 1))
    return result


def tile_plan(width, height, tile_size, overlap):
    step = tile_size - overlap * 2
    if step <= 0:
        raise v.Invalid('瓦片边长需要大于两倍重叠宽度')
    result = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            x0, y0 = max(0, x - overlap), max(0, y - overlap)
            x1, y1 = min(width, x + step + overlap), min(height, y + step + overlap)
            result.append({'column': x // step, 'row': y // step, 'bounds': [x0, y0, x1, y1], 'interior': [x, y, min(width, x + step), min(height, y + step)]})
    return result


def tile_export(image, body):
    size = v.integer(body.get('tile_size', 256), '切片边长', 64, 1024)
    overlap = v.integer(body.get('overlap', 8), '切片重叠像素', 0, 64)
    pyramids = v.boolean(body.get('pyramid', True), '多分辨率切片')
    levels = image_levels(image, size) if pyramids else [image]
    stream = io.BytesIO()
    manifest = {'format': 'stacklight-image-tiles-v1', 'color_space': 'sRGB', 'note': '二维照片浏览切片；不属于游戏精灵图集。', 'tile_size': size, 'overlap': overlap, 'levels': []}
    total_tiles = 0
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, level in enumerate(levels):
            h, w = level.shape[:2]
            plan = tile_plan(w, h, size, overlap)
            total_tiles += len(plan)
            if total_tiles > 2048:
                raise v.Invalid('切片数量超过2048')
            for entry in plan:
                x0, y0, x1, y1 = entry['bounds']
                name = f'level-{index}/{entry["column"]}-{entry["row"]}.png'
                archive.writestr(name, png_bytes(level[y0:y1, x0:x1]))
                entry['file'] = name
            manifest['levels'].append({'level': index, 'width': w, 'height': h, 'tiles': plan})
        archive.writestr('tiles.json', json_bytes(manifest))
    return stream.getvalue(), manifest
