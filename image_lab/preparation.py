import numpy as np
from . import model
from .arrays import fit_size, letterbox, resize
from .geometry import pixel_matrix, warp
from .calibration import apply_calibration
from .validation import Invalid
from .lens import correct


def prepare(project, assets, aligned=True, progress=None, cancelled=None):
    frames = model.enabled_frames(project)
    reference = project['reference']
    if reference not in [frame['id'] for frame in frames]:
        raise Invalid('参考帧必须启用')
    ref = model.find_frame(project, reference)
    metadata = assets.metadata(ref['asset'])
    width, height = fit_size((metadata['height'], metadata['width']), project['settings']['size'])
    images, validities, raw_masks, matrices, reports = [], [], [], [], []
    geometric_validities = []
    for index, frame in enumerate(frames):
        if cancelled and cancelled():
            raise InterruptedError('在照片准备阶段取消')
        image = assets.field(frame['asset'])
        image, report = apply_calibration(image, project['settings']['calibration'], assets.field)
        image, lens_valid = correct(image, project['settings']['lens'])
        image, validity = letterbox(image, width, height)
        lens_valid, _ = letterbox(lens_valid, width, height)
        validity *= lens_valid
        if frame['mask']:
            mask = assets.field(frame['mask'])
            mask = resize(mask, width, height)
        else:
            mask = np.ones((height, width), np.float32)
        transform = pixel_matrix(frame['matrix'], width, height)
        if aligned:
            image = warp(image, transform, (width, height))
            validity = warp(validity, transform, (width, height))
            mask = warp(mask, transform, (width, height))
        images.append(image)
        geometric_validities.append(np.clip(validity, 0, 1))
        validities.append(np.clip(validity * mask, 0, 1) * float(frame['weight'] > 0))
        raw_masks.append(mask)
        matrices.append(transform)
        reports.append(report)
        if progress:
            progress(.1 + .25 * (index + 1) / len(frames), '准备照片 ' + str(index + 1))
    return {'frames': frames, 'images': images, 'validities': validities, 'geometric_validities': geometric_validities, 'masks': raw_masks, 'matrices': matrices, 'reference_index': next(i for i, f in enumerate(frames) if f['id'] == reference), 'size': (width, height), 'calibration': reports}
