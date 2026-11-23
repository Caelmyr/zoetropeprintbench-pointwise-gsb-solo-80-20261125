from copy import deepcopy
import cv2
import numpy as np
from . import validation as v
from . import model
from .arrays import luminance, resize


def perceptual_hash(image):
    gray = luminance(resize(image, 32, 32))
    frequency = cv2.dct(gray.astype(np.float32))[:8, :8]
    threshold = np.median(frequency.ravel()[1:])
    bits = frequency > threshold
    bits[0, 0] = False
    return np.packbits(bits.ravel()).tobytes().hex()


def hamming(first, second):
    return (int(first, 16) ^ int(second, 16)).bit_count()


def duplicate_plan(project, assets, threshold=5):
    rows = []
    for frame in project['frames']:
        image = assets.field(frame['asset'])
        fingerprint = perceptual_hash(image)
        rows.append({'id': frame['id'], 'name': frame['name'], 'asset': frame['asset'], 'hash': fingerprint, 'enabled': frame['enabled']})
    parents = list(range(len(rows)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    pairs = []
    for i, first in enumerate(rows):
        for j in range(i + 1, len(rows)):
            second = rows[j]
            distance = hamming(first['hash'], second['hash'])
            exact = first['asset'] == second['asset']
            if distance <= threshold or exact:
                pairs.append({'first': first['id'], 'second': second['id'], 'distance': distance, 'exact_pixels': exact})
                parents[root(j)] = root(i)
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault(root(index), []).append(row)
    return {'frames': rows, 'groups': [group for group in groups.values() if len(group) > 1], 'pairs': pairs, 'threshold': threshold, 'note': '曝光包围/景深序列本来就相似；仅像素完全相同的帧才适合自动去重。'}


def disable_exact_duplicates(store, assets, key, revision):
    with store.edit(key, revision, '停用像素完全重复照片') as project:
        reference = project['reference']
        preferred = sorted(project['frames'], key=lambda f: f['id'] != reference)
        seen = set()
        for frame in preferred:
            if frame['asset'] in seen:
                frame['enabled'] = False
            else:
                seen.add(frame['asset'])
    return store.get(key)


def sort_frames(store, assets, key, revision, method):
    method = v.choice(method, '照片排序', ['name', 'exposure', 'sharpness', 'rating', 'reverse'])
    with store.edit(key, revision, '照片排序：' + method) as project:
        if method == 'reverse':
            project['frames'].reverse()
        elif method == 'name':
            from .series import natural_name
            project['frames'].sort(key=lambda frame: natural_name(frame['name']))
        elif method == 'exposure':
            project['frames'].sort(key=lambda frame: frame['exposure'] * 2 ** frame['ev'])
        elif method == 'rating':
            project['frames'].sort(key=lambda frame: frame['rating'], reverse=True)
        else:
            from .statistics import focus_statistics
            scores = {}
            for frame in project['frames']:
                image = assets.field(frame['asset'])
                scores[frame['id']] = focus_statistics(resize(image, 384, 256))['laplacian_variance']
            project['frames'].sort(key=lambda frame: scores[frame['id']], reverse=True)
    return store.get(key)


def subset_project(store, key, body):
    source = store.get(key)
    ids = [v.identifier(item) for item in v.list_value(body.get('frame_ids'), '子序列照片', 24)]
    if not ids or len(ids) != len(set(ids)):
        raise v.Invalid('子序列需要不同的照片帧')
    for frame_id in ids:
        model.find_frame(source, frame_id)
    created = store.create(v.text(body.get('name', source['name'][:90] + ' · 子序列'), '子工程名称', 120, False))
    with store.edit(created['id'], 0, '从已选照片创建子工程') as project:
        project['frames'] = [deepcopy(model.find_frame(source, frame_id)) for frame_id in ids]
        project['reference'] = source['reference'] if source['reference'] in ids else ids[0]
        project['settings'] = deepcopy(source['settings'])
        project['notes'] = '来源工程：' + source['id'] + ' 修订' + str(source['revision'])
    return store.get(created['id'])
