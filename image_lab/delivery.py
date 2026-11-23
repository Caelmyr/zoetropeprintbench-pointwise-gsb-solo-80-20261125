import io
import zipfile
import hashlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from . import validation as v
from .arrays import srgb_decode, srgb_encode
from .codecs import png_bytes, jpeg_bytes
from .exporting import json_bytes


def resize_linear(image, width, height):
    channels = []
    linear = srgb_decode(image)
    for index in range(3):
        raster = Image.fromarray(linear[:, :, index].astype(np.float32))
        scaled = raster.resize((width, height), Image.Resampling.LANCZOS)
        channels.append(np.asarray(scaled, np.float32))
    return np.clip(srgb_encode(np.maximum(np.stack(channels, axis=2), 0)), 0, 1)


def sizing(image, width, height, fit='contain', anchor=(.5, .5)):
    h, w = image.shape[:2]
    if fit == 'stretch':
        return resize_linear(image, width, height), {'source_crop': [0, 0, w, h]}
    factor = min(width / w, height / h) if fit == 'contain' else max(width / w, height / h)
    target_w, target_h = max(1, round(w * factor)), max(1, round(h * factor))
    scaled = resize_linear(image, target_w, target_h)
    if fit == 'cover':
        left = min(target_w - width, max(0, round((target_w - width) * anchor[0])))
        top = min(target_h - height, max(0, round((target_h - height) * anchor[1])))
        return scaled[top:top + height, left:left + width], {'scaled_crop': [left, top, left + width, top + height]}
    output = np.ones((height, width, 3), np.float32) * .06
    x = round((width - target_w) * anchor[0])
    y = round((height - target_h) * anchor[1])
    output[y:y + target_h, x:x + target_w] = scaled
    return output, {'padding': [x, y, width - x - target_w, height - y - target_h]}


def caption_image(image, text, opacity=.7, position='bottom-right'):
    text = v.text(text, '署名文字', 80)
    if not text:
        return image
    raster = Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)).convert('RGBA')
    layer = Image.new('RGBA', raster.size)
    draw = ImageDraw.Draw(layer)
    size = max(10, min(44, round(min(raster.size) * .035)))
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        font = ImageFont.load_default()
    bounds = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    margin = size
    x = margin if position == 'bottom-left' else max(0, raster.width - text_w - margin)
    y = max(0, raster.height - text_h - margin)
    draw.rounded_rectangle((x - 8, y - 5, x + text_w + 8, y + text_h + 8), radius=4, fill=(0, 0, 0, round(110 * opacity)))
    draw.text((x, y), text, fill=(255, 255, 255, round(255 * opacity)), font=font)
    result = Image.alpha_composite(raster, layer).convert('RGB')
    return np.asarray(result, np.float32) / 255


def delivery_plan(body):
    variants = v.list_value(body.get('variants', [{'name': 'web', 'width': 1280, 'height': 960, 'fit': 'contain'}]), '交付尺寸', 8)
    if not variants:
        raise v.Invalid('请添加至少一个交付规格')
    result = []
    names = set()
    total_pixels = 0
    for index, item in enumerate(variants):
        item = v.object_value(item, '交付规格')
        name = v.text(item.get('name', 'size-' + str(index + 1)), '规格名称', 60, False)
        if not name.replace('-', '').replace('_', '').isalnum() or not name.isascii():
            raise v.Invalid('规格名称只允许ASCII字母、数字、横线、下划线')
        if name in names:
            raise v.Invalid('规格名称不能重复')
        names.add(name)
        width = v.integer(item.get('width'), '交付宽度', 64, 4096)
        height = v.integer(item.get('height'), '交付高度', 64, 4096)
        total_pixels += width * height
        if total_pixels > 24000000:
            raise v.Invalid('多规格交付总像素超过2400万')
        fit = v.choice(item.get('fit', 'contain'), '画面适配', ['contain', 'cover', 'stretch'])
        anchor = v.vector(item.get('anchor', [.5, .5]), '裁切锚点', 2, 0, 1)
        result.append({'name': name, 'width': width, 'height': height, 'fit': fit, 'anchor': anchor})
    return result


def delivery_export(image, body):
    plan = delivery_plan(body)
    caption = v.text(body.get('caption', ''), '署名', 80)
    output = io.BytesIO()
    entries = []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for variant in plan:
            raster, sizing_report = sizing(image, variant['width'], variant['height'], variant['fit'], variant['anchor'])
            if caption:
                raster = caption_image(raster, caption)
            encoded = jpeg_bytes(raster, body.get('quality', 92))
            name = variant['name'] + '.jpg'
            archive.writestr(name, encoded)
            entries.append({**variant, 'file': name, 'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded), 'sizing': sizing_report})
        manifest = {'format': 'stacklight-delivery-v1', 'resize_space': 'linear sRGB', 'caption': caption, 'variants': entries}
        archive.writestr('delivery.json', json_bytes(manifest))
    return output.getvalue()
