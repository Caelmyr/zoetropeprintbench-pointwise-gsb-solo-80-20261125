from copy import deepcopy
import numpy as np
from . import model
from . import validation as v
from .arrays import luminance
from .preparation import prepare
from .statistics import focus_statistics


def select_bracket(project, assets, body):
    available = model.enabled_frames(project)
    count = v.integer(body.get('count', min(3, len(available))), '保留照片数', 1, min(12, len(available)))
    low = v.number(body.get('low', .05), '有效曝光低点', .001, .4)
    high = v.number(body.get('high', .95), '有效曝光高点', .6, .999)
    copy = deepcopy(project)
    copy['settings']['size'] = min(384, copy['settings']['size'])
    prepared = prepare(copy, assets)
    images = prepared['images']
    validities = prepared['validities']
    covered = np.zeros(images[0].shape[:2], bool)
    quality_maps = []
    balanced_scores = []
    sharpness_scores = []
    rows = []
    for image, valid, frame in zip(images, validities, prepared['frames']):
        gray = luminance(image)
        support = valid > .99
        well = support & (gray >= low) & (gray <= high)
        quality_maps.append(well)
        middle = np.maximum(0, 1 - np.abs(gray - .5) * 2) * support
        balanced_scores.append(float(middle.mean()))
        sharpness = focus_statistics(image)['laplacian_variance']
        sharpness_scores.append(sharpness)
        rows.append({'frame_id': frame['id'], 'name': frame['name'], 'well_exposed_fraction': float(well.mean()), 'balanced_score': float(middle.mean()), 'sharpness': sharpness})
    first = int(np.argmax(balanced_scores))
    chosen = [first]
    covered |= quality_maps[first]
    incremental = [float(covered.mean())]
    while len(chosen) < count:
        candidates = []
        for index, quality in enumerate(quality_maps):
            if index in chosen:
                continue
            gain = float(np.mean(quality & ~covered))
            redundancy = float(np.mean(quality & covered))
            score = gain + balanced_scores[index] * .0001 + np.log1p(sharpness_scores[index]) * .00001 - redundancy * .000001
            candidates.append((score, index, gain))
        _, best, gain = max(candidates)
        chosen.append(best)
        covered |= quality_maps[best]
        incremental.append(gain)
    selected_ids = [prepared['frames'][index]['id'] for index in sorted(chosen)]
    reference = prepared['frames'][first]['id']
    union = np.any(np.stack(quality_maps), axis=0)
    selected_coverage = float(covered.mean())
    total_coverage = float(union.mean())
    loss = max(0, total_coverage - selected_coverage)
    return {'selected_frame_ids': selected_ids, 'suggested_reference': reference, 'coverage_fraction': selected_coverage, 'all_frame_coverage_fraction': total_coverage, 'coverage_loss_fraction': loss, 'incremental_gains': incremental, 'frames': rows, 'note': '贪心保留互补的未截断像素；不适用于景深/降噪序列筛选，也不保证HDR辐射精度最优。'}


def apply_selection(store, assets, key, revision, body):
    project = store.get(key)
    if project['settings']['mode'] not in ('fusion', 'hdr', 'reference'):
        raise v.Invalid('自动曝光帧筛选只适用于曝光融合/HDR/参考帧模式')
    plan = select_bracket(project, assets, body)
    selected = set(plan['selected_frame_ids'])
    with store.edit(key, revision, '按曝光覆盖筛选照片') as current:
        for frame in current['frames']:
            frame['enabled'] = frame['id'] in selected
        current['reference'] = plan['suggested_reference']
    return {'project': store.get(key), 'selection': plan}


def exposure_coverage_map(project, assets, body):
    copy = deepcopy(project)
    copy['settings']['size'] = min(768, copy['settings']['size'])
    prepared = prepare(copy, assets)
    low = v.number(body.get('low', .05), '覆盖低点', 0, .4)
    high = v.number(body.get('high', .95), '覆盖高点', .6, 1)
    counts = np.zeros(prepared['images'][0].shape[:2], np.float32)
    reliability = np.zeros_like(counts)
    for image, valid in zip(prepared['images'], prepared['validities']):
        gray = luminance(image)
        exposed = (gray >= low) & (gray <= high)
        counts += exposed * valid
        reliability = np.maximum(reliability, np.maximum(0, 1 - np.abs(gray - .5) * 2) * valid)
    missing = (counts < .5).astype(np.float32)
    return {'coverage': counts / len(prepared['images']), 'unrecoverable': missing, 'best_exposedness': reliability}
