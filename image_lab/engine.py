import time
import numpy as np
from . import model
from .preparation import prepare
from .workflows import get_workflow, run_workflow
from .flow import refine
from .finishing import finish_image
from .local_adjustments import apply_all
from .arrays import srgb_decode, composite_valid
from .geometry import largest_rectangle, crop_arrays, finish_geometry, overlap_metrics


def process(project, assets, progress=None, cancelled=None):
    started = time.monotonic()
    stages = {}
    stage_started = started
    mode = project['settings']['mode']
    workflow = get_workflow(mode)
    prepared = prepare(project, assets, workflow.prepare_aligned, progress, cancelled)
    stages['prepare'] = time.monotonic() - stage_started
    if not any(np.any(valid > .001) for valid in prepared['validities']):
        from .validation import Invalid
        raise Invalid('没有有效贡献像素，请检查帧权重和排除遮罩')
    if cancelled and cancelled():
        raise InterruptedError('计算前取消')
    if progress:
        progress(.4, '计算多帧合成')
    settings = project['settings']
    stage_started = time.monotonic()
    if workflow.allow_flow:
        prepared = refine(prepared, settings['flow'], progress, cancelled)
    stages['local_registration'] = time.monotonic() - stage_started
    stage_started = time.monotonic()
    fields, report = run_workflow(workflow, prepared, settings, progress, cancelled)
    stages['compose'] = time.monotonic() - stage_started
    stage_started = time.monotonic()
    weights = fields.pop('weights', None)
    if weights is not None:
        for index, weight in enumerate(weights):
            fields['weight_' + str(index)] = weight.astype(np.float32)
    if 'linear' not in fields:
        fields['linear'] = srgb_decode(fields['image'])
    fields['before_finishing'] = fields['image'].copy()
    fields['image'] = finish_image(fields['image'], settings['finishing'])
    fields['image'] = apply_all(fields['image'], settings['local'])
    stages['postprocess'] = time.monotonic() - stage_started
    stage_started = time.monotonic()
    crop = None
    if settings['geometry']['auto_crop'] and workflow.allow_common_crop:
        common = np.min(prepared['geometric_validities'], axis=0)
        if np.any(common > .99):
            crop = largest_rectangle(common)
            fields = crop_arrays(fields, crop)
    fields, manual_crop = finish_geometry(fields, settings['geometry'])
    stages['output_geometry'] = time.monotonic() - stage_started
    stage_started = time.monotonic()
    for key, value in fields.items():
        if not np.isfinite(value).all():
            raise ValueError('计算产生非有限值：' + key)
        fields[key] = value.astype(np.float32)
    stages['output_validation'] = time.monotonic() - stage_started
    report['stage_seconds'] = stages
    report['timing_scope'] = '当前流程的实际墙钟耗时，可能包含进度回调IO；不代表独占CPU时间或峰值内存。'
    report['local_flow'] = prepared.get('flow_report', [])
    report.update(seconds=time.monotonic() - started, overlap=overlap_metrics(prepared['validities']), common_crop=crop, manual_crop=manual_crop, frame_count=len(prepared['frames']), calibration=prepared['calibration'])
    if progress:
        progress(.85, '合成完成，准备保存')
    return {'fields': fields, 'report': report, 'signature': model.signature(project), 'frame_ids': [f['id'] for f in prepared['frames']]}
