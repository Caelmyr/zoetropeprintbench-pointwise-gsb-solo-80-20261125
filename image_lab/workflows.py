from dataclasses import dataclass, field
from typing import Callable
import re
import json
import numpy as np
from . import validation as v


@dataclass(frozen=True)
class Workflow:
    key: str
    title: str
    description: str
    settings_section: str
    runner: Callable
    prepare_aligned: bool = True
    allow_flow: bool = True
    allow_common_crop: bool = True
    minimum_frames: int = 1
    parameter_validator: Callable | None = None
    parameter_labels: dict = field(default_factory=dict)
    parameter_options: dict = field(default_factory=dict)
    builtin: bool = False

    def public(self):
        return {
            'key': self.key,
            'title': self.title,
            'description': self.description,
            'settings_section': self.settings_section,
            'prepare_aligned': self.prepare_aligned,
            'allow_flow': self.allow_flow,
            'allow_common_crop': self.allow_common_crop,
            'minimum_frames': self.minimum_frames,
            'parameter_labels': dict(self.parameter_labels),
            'parameter_options': dict(self.parameter_options),
            'builtin': self.builtin,
        }


_REGISTRY = {}
_RESERVED_SECTIONS = {'size', 'mode', 'local', 'geometry', 'alignment', 'calibration', 'lens', 'flow', 'finishing', 'tone'}


def register(workflow):
    if not isinstance(workflow, Workflow):
        raise TypeError('需要Workflow定义')
    if not re.fullmatch('[a-z][a-z0-9_]{1,39}', workflow.key):
        raise ValueError('流程标识格式无效')
    if workflow.key in _REGISTRY:
        raise ValueError('流程标识已注册：' + workflow.key)
    if not callable(workflow.runner):
        raise ValueError('流程必须有实际处理函数')
    if not workflow.title or not workflow.description:
        raise ValueError('流程需要用户可读名称和说明')
    if not 1 <= workflow.minimum_frames <= 24:
        raise ValueError('流程最小帧数无效')
    if not workflow.builtin:
        if not re.fullmatch('[a-z][a-z0-9_]{1,39}', workflow.settings_section):
            raise ValueError('参数组标识格式无效')
        if workflow.settings_section in _RESERVED_SECTIONS:
            raise ValueError('扩展流程不能占用公共参数组')
        if any(item.settings_section == workflow.settings_section for item in _REGISTRY.values()):
            raise ValueError('扩展流程需要独立参数组')
        if not callable(workflow.parameter_validator):
            raise ValueError('扩展流程必须验证自身参数')
    _REGISTRY[workflow.key] = workflow
    return workflow


def get_workflow(key):
    if not isinstance(key, str) or key not in _REGISTRY:
        raise v.Invalid('合成流程尚未注册')
    return _REGISTRY[key]


def workflows():
    return tuple(_REGISTRY.values())


def public_workflows():
    return [item.public() for item in workflows()]


