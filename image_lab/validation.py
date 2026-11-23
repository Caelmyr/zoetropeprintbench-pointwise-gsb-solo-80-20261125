import math
import re
from datetime import datetime, timezone


class Invalid(ValueError):
    pass


class Conflict(Invalid):
    pass


def object_value(value, label='参数'):
    if not isinstance(value, dict):
        raise Invalid(label + '必须是对象')
    return value


def list_value(value, label='列表', maximum=100):
    if not isinstance(value, list):
        raise Invalid(label + '必须是列表')
    if len(value) > maximum:
        raise Invalid(label + '项数过多')
    return value


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise Invalid(label + '需要数字')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise Invalid(label + '需要数字')
    if not math.isfinite(result):
        raise Invalid(label + '必须有限')
    if minimum is not None and result < minimum:
        raise Invalid(f'{label}不能小于{minimum}')
    if maximum is not None and result > maximum:
        raise Invalid(f'{label}不能大于{maximum}')
    return result


def integer(value, label, minimum=None, maximum=None):
    result = number(value, label, minimum, maximum)
    if int(result) != result:
        raise Invalid(label + '必须为整数')
    return int(result)


def boolean(value, label):
    if not isinstance(value, bool):
        raise Invalid(label + '必须是布尔值')
    return value


def text(value, label='文字', maximum=200, empty=True):
    if not isinstance(value, str):
        raise Invalid(label + '必须是文字')
    value = value.strip()
    if len(value) > maximum or (not empty and not value):
        raise Invalid(label + '长度不符合要求')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise Invalid(label + '包含控制字符')
    return value


def choice(value, label, options):
    if not isinstance(value, (str, int)) or value not in options:
        raise Invalid(label + '取值不支持')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise Invalid('编号无效')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise Invalid('素材编号无效')
    return value


def revision(body):
    return integer(object_value(body).get('revision'), '修订号', 0)


def vector(value, label, count, minimum=None, maximum=None):
    items = list_value(value, label, count)
    if len(items) != count:
        raise Invalid(label + '维数错误')
    return [number(item, label, minimum, maximum) for item in items]


def matrix(value):
    rows = list_value(value, '变换矩阵', 3)
    if len(rows) != 3:
        raise Invalid('变换矩阵必须为3×3')
    result = [vector(row, '矩阵行', 3, -20, 20) for row in rows]
    import numpy as np
    if abs(np.linalg.det(result)) < 1e-8:
        raise Invalid('矩阵不可逆')
    if abs(result[2][2]) < 1e-8:
        raise Invalid('矩阵归一化分量为零')
    return result


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def color(value):
    if not isinstance(value, str) or not re.fullmatch('#[0-9a-fA-F]{6}', value):
        raise Invalid('颜色必须为#RRGGBB')
    return [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def working_size(value):
    return integer(value, '工作长边', 128, 1536)


def alignment_settings(body):
    body = object_value(body, '配准设置')
    return {
        'method': choice(body.get('method', 'phase'), '配准方式', ['phase', 'orb', 'ecc', 'mtb']),
        'motion': choice(body.get('motion', 'affine'), '运动模型', ['translation', 'euclidean', 'affine', 'homography']),
        'features': integer(body.get('features', 2500), '特征数量', 200, 6000),
        'ratio': number(body.get('ratio', .75), '特征比率', .4, .95),
        'ransac': number(body.get('ransac', 3), 'RANSAC像素阈值', .5, 12),
        'iterations': integer(body.get('iterations', 80), '迭代次数', 10, 300),
        'epsilon': number(body.get('epsilon', .0001), '收敛阈值', .000001, .01),
        'minimum_overlap': number(body.get('minimum_overlap', .2), '最小覆盖率', .05, .95),
    }


def fusion_settings(body):
    body = object_value(body, '曝光融合设置')
    return {
        'contrast': number(body.get('contrast', 1), '对比度权重', 0, 3),
        'saturation': number(body.get('saturation', .6), '饱和度权重', 0, 3),
        'exposedness': number(body.get('exposedness', 1), '曝光权重', 0, 3),
        'sigma': number(body.get('sigma', .22), '中灰宽度', .05, .5),
        'levels': integer(body.get('levels', 5), '金字塔层数', 1, 8),
        'ghost': number(body.get('ghost', 0), '运动抑制', 0, 1),
        'ghost_threshold': number(body.get('ghost_threshold', .15), '运动阈值', .02, .8),
    }


def hdr_settings(body):
    body = object_value(body, 'HDR设置')
    return {
        'response': choice(body.get('response', 'srgb'), '响应曲线', ['srgb', 'gamma', 'linear', 'learned']),
        'gamma': number(body.get('gamma', 2.2), '响应Gamma', 1, 3.5),
        'weight': choice(body.get('weight', 'triangle'), '辐射权重', ['triangle', 'gaussian', 'uniform']),
        'ghost': number(body.get('ghost', 0), 'HDR运动抑制', 0, 1),
        'clip_low': number(body.get('clip_low', .01), '暗部截断', 0, .2),
        'clip_high': number(body.get('clip_high', .99), '亮部截断', .8, 1),
    }


def focus_settings(body):
    body = object_value(body, '景深设置')
    return {
        'metric': choice(body.get('metric', 'laplacian'), '清晰度指标', ['laplacian', 'tenengrad', 'variance', 'modified_laplacian']),
        'radius': number(body.get('radius', 2), '清晰度平滑', .3, 12),
        'power': number(body.get('power', 3), '选择锐度', .5, 10),
        'levels': integer(body.get('levels', 5), '景深金字塔', 1, 8),
        'consistency': integer(body.get('consistency', 3), '索引中值窗口', 1, 15),
        'method': choice(body.get('method', 'pyramid'), '景深合成', ['pyramid', 'weighted', 'hard']),
    }


def denoise_settings(body):
    body = object_value(body, '降噪设置')
    return {
        'method': choice(body.get('method', 'sigma'), '降噪方式', ['mean', 'median', 'sigma', 'trimmed', 'weighted']),
        'sigma': number(body.get('sigma', 2), '离群阈值', .5, 5),
        'iterations': integer(body.get('iterations', 2), '剔除轮数', 1, 5),
        'trim': number(body.get('trim', .15), '截尾比例', 0, .4),
        'linear': boolean(body.get('linear', True), '线性空间降噪'),
    }


def tone_settings(body):
    body = object_value(body, '色调设置')
    return {
        'operator': choice(body.get('operator', 'reinhard'), '色调映射', ['reinhard', 'filmic', 'local', 'linear', 'drago']),
        'exposure': number(body.get('exposure', 0), '曝光EV', -6, 6),
        'key': number(body.get('key', .18), '中灰关键值', .02, 1),
        'white': number(body.get('white', 4), '白点', .2, 20),
        'saturation': number(body.get('saturation', 1), '色调饱和度', 0, 2),
        'local_radius': number(body.get('local_radius', 12), '局部映射半径', 1, 48),
        'detail': number(body.get('detail', 1), '细节保留', 0, 2),
    }
