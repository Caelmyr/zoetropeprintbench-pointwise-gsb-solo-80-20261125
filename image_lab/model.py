import uuid
import hashlib
import json
from copy import deepcopy
from . import validation as v
from .settings import all_settings


def uid():
    return uuid.uuid4().hex


def identity():
    return [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]


def frame(asset, name, exposure=1):
    return {'id': uid(), 'asset': asset, 'name': name, 'enabled': True, 'exposure': exposure, 'ev': 0., 'weight': 1., 'focus_distance': None, 'matrix': identity(), 'mask': None, 'rating': 0, 'label': '', 'notes': ''}


def project(name):
    now = v.timestamp()
    return {'schema': 1, 'id': uid(), 'name': name, 'revision': 0, 'created_at': now, 'updated_at': now, 'notes': '', 'tags': [], 'regions': [], 'frames': [], 'reference': None, 'settings': all_settings({}), 'undo': [], 'redo': [], 'current_result': None, 'archived': False}


def find_frame(project, frame_id):
    v.identifier(frame_id)
    current = next((f for f in project['frames'] if f['id'] == frame_id), None)
    if current is None:
        raise v.Invalid('照片帧不存在')
    return current


def enabled_frames(project):
    result = [f for f in project['frames'] if f['enabled']]
    if not result:
        raise v.Invalid('请至少启用一张照片')
    return result


def snapshot(project):
    return {k: deepcopy(value) for k, value in project.items() if k not in ('revision', 'updated_at', 'undo', 'redo', 'current_result')}


def signature(project):
    fields = ['id', 'asset', 'enabled', 'exposure', 'ev', 'weight', 'matrix', 'mask']
    data = {'frames': [{k: f[k] for k in fields} for f in project['frames']], 'reference': project['reference'], 'settings': project['settings']}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate(project):
    if project.get('schema') != 1:
        raise v.Invalid('不支持的工程格式')
    v.identifier(project.get('id'))
    v.text(project.get('name'), '工程名称', 120, False)
    v.text(project.get('notes', ''), '说明', 5000)
    for tag in v.list_value(project.get('tags', []), '标签', 20):
        v.text(tag, '标签', 30, False)
    ids = set()
    for f in v.list_value(project.get('frames'), '照片帧', 24):
        v.object_value(f, '照片帧')
        v.identifier(f.get('id'))
        if f['id'] in ids:
            raise v.Invalid('照片帧编号重复')
        ids.add(f['id'])
        v.sha(f.get('asset'))
        v.text(f.get('name'), '照片名', 180, False)
        v.boolean(f.get('enabled'), '启用照片')
        f['matrix'] = v.matrix(f.get('matrix'))
        v.number(f.get('exposure'), '曝光秒数', .000001, 3600)
        v.number(f.get('ev'), '曝光补偿', -12, 12)
        v.number(f.get('weight'), '帧权重', 0, 8)
        v.integer(f.get('rating', 0), '星级', 0, 5)
        v.text(f.get('label', ''), '颜色标签', 30)
        v.text(f.get('notes', ''), '照片备注', 500)
        if f.get('focus_distance') is not None:
            v.number(f['focus_distance'], '对焦距离', .001, 100000)
        if f.get('mask'):
            v.sha(f['mask'])
    if project.get('reference') is not None and project['reference'] not in ids:
        raise v.Invalid('参考帧不存在')
    project['settings'] = all_settings(project.get('settings'))
    if project['settings']['size'] ** 2 * max(len(ids), 1) > 16000000:
        raise v.Invalid('超过1600万帧像素预算，请降低长边或减少照片')
    from .regions import validate_region
    for region in v.list_value(project.get('regions', []), '质量检测区域', 32):
        validate_region(region)
    v.boolean(project.get('archived', False), '归档')
    return project


def summary(project):
    return {key: project[key] for key in ['id', 'name', 'revision', 'created_at', 'updated_at', 'reference', 'current_result', 'tags', 'archived']} | {'frame_count': len(project['frames']), 'mode': project['settings']['mode'], 'size': project['settings']['size']}