def validate_output(output, frame_count):
    if not isinstance(output, tuple) or len(output) != 2:
        raise v.Invalid('流程需要返回图像字段及处理报告')
    fields, report = output
    if not isinstance(fields, dict) or 'image' not in fields or 'valid' not in fields:
        raise v.Invalid('流程缺少显示图或有效覆盖图')
    if not isinstance(report, dict):
        raise v.Invalid('流程报告必须是对象')
    try:
        json.dumps(report, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise v.Invalid('流程报告需要可保存的有限JSON数据') from error
    image = np.asarray(fields['image'])
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 1:
        raise v.Invalid('流程显示图必须为H×W×3')
    if image.shape[0] * image.shape[1] > 12000000:
        raise v.Invalid('流程输出超过1200万像素')
    for name, value in fields.items():
        if not re.fullmatch('[a-z][a-z0-9_]{0,49}', name):
            raise v.Invalid('流程图像字段名称无效')
        if not isinstance(value, np.ndarray) or value.dtype.kind not in 'fiu':
            raise v.Invalid('流程图像字段必须为数值数组：' + name)
        expected = (frame_count, *image.shape[:2]) if name == 'weights' else image.shape[:2]
        actual = value.shape if name == 'weights' else value.shape[:2]
        if actual != expected:
            raise v.Invalid('流程辅助图与显示图尺寸不一致：' + name)
        if not np.isfinite(value).all():
            raise v.Invalid('流程产生非有限数值：' + name)
    valid = fields['valid']
    if valid.ndim != 2 or valid.min() < 0 or valid.max() > 1:
        raise v.Invalid('流程有效覆盖必须是0到1的二维图')
    if image.min() < 0 or image.max() > 1:
        raise v.Invalid('流程显示图必须已映射到0到1')
    if 'linear' in fields and fields['linear'].shape != image.shape:
        raise v.Invalid('线性图与显示图通道数不一致')
    return fields, report


def run_workflow(workflow, prepared, settings, progress=None, cancelled=None):
    if len(prepared['frames']) < workflow.minimum_frames:
        raise v.Invalid(workflow.title + '至少需要' + str(workflow.minimum_frames) + '张启用照片')
    if cancelled and cancelled():
        raise InterruptedError('流程执行前取消')
    output = workflow.runner(prepared, settings, progress, cancelled)
    return validate_output(output, len(prepared['frames']))


def _fusion(prepared, settings, progress, cancelled):
    from .fusion import fuse
    return fuse(prepared, settings['fusion'])


def _hdr(prepared, settings, progress, cancelled):
    from .hdr import merge_hdr
    from .tone import tone_map
    fields, report = merge_hdr(prepared, settings['hdr'])
    fields['image'] = tone_map(fields['linear'], settings['tone'])
    return fields, report


def _focus(prepared, settings, progress, cancelled):
    from .focus import focus_stack
    return focus_stack(prepared, settings['focus'])


def _denoise(prepared, settings, progress, cancelled):
    from .denoise import denoise_stack
    return denoise_stack(prepared, settings['denoise'])


def _panorama(prepared, settings, progress, cancelled):
    from .panorama import stitch
    return stitch(prepared, settings['panorama'], progress, cancelled)


def _trails(prepared, settings, progress, cancelled):
    from .trails import combine
    return combine(prepared, settings['trails'])


def _drizzle(prepared, settings, progress, cancelled):
    from .drizzle import reconstruct
    return reconstruct(prepared, settings['drizzle'], progress, cancelled)


def _reference(prepared, settings, progress, cancelled):
    index = prepared['reference_index']
    return {'image': prepared['images'][index].copy(), 'valid': prepared['validities'][index].copy()}, {'mode': 'reference', 'note': '参考帧后期处理'}


register(Workflow('fusion', '曝光融合', '多尺度融合曝光互补区域，输出自然的显示图。', 'fusion', _fusion, builtin=True))
register(Workflow('hdr', 'HDR 辐射合成', '使用有效曝光合并线性辐射，再进行色调映射。', 'hdr', _hdr, minimum_frames=2, builtin=True))
register(Workflow('focus', '景深堆栈', '按局部清晰度融合不同焦点照片；索引表示帧编号。', 'focus', _focus, builtin=True))
register(Workflow('denoise', '多帧降噪', '在配准序列中平均、取中值或剔除离群值。', 'denoise', _denoise, builtin=True))
register(Workflow('panorama', '平面全景拼接', '扩展画布，进行增益补偿与接缝融合。', 'panorama', _panorama, prepare_aligned=False, allow_flow=False, allow_common_crop=False, builtin=True))
register(Workflow('trails', '光轨与时序合成', '合并亮色或暗色轨迹，可按帧顺序渐隐。', 'trails', _trails, allow_flow=False, builtin=True))
register(Workflow('drizzle', '亚像素重建', '工作像素前向投影；需要真实亚像素采样。', 'drizzle', _drizzle, prepare_aligned=False, allow_flow=False, allow_common_crop=False, builtin=True))
register(Workflow('reference', '参考帧后期', '对参考帧应用校正与后期，建立处理基线。', 'finishing', _reference, builtin=True))
