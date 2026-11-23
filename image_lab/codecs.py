import io
import warnings
import numpy as np
import cv2
from PIL import Image, ImageOps
from . import validation as v
from .arrays import finite_rgb, luminance, normalize, composite_valid
from .metadata import extract_exif
from .color_management import convert_to_srgb, srgb_profile_bytes, orient_array


def decode_image(raw):
    if len(raw) > 48 * 1024 * 1024:
        raise v.Invalid('单张图片不能超过48MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if min(image.size) < 32 or max(image.size) > 4096:
                    raise v.Invalid('照片尺寸须为32–4096像素')
                if image.width * image.height > 12000000:
                    raise v.Invalid('单张照片不得超过1200万像素')
                metadata = extract_exif(image)
                orientation = image.getexif().get(274, 1)
                original_mode = image.mode
                image = ImageOps.exif_transpose(image)
                image, color_report = convert_to_srgb(image)
                high_precision = None
                is_16_png = raw[:8] == b'\x89PNG\r\n\x1a\n' and len(raw) > 25 and raw[24] == 16
                is_tiff = raw[:4] in (b'II*\x00', b'MM\x00*')
                if is_16_png or is_tiff:
                    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                    if decoded is not None and decoded.dtype == np.uint16 and decoded.ndim == 3:
                        high_precision = decoded.astype(np.float32) / 65535
                if high_precision is not None:
                    field = high_precision[:, :, :3][:, :, ::-1]
                    if high_precision.shape[2] == 4:
                        field *= high_precision[:, :, 3:4]
                    field = orient_array(field, orientation)
                    color_report['converted'] = False
                    color_report['note'] = '16位RGB数值保留；按sRGB解释，嵌入的非sRGB配置请先转换。'
                elif original_mode in ('I;16', 'I;16B', 'I;16L', 'I'):
                    field = np.asarray(image, dtype=np.float32) / 65535
                    field = np.repeat(field[:, :, None], 3, axis=2)
                elif original_mode == 'F':
                    field = np.repeat(np.asarray(image, dtype=np.float32)[:, :, None], 3, axis=2)
                else:
                    rgba = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255
                    field = rgba[:, :, :3] * rgba[:, :, 3:4]
                field = finite_rgb(field)
                return np.clip(field, 0, 1), {'exif': metadata, 'mode': original_mode, 'orientation_applied': True, 'color_management': color_report}
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise v.Invalid('图片无法解码或尺寸超限') from error


def png_bytes(image, bits=8):
    image = np.clip(image, 0, 1)
    stream = io.BytesIO()
    if bits == 16:
        array = np.rint(image * 65535).astype(np.uint16)
        if array.ndim == 3:
            ok, encoded = cv2.imencode('.png', array[:, :, ::-1])
            if not ok:
                raise v.Invalid('16位PNG编码失败')
            return encoded.tobytes()
        raster = Image.fromarray(array)
    else:
        raster = Image.fromarray(np.rint(image * 255).astype(np.uint8))
    raster.save(stream, format='PNG', icc_profile=srgb_profile_bytes() if image.ndim == 3 else None)
    return stream.getvalue()


def jpeg_bytes(image, quality=92):
    quality = v.integer(quality, 'JPEG质量', 50, 100)
    stream = io.BytesIO()
    Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)).save(stream, 'JPEG', quality=quality, optimize=True, icc_profile=srgb_profile_bytes())
    return stream.getvalue()


def tiff_bytes(image, bits=16):
    if bits == 32:
        if image.ndim != 2:
            raise v.Invalid('32位TIFF仅用于单通道辅助图')
        raster = Image.fromarray(image.astype(np.float32))
        stream = io.BytesIO()
        raster.save(stream, 'TIFF')
        return stream.getvalue()
    array = np.rint(np.clip(image, 0, 1) * 65535).astype(np.uint16)
    ok, encoded = cv2.imencode('.tiff', array[:, :, ::-1] if array.ndim == 3 else array)
    if not ok:
        raise v.Invalid('TIFF编码失败')
    return encoded.tobytes()


def radiance_bytes(linear):
    ok, encoded = cv2.imencode('.hdr', np.maximum(linear, 0).astype(np.float32)[:, :, ::-1])
    if not ok:
        raise v.Invalid('Radiance HDR编码失败')
    return encoded.tobytes()


def float_archive(fields):
    stream = io.BytesIO()
    np.savez_compressed(stream, **fields)
    return stream.getvalue()


def visualization(field, color='gray'):
    if field.ndim == 3:
        return np.clip(field, 0, 1)
    field = np.clip(field, 0, 1)
    if color == 'heat':
        data = cv2.applyColorMap(np.rint(field * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        return data[:, :, ::-1].astype(np.float32) / 255
    return np.repeat(field[:, :, None], 3, axis=2)


def thumbnail(image, size=240):
    raster = Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8))
    raster.thumbnail((size, size), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    raster.save(stream, 'JPEG', quality=85)
    return stream.getvalue()
