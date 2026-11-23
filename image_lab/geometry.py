import cv2
import numpy as np
from scipy import ndimage as ndi
from .arrays import resize, fit_size, letterbox
from . import validation as v


def pixel_matrix(normalized, width, height):
    diagonal = np.diag([width, height, 1.])
    return diagonal @ np.asarray(normalized, dtype=np.float64) @ np.linalg.inv(diagonal)


def normalized_matrix(pixel, width, height):
    diagonal = np.diag([width, height, 1.])
    result = np.linalg.inv(diagonal) @ np.asarray(pixel, dtype=np.float64) @ diagonal
    return (result / result[2, 2]).tolist()


def warp(image, matrix, size, mask=False):
    width, height = size
    interpolation = cv2.INTER_NEAREST if mask else cv2.INTER_LINEAR
    return cv2.warpPerspective(image, np.asarray(matrix, np.float64), (width, height), flags=interpolation, borderMode=cv2.BORDER_CONSTANT).astype(np.float32)


def transformed_corners(matrix, width, height):
    corners = np.array([[[0, 0], [width, 0], [width, height], [0, height]]], np.float32)
    return cv2.perspectiveTransform(corners, np.asarray(matrix, np.float64))[0]


def canvas_bounds(matrices, width, height, maximum=4096):
    all_corners = np.concatenate([transformed_corners(m, width, height) for m in matrices], axis=0)
    if not np.isfinite(all_corners).all():
        raise v.Invalid('变换导致无效画布')
    minimum = np.floor(all_corners.min(axis=0))
    maximum_xy = np.ceil(all_corners.max(axis=0))
    extent = maximum_xy - minimum
    if np.any(extent <= 0) or np.max(extent) > 30000:
        raise v.Invalid('拼接变换超出合理范围')
    scale = min(1., maximum / max(extent))
    width_out, height_out = np.maximum(1, np.ceil(extent * scale).astype(int))
    if width_out * height_out > 12000000:
        raise v.Invalid('拼接输出超过1200万像素')
    shift = np.array([[scale, 0, -minimum[0] * scale], [0, scale, -minimum[1] * scale], [0, 0, 1]], np.float64)
    return (int(width_out), int(height_out)), [shift @ m for m in matrices], {'minimum': minimum.tolist(), 'scale': float(scale)}


def support_bounds(mask):
    y, x = np.where(mask > .5)
    if not len(x):
        raise v.Invalid('没有有效重叠区域')
    return [int(x.min()), int(y.min()), int(x.max()) + 1, int(y.max()) + 1]


def largest_rectangle(mask):
    binary = mask > .99
    heights = np.zeros(binary.shape[1], dtype=int)
    best = (0, [0, 0, 0, 0])
    for y, row in enumerate(binary):
        heights = np.where(row, heights + 1, 0)
        stack = []
        for x in range(len(heights) + 1):
            h = heights[x] if x < len(heights) else 0
            start = x
            while stack and stack[-1][1] > h:
                left, height = stack.pop()
                area = height * (x - left)
                if area > best[0]:
                    best = (int(area), [int(left), int(y - height + 1), int(x), int(y + 1)])
                start = left
            if not stack or stack[-1][1] < h:
                stack.append((start, h))
    if best[0] == 0:
        raise v.Invalid('没有完整有效矩形')
    return best[1]


def crop_arrays(fields, bounds):
    x0, y0, x1, y1 = bounds
    return {name: field[y0:y1, x0:x1].copy() for name, field in fields.items()}


def finish_geometry(fields, settings):
    first = next(iter(fields.values()))
    h, w = first.shape[:2]
    x0, y0, x1, y1 = settings['crop']
    bounds = [min(w - 1, int(x0 * w)), min(h - 1, int(y0 * h)), max(1, int(np.ceil(x1 * w))), max(1, int(np.ceil(y1 * h)))]
    result = crop_arrays(fields, bounds)
    for key, image in result.items():
        if settings['rotation']:
            image = np.rot90(image, -(settings['rotation'] // 90))
        if settings['flip_x']:
            image = np.flip(image, axis=1)
        if settings['flip_y']:
            image = np.flip(image, axis=0)
        result[key] = np.ascontiguousarray(image)
    return result, bounds


def overlap_metrics(masks):
    stack = np.stack(masks)
    count = stack.sum(axis=0)
    return {'common_fraction': float(np.mean(np.all(stack > .99, axis=0))), 'union_fraction': float(np.mean(count > .01)), 'mean_contributors': float(count.mean()), 'per_frame': [float(mask.mean()) for mask in masks]}


def feather_weight(mask, radius=40):
    binary = mask > .99
    padded = np.pad(binary, 1, constant_values=False)
    distance = ndi.distance_transform_edt(padded)[1:-1, 1:-1]
    return np.clip(distance / radius, 0, 1).astype(np.float32) * mask
