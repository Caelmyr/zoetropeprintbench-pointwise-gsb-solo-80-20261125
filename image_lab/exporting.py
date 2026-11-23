import io
import json
import zipfile
import hashlib
import numpy as np
from . import validation as v
from .codecs import png_bytes, jpeg_bytes, tiff_bytes, radiance_bytes, float_archive, visualization
from . import model


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf8')


def result_bundle(fields, metadata, options):
    bits = v.choice(options.get('bits', 16), '导出位深', [8, 16])
    auxiliaries = v.boolean(options.get('auxiliaries', True), '包含辅助图')
    raw = v.boolean(options.get('raw', True), '包含线性浮点数据')
    files = {'composite.png': png_bytes(fields['image'], bits), 'composite.jpg': jpeg_bytes(fields['image'])}
    if auxiliaries:
        for name, field in fields.items():
            if name in ('image', 'linear', 'before_finishing'):
                continue
            files['auxiliary/' + name + '.png'] = png_bytes(visualization(field, 'heat'))
    if raw:
        files['linear-radiance.hdr'] = radiance_bytes(fields['linear'])
        files['float32-fields.npz'] = float_archive(fields)
    files['source-settings.json'] = json_bytes(metadata['project'])
    report = {key: value for key, value in metadata.items() if key not in ('project', 'statistics')}
    files['report.json'] = json_bytes(report)
    manifest = {'format': 'stacklight-result-v1', 'display_encoding': 'sRGB', 'display_bits': bits, 'linear_encoding': 'linear float32; relative units', 'mode': metadata['mode'], 'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
    files['manifest.json'] = json_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue(), manifest


def export_image(fields, format, body):
    format = v.choice(format, '图像导出格式', ['png', 'jpeg', 'tiff', 'hdr', 'npz'])
    bits = v.choice(body.get('bits', 16), '位深', [8, 16])
    if format == 'png':
        return png_bytes(fields['image'], bits), 'composite.png', 'image/png'
    if format == 'jpeg':
        return jpeg_bytes(fields['image'], body.get('quality', 92)), 'composite.jpg', 'image/jpeg'
    if format == 'tiff':
        return tiff_bytes(fields['image']), 'composite-16bit.tiff', 'image/tiff'
    if format == 'hdr':
        return radiance_bytes(fields['linear']), 'linear-radiance.hdr', 'application/octet-stream'
    return float_archive(fields), 'float32-fields.npz', 'application/octet-stream'


def project_bundle(project, assets):
    references = {frame['asset'] for frame in project['frames']}
    references.update(frame['mask'] for frame in project['frames'] if frame['mask'])
    calibration = project['settings']['calibration']
    references.update(asset for asset in [calibration['dark'], calibration['flat']] if asset)
    stream = io.BytesIO()
    manifest = {'format': 'stacklight-project-v1', 'assets': {}}
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json_bytes(model.snapshot(project)))
        for asset_id in sorted(references):
            data = assets.path(asset_id).read_bytes()
            metadata = assets.metadata(asset_id)
            archive.writestr('assets/' + asset_id + '.npz', data)
            archive.writestr('assets/' + asset_id + '.json', json_bytes(metadata))
            manifest['assets'][asset_id] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        archive.writestr('manifest.json', json_bytes(manifest))
    return stream.getvalue()


def contact_sheet(images, labels, columns=3, cell=260):
    from PIL import Image, ImageDraw
    if not images:
        raise v.Invalid('联系表不能为空')
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * cell, rows * (cell * 3 // 4 + 32)), '#14232d')
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(zip(images, labels)):
        raster = Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8))
        raster.thumbnail((cell - 12, cell * 3 // 4 - 8), Image.Resampling.LANCZOS)
        x = index % columns * cell + (cell - raster.width) // 2
        y = index // columns * (cell * 3 // 4 + 32)
        sheet.paste(raster, (x, y + 4))
        draw.text((index % columns * cell + 10, y + cell * 3 // 4 + 4), label[:60], fill='#dcc59c')
    output = io.BytesIO()
    sheet.save(output, 'PNG')
    return output.getvalue()
