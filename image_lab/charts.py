import io
import numpy as np
from PIL import Image, ImageDraw
from .codecs import png_bytes
from .statistics import histogram
from .arrays import luminance


def histogram_chart(image, width=720, height=250):
    report = histogram(image, 128)
    canvas = Image.new('RGB', (width, height), '#15232c')
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = 45, 20, width - 20, height - 30
    for index in range(5):
        y = top + (bottom - top) * index / 4
        draw.line((left, y, right, y), fill='#2d424d')
    colors = ['#d5806e', '#74c1a0', '#83ace0']
    maximum = max(max(channel['counts']) for channel in report['channels'])
    for channel, color in zip(report['channels'], colors):
        points = []
        for index, count in enumerate(channel['counts']):
            x = left + (right - left) * index / 127
            y = bottom - (bottom - top) * np.sqrt(count / max(maximum, 1))
            points.append((x, y))
        draw.line(points, fill=color, width=2)
    draw.text((left, bottom + 8), '0 / shadows', fill='#a6b8c4')
    draw.text((right - 95, bottom + 8), '1 / highlights', fill='#a6b8c4')
    output = io.BytesIO()
    canvas.save(output, 'PNG')
    return output.getvalue()


def waveform(image, width=720, height=256):
    gray = luminance(image)
    h, w = gray.shape
    density = np.zeros((height, width), np.float32)
    xs = np.minimum(width - 1, np.floor(np.arange(w) / w * width).astype(int))
    for column in range(w):
        ys = np.clip(np.rint((1 - gray[:, column]) * (height - 1)), 0, height - 1).astype(int)
        np.add.at(density, (ys, np.full(h, xs[column])), 1)
    values = np.log1p(density)
    values /= max(float(values.max()), 1)
    output = np.zeros((height, width, 3), np.float32)
    output[:, :, 0] = values * .38
    output[:, :, 1] = values
    output[:, :, 2] = values * .7
    for row in [0, height // 4, height // 2, height * 3 // 4, height - 1]:
        output[row] = np.maximum(output[row], [.13, .2, .23])
    return png_bytes(output)


def vectorscope(image, size=400):
    values = image.reshape(-1, 3)[::max(1, image.size // 3 // 150000)]
    cb = -.168736 * values[:, 0] - .331264 * values[:, 1] + .5 * values[:, 2]
    cr = .5 * values[:, 0] - .418688 * values[:, 1] - .081312 * values[:, 2]
    x = np.clip(np.rint((cb + .5) * (size - 1)), 0, size - 1).astype(int)
    y = np.clip(np.rint((.5 - cr) * (size - 1)), 0, size - 1).astype(int)
    density = np.zeros((size, size), np.float32)
    np.add.at(density, (y, x), 1)
    density = np.log1p(density)
    density /= max(float(density.max()), 1)
    output = np.stack([density * .9, density, density * .6], axis=2)
    output[size // 2] = np.maximum(output[size // 2], [.15, .2, .2])
    output[:, size // 2] = np.maximum(output[:, size // 2], [.15, .2, .2])
    return png_bytes(output)
