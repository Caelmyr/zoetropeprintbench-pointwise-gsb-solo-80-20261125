import base64
import html
import io
import json
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from .codecs import png_bytes
from .arrays import resize, fit_size
from .precision import export_preflight
from .charts import histogram_chart
from .exporting import contact_sheet


def data_uri(raw):
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def html_report(project, fields, meta):
    title = html.escape(project['name'])
    w, h = fit_size(fields['image'].shape, 1000)
    preview = png_bytes(resize(fields['image'], w, h))
    precision = export_preflight(fields, meta)
    rows = []
    for key, value in meta['report'].items():
        if isinstance(value, (int, float, str)) or value is None:
            rows.append('<tr><td>' + html.escape(key) + '</td><td>' + html.escape(str(value)) + '</td></tr>')
    warning = '；'.join(precision['warnings']) or '无编码预检提示'
    payload = html.escape(json.dumps({'processing': meta['project']['settings'], 'precision': precision}, ensure_ascii=False, indent=2))
    segments = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>' + title + '</title>',
        '<style>body{max-width:1050px;margin:40px auto;font:14px/1.6 sans-serif;color:#23343e}img{max-width:100%}table{border-collapse:collapse;width:100%}td{border-bottom:1px solid #ddd;padding:8px}pre{white-space:pre-wrap;word-break:break-all;font-size:11px}@media print{button{display:none}}</style>',
        '<button onclick="window.print()">打印 / 另存PDF</button><h1>' + title + '</h1>',
        '<p>叠光 ScanWeave · 多帧摄影合成报告 · 修订 ' + str(meta['source_revision']) + '</p>',
        '<img alt="合成输出" src="' + data_uri(preview) + '">',
        '<h2>处理概况</h2><table>' + ''.join(rows) + '</table>',
        '<h2>显示直方图</h2><img alt="直方图" src="' + data_uri(histogram_chart(fields['image'])) + '">',
        '<h2>导出提示</h2><p>' + html.escape(warning) + '</p>',
        '<p>预览显示图为sRGB。HDR是相对线性辐射数据，焦点索引不是物理深度。统计指标用于复核，不代替真值标定。</p>',
        '<h2>完整设置与编码预检</h2><pre>' + payload + '</pre></html>',
    ]
    return ''.join(segments)


def pdf_proofbook(project, fields, meta, assets):
    page_width, page_height = 1240, 1754
    try:
        heading = ImageFont.truetype('DejaVuSans.ttf', 32)
        normal = ImageFont.truetype('DejaVuSans.ttf', 18)
    except OSError:
        heading = ImageFont.load_default()
        normal = heading
    pages = []
    def page(title):
        canvas = Image.new('RGB', (page_width, page_height), 'white')
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((55, 50, 63, 105), fill='#a88450')
        draw.text((85, 55), title, fill='#203742', font=heading)
        draw.text((65, page_height - 65), 'ScanWeave / Photographic proof / Relative image measurements', fill='#80909a', font=normal)
        return canvas, draw
    cover, draw = page('COMPOSITE / ' + meta['mode'].upper())
    image = Image.fromarray(np.rint(fields['image'] * 255).astype(np.uint8))
    image.thumbnail((page_width - 130, 1000), Image.Resampling.LANCZOS)
    cover.paste(image, ((page_width - image.width) // 2, 150))
    y = 150 + image.height + 45
    rows = [('Project ID', project['id']), ('Source revision', str(meta['source_revision'])), ('Output size', f'{meta["width"]} x {meta["height"]}'), ('Enabled frames', str(len(meta['frame_ids']))), ('Created UTC', meta['created_at'])]
    for label, value in rows:
        draw.text((65, y), label + ': ' + value, fill='#344954', font=normal)
        y += 33
    pages.append(cover)
    included = project['frames'][:24]
    for offset in range(0, len(included), 6):
        canvas, draw = page('SOURCE FRAMES / ' + str(offset // 6 + 1))
        for index, frame in enumerate(included[offset:offset + 6]):
            field = assets.field(frame['asset'])
            raster = Image.fromarray(np.rint(np.clip(field, 0, 1) * 255).astype(np.uint8))
            raster.thumbnail((520, 370), Image.Resampling.LANCZOS)
            x = 65 + index % 2 * 565
            y = 160 + index // 2 * 470
            canvas.paste(raster, (x, y))
            draw.text((x, y + 385), f'Frame {offset + index + 1:02d} / EV {frame["ev"]:+.2f}', fill='#344954', font=normal)
            draw.text((x, y + 415), f'Exposure {frame["exposure"]:.6g} / Weight {frame["weight"]:.2f}', fill='#70818c', font=normal)
        pages.append(canvas)
    chart_page, draw = page('DIAGNOSTICS / DISPLAY HISTOGRAM')
    chart = Image.open(io.BytesIO(histogram_chart(fields['image'], 1100, 420))).convert('RGB')
    chart_page.paste(chart, (70, 180))
    draw.text((70, 650), 'Luminance metrics refer to the display image.', fill='#344954', font=normal)
    luminance = meta['statistics']['luminance']
    for index, key in enumerate(['mean', 'std', 'clip_black', 'clip_white', 'entropy_bits']):
        draw.text((70, 710 + index * 40), key + ': ' + str(round(luminance[key], 6)), fill='#344954', font=normal)
    pages.append(chart_page)
    stream = io.BytesIO()
    pages[0].save(stream, 'PDF', save_all=True, append_images=pages[1:], resolution=150., title='ScanWeave photographic proof')
    return stream.getvalue()
