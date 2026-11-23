import numpy as np
from .arrays import luminance, normalize, blur


def power_spectrum(image, bins=48):
    gray = luminance(image)
    h, w = gray.shape
    window = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    centered = (gray - gray.mean()) * window
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(centered))) ** 2
    fx = np.fft.fftshift(np.fft.fftfreq(w))
    fy = np.fft.fftshift(np.fft.fftfreq(h))
    radius = np.hypot(fx[None, :], fy[:, None])
    edges = np.linspace(0, .5, bins + 1)
    counts, _ = np.histogram(radius, bins=edges)
    total, _ = np.histogram(radius, bins=edges, weights=spectrum)
    average = np.divide(total, counts, out=np.zeros_like(total), where=counts > 0)
    visual = normalize(np.log1p(spectrum))
    return {'frequency_cycles_per_pixel': ((edges[:-1] + edges[1:]) / 2).tolist(), 'power': average.tolist(), 'window': 'Hann', 'interpretation': '功率谱包含纹理与噪声，不能单独判断降噪质量。'}, visual


def autocorrelation(image):
    gray = luminance(image)
    gray -= gray.mean()
    spectrum = np.fft.fft2(gray)
    correlation = np.fft.fftshift(np.fft.ifft2(spectrum * np.conj(spectrum)).real)
    center = max(float(correlation[gray.shape[0] // 2, gray.shape[1] // 2]), 1e-8)
    correlation /= center
    h, w = gray.shape
    return {'horizontal': correlation[h // 2].tolist(), 'vertical': correlation[:, w // 2].tolist(), 'center': [w // 2, h // 2]}, np.clip(correlation * .5 + .5, 0, 1).astype(np.float32)


def multiscale_detail(image, scales=(1, 2, 4, 8, 16)):
    gray = luminance(image)
    result = []
    previous = gray
    for scale in scales:
        smooth = blur(gray, scale)
        detail = previous - smooth
        result.append({'sigma': scale, 'mean_abs': float(np.mean(np.abs(detail))), 'rms': float(np.sqrt(np.mean(detail ** 2))), 'p95': float(np.percentile(np.abs(detail), 95))})
        previous = smooth
    return result


def edge_spread(image, bounds, orientation='vertical'):
    x0, y0, x1, y1 = bounds
    gray = luminance(image[y0:y1, x0:x1])
    profile = gray.mean(axis=0 if orientation == 'vertical' else 1)
    low, high = np.percentile(profile, [5, 95])
    normalized = (profile - low) / max(high - low, 1e-6)
    derivative = np.diff(normalized)
    derivative = np.abs(derivative)
    derivative *= np.hanning(max(1, len(derivative)))
    frequency = np.fft.rfftfreq(max(1, len(derivative)))
    mtf = np.abs(np.fft.rfft(derivative))
    mtf /= max(float(mtf[0]), 1e-8)
    crossings = np.where(mtf < .5)[0]
    mtf50 = float(frequency[crossings[0]]) if len(crossings) else None
    return {'profile': profile.tolist(), 'normalized_profile': normalized.tolist(), 'frequency': frequency.tolist(), 'mtf': mtf.tolist(), 'mtf50_approx': mtf50, 'note': '区域应含近似直边；简单行/列平均并非ISO斜边标定方法，数值仅作内部对比。'}
