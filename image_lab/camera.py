import cv2
import numpy as np
from . import validation as v
from .arrays import luminance


def detect_board(image, columns=9, rows=6):
    gray = np.rint(np.clip(luminance(image), 0, 1) * 255).astype(np.uint8)
    found, corners = cv2.findChessboardCornersSB(gray, (columns, rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return {'found': False, 'corners': [], 'size': [gray.shape[1], gray.shape[0]]}
    return {'found': True, 'corners': corners.reshape(-1, 2).tolist(), 'size': [gray.shape[1], gray.shape[0]], 'columns': columns, 'rows': rows}


def calibrate_camera(images, body):
    columns = v.integer(body.get('columns', 9), '棋盘内角点列数', 3, 20)
    rows = v.integer(body.get('rows', 6), '棋盘内角点行数', 3, 20)
    square = v.number(body.get('square_size', 1), '格子边长', .001, 1000)
    if len(images) < 3 or len(images) > 24:
        raise v.Invalid('镜头标定需要3–24张不同姿态棋盘图')
    points = np.zeros((columns * rows, 3), np.float32)
    points[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * square
    object_points, image_points, detected = [], [], []
    size = None
    for index, image in enumerate(images):
        result = detect_board(image, columns, rows)
        detected.append({'index': index, 'found': result['found']})
        if not result['found']:
            continue
        if size is not None and size != tuple(result['size']):
            raise v.Invalid('棋盘图像尺寸必须相同')
        size = tuple(result['size'])
        object_points.append(points.copy())
        image_points.append(np.asarray(result['corners'], np.float32).reshape(-1, 1, 2))
    if len(image_points) < 3:
        raise v.Invalid('有效棋盘不足3张')
    rms, matrix, distortion, rotations, translations = cv2.calibrateCamera(object_points, image_points, size, None, None)
    reprojection = []
    for obj, observed, rotation, translation in zip(object_points, image_points, rotations, translations):
        projected, _ = cv2.projectPoints(obj, rotation, translation, matrix, distortion)
        reprojection.append(float(np.sqrt(np.mean(np.sum((observed - projected) ** 2, axis=2)))))
    coefficients = distortion.ravel()
    return {'rms_reprojection_px': float(rms), 'camera_matrix': matrix.tolist(), 'distortion': coefficients.tolist(), 'per_image_error_px': reprojection, 'detected': detected, 'size': list(size), 'suggested_lens': {'enabled': True, 'k1': float(np.clip(coefficients[0], -.5, .5)), 'k2': float(np.clip(coefficients[1], -.5, .5)), 'p1': float(np.clip(coefficients[2], -.05, .05)), 'p2': float(np.clip(coefficients[3], -.05, .05)), 'center_x': float(np.clip(matrix[0, 2] / size[0], .2, .8)), 'center_y': float(np.clip(matrix[1, 2] / size[1], .2, .8)), 'focal': float(np.clip((matrix[0, 0] + matrix[1, 1]) / 2 / max(size), .3, 4))}, 'note': '单一针孔加径向/切向模型；建议多个角度与画面覆盖。建议参数受工作台允许范围裁剪。'}


def board_overlay(image, result):
    raster = np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)
    if result['found']:
        corners = np.asarray(result['corners'], np.float32).reshape(-1, 1, 2)
        cv2.drawChessboardCorners(raster, (result['columns'], result['rows']), corners, True)
    return raster.astype(np.float32) / 255
