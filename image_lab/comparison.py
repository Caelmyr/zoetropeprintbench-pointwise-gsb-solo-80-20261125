import numpy as np
import cv2
from .arrays import luminance, blur, resize, normalize, checkerboard
from .statistics import image_report
from . import validation as v


def ssim_map(a, b, sigma=1.5):
    a = luminance(a) if a.ndim == 3 else a
    b = luminance(b) if b.ndim == 3 else b
    mu_a, mu_b = blur(a, sigma), blur(b, sigma)
    var_a = np.maximum(blur(a * a, sigma) - mu_a ** 2, 0)
    var_b = np.maximum(blur(b * b, sigma) - mu_b ** 2, 0)
    covariance = blur(a * b, sigma) - mu_a * mu_b
    top = (2 * mu_a * mu_b + .01 ** 2) * (2 * covariance + .03 ** 2)
    bottom = (mu_a ** 2 + mu_b ** 2 + .01 ** 2) * (var_a + var_b + .03 ** 2)
    return np.clip(top / np.maximum(bottom, 1e-12), -1, 1).astype(np.float32)


def compare(a, b, mask=None):
    if a.shape != b.shape:
        raise v.Invalid('对比要求相同尺寸')
    selected = mask > .5 if mask is not None else np.ones(a.shape[:2], bool)
    if not selected.any():
        raise v.Invalid('没有有效对比像素')
    difference = a - b
    mse = float(np.mean(difference[selected] ** 2))
    lab_a = cv2.cvtColor(a.astype(np.float32), cv2.COLOR_RGB2LAB)
    lab_b = cv2.cvtColor(b.astype(np.float32), cv2.COLOR_RGB2LAB)
    delta = np.linalg.norm(lab_a - lab_b, axis=2)
    return {'mae': float(np.mean(np.abs(difference[selected]))), 'rmse': float(np.sqrt(mse)), 'psnr_db': float(10 * np.log10(1 / mse)) if mse > 1e-12 else 120., 'ssim': float(ssim_map(a, b)[selected].mean()), 'delta_e76_mean': float(delta[selected].mean()), 'delta_e76_p95': float(np.percentile(delta[selected], 95)), 'pixels': int(selected.sum()), 'note': 'PSNR/SSIM用于同场景同尺寸比较，不是真值画质评分。'}


def difference_image(a, b, gain=4):
    values = np.clip(np.mean(np.abs(a - b), axis=2) * gain, 0, 1)
    encoded = np.rint(values * 255).astype(np.uint8)
    return cv2.applyColorMap(encoded, cv2.COLORMAP_TURBO)[:, :, ::-1].astype(np.float32) / 255


def split_view(a, b, fraction=.5, orientation='vertical'):
    if a.shape != b.shape:
        raise v.Invalid('分屏要求相同尺寸')
    result = a.copy()
    if orientation == 'horizontal':
        position = round(a.shape[0] * fraction)
        result[position:] = b[position:]
        if 0 <= position < a.shape[0]:
            result[position:position + 1] = [1, .8, .4]
    else:
        position = round(a.shape[1] * fraction)
        result[:, position:] = b[:, position:]
        if 0 <= position < a.shape[1]:
            result[:, position:position + 1] = [1, .8, .4]
    return result


def checker_compare(a, b, cells=8):
    if a.shape != b.shape:
        raise v.Invalid('棋盘对比要求同尺寸')
    h, w = a.shape[:2]
    yy, xx = np.indices((h, w))
    selector = (xx // max(1, w // cells) + yy // max(1, h // cells)) % 2
    return np.where(selector[:, :, None] == 0, a, b)


def registration_overlay(a, b, opacity=.5):
    if a.shape != b.shape:
        raise v.Invalid('配准叠加要求同尺寸')
    gray_a = luminance(a)
    gray_b = luminance(b)
    color = np.stack([gray_a, gray_b, gray_b], axis=2)
    return color * opacity + (a + b) / 2 * (1 - opacity)


def blink_animation(a, b, duration=500):
    from PIL import Image
    import io
    frames = [Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)) for image in [a, b]]
    output = io.BytesIO()
    frames[0].save(output, 'PNG', save_all=True, append_images=frames[1:], duration=duration, loop=0)
    return output.getvalue()


def line_profile(image, start, end, samples=256):
    x = np.linspace(start[0], end[0], samples, dtype=np.float32)[None]
    y = np.linspace(start[1], end[1], samples, dtype=np.float32)[None]
    values = cv2.remap(image, x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[0]
    gray = values @ np.array([.2126, .7152, .0722])
    return {'rgb': values.tolist(), 'luminance': gray.tolist(), 'samples': samples, 'start': start, 'end': end}
