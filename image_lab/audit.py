import hashlib
import json
from pathlib import Path
import numpy as np
from . import model
from . import validation as v
from .exporting import json_bytes


def project_references(project):
    references = set()
    for frame in project['frames']:
        references.add(frame['asset'])
        if frame['mask']:
            references.add(frame['mask'])
    calibration = project['settings']['calibration']
    for field in ['dark', 'flat']:
        if calibration[field]:
            references.add(calibration[field])
    return references


def audit_project(store, assets, results, key):
    project = store.get(key)
    errors, warnings, asset_checks, result_checks = [], [], [], []
    try:
        model.validate(project)
    except v.Invalid as error:
        errors.append(str(error))
    references = project_references(project)
    for asset_id in sorted(references):
        try:
            check = assets.verify(asset_id)
            asset_checks.append(check)
            if not check['hash_valid'] or not check['finite']:
                errors.append('素材校验失败：' + asset_id)
        except (v.Invalid, OSError, ValueError) as error:
            errors.append('素材不可读：' + asset_id + ' / ' + str(error))
    history = store.directory(key) / 'history'
    for entry_id in project['undo'] + project['redo']:
        if not (history / (entry_id + '.json')).exists():
            errors.append('历史记录缺失：' + entry_id)
    for metadata in results.list(key):
        try:
            check = results.integrity(key, metadata['id'])
            result_checks.append({'id': metadata['id'], 'valid': check['valid'], 'stale': metadata['stale']})
            if not check['valid']:
                errors.append('历史合成数据损坏：' + metadata['id'])
        except (v.Invalid, OSError, ValueError) as error:
            errors.append('结果不可读：' + metadata['id'] + ' / ' + str(error))
    if project['current_result'] and not any(row['id'] == project['current_result'] and row['valid'] for row in result_checks):
        errors.append('当前结果引用无效')
    for path in (store.directory(key) / 'results').iterdir():
        if path.is_dir() and not (path / 'result.json').exists():
            warnings.append('存在未完成的结果目录：' + path.name)
    return {'project_id': key, 'revision': project['revision'], 'signature': model.signature(project), 'healthy': not errors, 'errors': errors, 'warnings': warnings, 'assets': asset_checks, 'results': result_checks, 'checked_at': v.timestamp()}


def source_provenance(project, assets):
    result = []
    for index, frame in enumerate(project['frames']):
        metadata = assets.metadata(frame['asset'])
        result.append({'index': index, 'frame_id': frame['id'], 'name': frame['name'], 'asset_id': frame['asset'], 'source_sha256': metadata.get('source_sha256'), 'synthetic': metadata.get('synthetic', False), 'width': metadata['width'], 'height': metadata['height'], 'exposure': frame['exposure'], 'ev': frame['ev'], 'matrix': frame['matrix'], 'mask': frame['mask'], 'enabled': frame['enabled']})
    return {'project_id': project['id'], 'signature': model.signature(project), 'frames': result, 'note': '素材摘要基于规范化float32像素；上传原文件摘要另存source_sha256，示范图明确标记synthetic。'}
