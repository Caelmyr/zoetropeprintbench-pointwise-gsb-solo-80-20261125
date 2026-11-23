from copy import deepcopy
import io
import zipfile
import numpy as np
from . import validation as v
from .settings import merge_settings
from .profiles import BUILTINS
from .exporting import result_bundle, json_bytes, contact_sheet


def batch_plan(project, body):
    kind = body.get('kind', 'batch')
    result = []
    if kind == 'sweep':
        section = v.choice(body.get('section', 'tone'), '扫描参数组', ['tone', 'fusion', 'focus', 'denoise', 'finishing'])
        parameter = v.text(body.get('parameter', 'exposure'), '扫描参数名', 60, False)
        if parameter not in project['settings'][section]:
            raise v.Invalid('扫描参数不存在')
        current = project['settings'][section][parameter]
        if not isinstance(current, (float, int)) or isinstance(current, bool):
            raise v.Invalid('扫描仅支持数值参数')
        start = v.number(body.get('start', -1), '扫描起点')
        end = v.number(body.get('end', 1), '扫描终点')
        count = v.integer(body.get('count', 3), '扫描数量', 2, 8)
        if start >= end:
            raise v.Invalid('扫描起点必须小于终点')
        for value in np.linspace(start, end, count):
            if isinstance(current, int):
                value = int(round(value))
            else:
                value = float(value)
            copied = deepcopy(project)
            copied['settings'] = merge_settings(project['settings'], {section: {parameter: value}})
            result.append({'label': section + '.' + parameter + '=' + str(round(value, 4)), 'project': copied})
    else:
        profiles = v.list_value(body.get('profiles', ['balanced', 'soft']), '批处理方案', 8)
        if not profiles:
            raise v.Invalid('请选择处理方案')
        for profile in profiles:
            v.choice(profile, '内置批处理方案', BUILTINS)
            copied = deepcopy(project)
            copied['settings'] = merge_settings(project['settings'], BUILTINS[profile]['settings'])
            result.append({'label': profile, 'project': copied})
    return result


def run_batch(project, jobs, job, event, progress):
    plan = batch_plan(project, job['parameters'])
    stream = io.BytesIO()
    entries, previews, labels = [], [], []
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(plan):
            if event.is_set():
                raise InterruptedError('批处理取消，已完成结果保留')
            callback = lambda fraction, message: progress((index + fraction) / len(plan), f'{index + 1}/{len(plan)} ' + message)
            meta = jobs.results.run(item['project'], callback, event.is_set, activate=False)
            jobs.append_result(job['id'], meta['id'])
            fields, _ = jobs.results.fields(project['id'], meta['id'], False)
            data, _ = result_bundle(fields, meta, {'raw': False, 'auxiliaries': False})
            archive.writestr(f'variant-{index + 1:02d}.zip', data)
            entries.append({'index': index, 'label': item['label'], 'result_id': meta['id'], 'signature': meta['source_signature']})
            previews.append(fields['image'])
            labels.append(item['label'])
        archive.writestr('variants.json', json_bytes(entries))
        archive.writestr('contact.png', contact_sheet(previews, labels))
    return stream.getvalue(), {'variants': entries, 'note': '批次输出均独立保存，不覆盖当前工程的已选结果。'}
