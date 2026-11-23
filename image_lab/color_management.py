import io
import hashlib
from PIL import ImageCms
from . import validation as v


def profile_description(raw):
    if not raw:
        return {'embedded': False, 'assumed_space': 'sRGB', 'note': '未嵌入ICC，按sRGB解释。'}
    if len(raw) > 4 * 1024 * 1024:
        raise v.Invalid('ICC配置文件超过4MB')
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
        name = ImageCms.getProfileName(profile).strip()
        description = ImageCms.getProfileDescription(profile).strip()
        info = ImageCms.getProfileInfo(profile).strip()
        return {'embedded': True, 'name': name[:200], 'description': description[:400], 'info': info[:1000], 'sha256': hashlib.sha256(raw).hexdigest()}
    except (ImageCms.PyCMSError, OSError, ValueError) as error:
        return {'embedded': True, 'valid': False, 'error': str(error)[:200], 'note': 'ICC无法读取，按已解码RGB数值解释。'}


def convert_to_srgb(image):
    raw = image.info.get('icc_profile')
    report = profile_description(raw)
    if not raw or report.get('valid') is False:
        report['converted'] = False
        return image, report
    if image.mode not in ('RGB', 'RGBA', 'CMYK', 'LAB', 'L'):
        report['converted'] = False
        report['note'] = '此Pillow模式不通过8位CMS转换，以保留原始灰度位深。'
        return image, report
    alpha = image.getchannel('A') if image.mode == 'RGBA' else None
    source = image.convert('RGB') if image.mode == 'RGBA' else image
    try:
        input_profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
        output_profile = ImageCms.createProfile('sRGB')
        converted = ImageCms.profileToProfile(source, input_profile, output_profile, outputMode='RGB', renderingIntent=0)
        if alpha is not None:
            converted.putalpha(alpha)
        report['converted'] = True
        report['output_space'] = 'sRGB'
        return converted, report
    except (ImageCms.PyCMSError, OSError, ValueError) as error:
        report['converted'] = False
        report['error'] = str(error)[:200]
        report['note'] = 'ICC变换失败，保留解码颜色并记录提示。'
        return image, report


def srgb_profile_bytes():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def output_color_report(metadata, fields):
    linear = fields.get('linear')
    maximum = float(linear.max()) if linear is not None else 1
    return {'display': {'space': 'sRGB', 'white_point': 'D65', 'channels': 'RGB', 'range': [0, 1], 'transfer': 'IEC sRGB piecewise'}, 'linear': {'space': 'linear RGB using sRGB primaries', 'maximum': maximum, 'absolute_luminance_calibrated': False}, 'icc_tagged_exports': ['8bit PNG RGB', 'JPEG'], 'untagged_exports': ['16bit RGB PNG: sRGB declared in manifest', 'HDR: linear relative radiance'], 'source_revision': metadata['source_revision'], 'warning': 'HDR相对辐射与色调映射显示图不同；颜色统计不等于经过仪器校准的色度测量。'}


def orient_array(field, orientation):
    import numpy as np
    if orientation == 2:
        field = np.fliplr(field)
    elif orientation == 3:
        field = np.rot90(field, 2)
    elif orientation == 4:
        field = np.flipud(field)
    elif orientation == 5:
        field = np.swapaxes(field, 0, 1)
    elif orientation == 6:
        field = np.rot90(field, -1)
    elif orientation == 7:
        field = np.flip(np.swapaxes(field, 0, 1), axis=(0, 1))
    elif orientation == 8:
        field = np.rot90(field, 1)
    return np.ascontiguousarray(field)
