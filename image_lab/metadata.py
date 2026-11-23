from PIL import ExifTags
from .validation import number


def rational(value):
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return float(value[0]) / max(float(value[1]), 1e-20)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def extract_exif(image):
    result = {}
    try:
        exif = image.getexif()
        tags = {ExifTags.TAGS.get(key, str(key)): value for key, value in exif.items()}
        if 34665 in exif:
            tags.update({ExifTags.TAGS.get(key, str(key)): value for key, value in exif.get_ifd(34665).items()})
    except (ValueError, TypeError, OSError, KeyError):
        return result
    numeric = ['ExposureTime', 'FNumber', 'ISOSpeedRatings', 'PhotographicSensitivity', 'ExposureBiasValue', 'FocalLength']
    for key in numeric:
        value = rational(tags.get(key))
        if value is not None and abs(value) < 1e9:
            result[key] = value
    for key in ['Make', 'Model', 'LensModel', 'DateTimeOriginal', 'DateTime']:
        value = tags.get(key)
        if isinstance(value, str):
            result[key] = value[:180]
    result['note'] = '只保存摄影参数；GPS、序列号及其他私有字段不写入工程。'
    return result


def effective_exposure(metadata):
    time = metadata.get('ExposureTime', 1)
    aperture = metadata.get('FNumber')
    iso = metadata.get('PhotographicSensitivity', metadata.get('ISOSpeedRatings'))
    if time <= 0:
        time = 1
    factor = time
    if aperture and aperture > 0:
        factor /= aperture * aperture
    if iso and iso > 0:
        factor *= iso / 100
    return min(3600., max(.000001, factor))


def exposure_seconds(frame):
    return frame['exposure'] * 2 ** frame['ev']


def exposure_table(project, assets):
    rows = []
    values = [exposure_seconds(f) for f in project['frames'] if f['enabled']]
    base = min(values) if values else 1
    import math
    for frame in project['frames']:
        asset = assets(frame['asset'])
        value = exposure_seconds(frame)
        rows.append({'id': frame['id'], 'name': frame['name'], 'enabled': frame['enabled'], 'effective_seconds': value, 'relative_ev': math.log2(value / base), 'metadata': asset.get('exif', {}), 'manual_ev': frame['ev'], 'explanation': '有效曝光包含导入时的光圈/ISO归一化；可手工修订，以比例而非绝对秒数参与HDR。'})
    return rows
