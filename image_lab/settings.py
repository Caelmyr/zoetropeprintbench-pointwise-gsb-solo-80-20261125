from copy import deepcopy
import json
from . import validation as v
from .lens import settings as lens_settings
from .flow import settings as flow_settings
from .trails import settings as trails_settings
from .drizzle import settings as drizzle_settings
from .local_adjustments import adjustment_spec
from .workflows import workflows, get_workflow


def finishing(body):
    body = v.object_value(body, '后期设置')
    return {
        'exposure': v.number(body.get('exposure', 0), '后期曝光EV', -4, 4),
        'contrast': v.number(body.get('contrast', 1), '对比度', .2, 3),
        'saturation': v.number(body.get('saturation', 1), '饱和度', 0, 2),
        'temperature': v.number(body.get('temperature', 0), '色温偏移', -1, 1),
        'tint': v.number(body.get('tint', 0), '色调偏移', -1, 1),
        'shadows': v.number(body.get('shadows', 0), '阴影', -1, 1),
        'highlights': v.number(body.get('highlights', 0), '高光', -1, 1),
        'black': v.number(body.get('black', 0), '黑点', 0, .4),
        'white': v.number(body.get('white', 1), '白点', .6, 1),
        'gamma': v.number(body.get('gamma', 1), '后期Gamma', .3, 3),
        'sharpen': v.number(body.get('sharpen', 0), '锐化', 0, 3),
        'clarity': v.number(body.get('clarity', 0), '局部清晰度', -1, 1),
        'vignette': v.number(body.get('vignette', 0), '暗角', -1, 1),
        'clahe': v.number(body.get('clahe', 0), '局部均衡', 0, 4),
        'curve': curve(body.get('curve', [[0, 0], [1, 1]])),
    }


def curve(value):
    result = [v.vector(item, '曲线点', 2, 0, 1) for item in v.list_value(value, '曲线', 12)]
    if len(result) < 2 or result[0][0] != 0 or result[-1][0] != 1:
        raise v.Invalid('曲线必须从x=0到x=1且至少两个点')
    if any(a[0] >= b[0] for a, b in zip(result, result[1:])):
        raise v.Invalid('曲线横坐标必须递增')
    return result


def calibration(body):
    body = v.object_value(body, '校正设置')
    dark = body.get('dark')
    flat = body.get('flat')
    if dark:
        v.sha(dark)
    if flat:
        v.sha(flat)
    return {
        'enabled': v.boolean(body.get('enabled', False), '启用暗场平场校正'),
        'dark': dark or None,
        'flat': flat or None,
        'dark_scale': v.number(body.get('dark_scale', 1), '暗场系数', 0, 8),
        'flat_strength': v.number(body.get('flat_strength', 1), '平场强度', 0, 1),
        'hot_threshold': v.number(body.get('hot_threshold', .05), '坏点阈值', .001, .5),
        'hot_correct': v.boolean(body.get('hot_correct', False), '修复坏点'),
    }


def panorama(body):
    body = v.object_value(body, '拼接设置')
    return {
        'blend': v.choice(body.get('blend', 'multiband'), '拼接融合', ['feather', 'multiband', 'overwrite', 'seam']),
        'levels': v.integer(body.get('levels', 5), '拼接金字塔', 1, 8),
        'feather': v.number(body.get('feather', 40), '羽化宽度', 2, 200),
        'gain': v.boolean(body.get('gain', True), '重叠区域增益补偿'),
        'crop': v.choice(body.get('crop', 'bounds'), '拼接裁切', ['bounds', 'largest_rectangle', 'none']),
        'max_edge': v.integer(body.get('max_edge', 3072), '拼接画布长边限制', 512, 4096),
    }


def geometry(body):
    body = v.object_value(body, '输出几何')
    crop = body.get('crop', [0, 0, 1, 1])
    crop = v.vector(crop, '归一化裁切框', 4, 0, 1)
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise v.Invalid('裁切框必须有正宽高')
    return {
        'crop': crop,
        'rotation': v.choice(body.get('rotation', 0), '输出旋转', [0, 90, 180, 270]),
        'flip_x': v.boolean(body.get('flip_x', False), '水平翻转'),
        'flip_y': v.boolean(body.get('flip_y', False), '垂直翻转'),
        'auto_crop': v.boolean(body.get('auto_crop', True), '共同覆盖裁切'),
    }


def all_settings(body):
    body = v.object_value(body, '工程设置')
    allowed = {'size', 'mode', 'alignment', 'fusion', 'hdr', 'focus', 'denoise', 'tone', 'finishing', 'calibration', 'panorama', 'geometry', 'lens', 'flow', 'trails', 'drizzle', 'local'}
    extensions = [item for item in workflows() if not item.builtin]
    allowed.update(item.settings_section for item in extensions)
    if set(body) - allowed:
        raise v.Invalid('存在未知工程设置')
    result = {
        'local': [adjustment_spec(item) for item in v.list_value(body.get('local', []), '局部调整层', 16)],
        'drizzle': drizzle_settings(body.get('drizzle', {})),
        'lens': lens_settings(body.get('lens', {})),
        'flow': flow_settings(body.get('flow', {})),
        'trails': trails_settings(body.get('trails', {})),
        'size': v.working_size(body.get('size', 768)),
        'mode': get_workflow(body.get('mode', 'fusion')).key,
        'alignment': v.alignment_settings(body.get('alignment', {})),
        'fusion': v.fusion_settings(body.get('fusion', {})),
        'hdr': v.hdr_settings(body.get('hdr', {})),
        'focus': v.focus_settings(body.get('focus', {})),
        'denoise': v.denoise_settings(body.get('denoise', {})),
        'tone': v.tone_settings(body.get('tone', {})),
        'finishing': finishing(body.get('finishing', {})),
        'calibration': calibration(body.get('calibration', {})),
        'panorama': panorama(body.get('panorama', {})),
        'geometry': geometry(body.get('geometry', {})),
    }
    for workflow in extensions:
        section = workflow.settings_section
        values = v.object_value(body.get(section, {}), '扩展流程参数')
        checked = workflow.parameter_validator(deepcopy(values))
        checked = v.object_value(checked, '已验证扩展流程参数')
        try:
            json.dumps(checked, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise v.Invalid('扩展流程参数需要可保存的有限JSON数据') from error
        result[section] = deepcopy(checked)
    return result


def merge_settings(original, patch):
    patch = v.object_value(patch, '设置更新')
    merged = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return all_settings(merged)
