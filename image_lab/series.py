import re
from datetime import datetime
from . import validation as v
from .metadata import effective_exposure


def capture_time(metadata):
    exif = metadata.get('exif', {})
    text = exif.get('DateTimeOriginal', exif.get('DateTime'))
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], '%Y:%m:%d %H:%M:%S').timestamp()
    except (TypeError, ValueError):
        return None


def natural_name(name):
    parts = re.split(r'(\d+)', name.casefold())
    return [(0, int(part)) if part.isdigit() else (1, part) for part in parts]


def group_assets(assets, body):
    ids = v.list_value(body.get('asset_ids'), '照片素材', 96)
    if not ids:
        raise v.Invalid('请选择素材')
    method = v.choice(body.get('method', 'count'), '分组方式', ['count', 'time', 'exposure_cycle'])
    count = v.integer(body.get('count', 3), '每组帧数', 2, 24)
    gap = v.number(body.get('gap_seconds', 5), '拍摄间隔秒', .1, 3600)
    metadata = [assets.metadata(key) for key in ids]
    if any(item['kind'] != 'image' for item in metadata):
        raise v.Invalid('只能对照片素材分组')
    metadata.sort(key=lambda item: (capture_time(item) or 0, natural_name(item['name'])))
    groups, current = [], []
    previous = None
    for item in metadata:
        split = False
        if current:
            if method == 'count':
                split = len(current) >= count
            elif method == 'time':
                now, before = capture_time(item), capture_time(previous)
                if now is None or before is None:
                    raise v.Invalid('按拍摄时间分组需要全部照片含有效EXIF时间')
                split = now - before > gap or len(current) >= 24
            else:
                now = effective_exposure(item.get('exif', {}))
                before = effective_exposure(previous.get('exif', {}))
                split = now < before * .7 or len(current) >= 24
        if split:
            groups.append(current)
            current = []
        current.append(item)
        previous = item
    if current:
        groups.append(current)
    return {'method': method, 'groups': [{'index': index, 'asset_ids': [item['id'] for item in group], 'names': [item['name'] for item in group], 'count': len(group)} for index, group in enumerate(groups)], 'note': '自动分组只提供候选序列，请检查曝光顺序、内容与移动物体。'}


def instantiate_groups(store, frames, assets, body):
    plan = group_assets(assets, body)
    prefix = v.text(body.get('name', '摄影序列'), '工程前缀', 80, False)
    created = []
    for group in plan['groups']:
        project = store.create(prefix + ' ' + str(group['index'] + 1).zfill(2))
        added = frames.add(project['id'], project['revision'], group['asset_ids'])
        created.append(added['project'])
    return {'plan': plan, 'projects': created}
