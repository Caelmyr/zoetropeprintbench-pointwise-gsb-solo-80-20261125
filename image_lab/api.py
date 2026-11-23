import io
from flask import Blueprint, request, jsonify, Response, send_file
from . import validation as v
from . import model
from .demos import DEMOS, create_demo
from .archives import import_project
from .exporting import export_image, contact_sheet
from .codecs import png_bytes, visualization
from .metadata import exposure_table
from .preparation import prepare
from .quality import rank_frames, stack_preflight, clipping_map, zebra_map, focus_peaking
from .statistics import image_report, grid_statistics
from .comparison import compare, difference_image, split_view, checker_compare, registration_overlay, blink_animation, line_profile
from .charts import histogram_chart, waveform, vectorscope
from .tone import tone_map, tone_curve
from .hdr import response_curve
from .finishing import finish_image, white_balance_estimate
from .calibration import calibration_report
from .lens import grid_preview
from .pyramids import pyramid_energy
from .batch import batch_plan
from .workflows import public_workflows
from .arrays import resize, composite_valid, normalize


def body():
    data = request.get_json(silent=True)
    if data is None:
        raise v.Invalid('请提交JSON对象')
    return v.object_value(data)


def image_response(image):
    return Response(png_bytes(image), mimetype='image/png', headers={'Cache-Control': 'no-store'})


def byte_image(raw):
    return Response(raw, mimetype='image/png', headers={'Cache-Control': 'no-store'})


def download(data, filename, mimetype='application/zip'):
    return send_file(io.BytesIO(data), as_attachment=True, download_name=filename, mimetype=mimetype)


def upload():
    file = request.files.get('file')
    if file is None:
        raise v.Invalid('请选择上传文件')
    return file


def create_api(services):
    api = Blueprint('api', __name__, url_prefix='/api')
    store = services['store']
    assets = services['assets']
    frames = services['frames']
    masks = services['masks']
    results = services['results']
    jobs = services['jobs']
    profiles = services['profiles']
    regions = services['regions']

    def result_fields(key, result_id=None):
        return results.fields(key, result_id, not bool(result_id))

    def frame_image(key, frame_id, aligned=False):
        project = store.get(key)
        frame = model.find_frame(project, frame_id)
        if not aligned:
            image = assets.field(frame['asset'])
            from .arrays import fit_size
            w, h = fit_size(image.shape, min(project['settings']['size'], 1024))
            return resize(image, w, h)
        data = prepare(project, assets, True)
        index = next((i for i, f in enumerate(data['frames']) if f['id'] == frame_id), None)
        if index is None:
            raise v.Invalid('对齐预览需要先启用照片')
        return data['images'][index]

    @api.get('/health')
    def health():
        import shutil
        free = shutil.disk_usage(store.root).free
        return jsonify({'application': 'GasketDesk Image Preparation', 'version': '1.0', 'status': 'ok' if free > 1048576 else 'degraded', 'free_bytes': free, 'projects': len(store.list()), 'worker': 'single CPU worker'})

    @api.get('/capabilities')
    def capabilities():
        return jsonify({'modes': [item['key'] for item in public_workflows()], 'workflows': public_workflows(), 'alignment': ['phase', 'orb', 'ecc', 'mtb'], 'limits': {'frames': 24, 'long_edge': 1536, 'frame_pixels': 16000000, 'active_jobs': 5}, 'settings': model.project('defaults')['settings']})

    @api.get('/demos')
    def demos():
        return jsonify(DEMOS)

    @api.post('/demos/<kind>')
    def demo(kind):
        return jsonify(create_demo(store, assets, kind)), 201

    @api.get('/projects')
    def project_list():
        return jsonify(store.list(request.args.get('query', ''), request.args.get('archived') == 'true'))

    @api.post('/projects')
    def create_project():
        data = body()
        return jsonify(store.create(data.get('name', '新的摄影工程'))), 201

    @api.get('/projects/<key>')
    def project(key):
        return jsonify(store.get(key))

    @api.patch('/projects/<key>')
    def update_project(key):
        data = body()
        return jsonify(store.update(key, v.revision(data), data))

    @api.post('/projects/<key>/duplicate')
    def duplicate_project(key):
        return jsonify(store.duplicate(key)), 201

    @api.get('/projects/<key>/history')
    def history(key):
        return jsonify(store.history(key))

    @api.post('/projects/<key>/undo')
    def undo(key):
        data = body()
        return jsonify(store.undo(key, v.revision(data)))

    @api.post('/projects/<key>/redo')
    def redo(key):
        data = body()
        return jsonify(store.undo(key, v.revision(data), True))

    @api.get('/projects/<key>/snapshots')
    def snapshots(key):
        return jsonify(store.snapshots(key))

    @api.post('/projects/<key>/snapshots')
    def create_snapshot(key):
        return jsonify(store.snapshot_create(key, body())), 201

    @api.post('/projects/<key>/snapshots/<snapshot_id>/restore')
    def restore_snapshot(key, snapshot_id):
        data = body()
        return jsonify(store.snapshot_restore(key, v.revision(data), snapshot_id))

    @api.get('/assets')
    def asset_list():
        return jsonify(assets.list(request.args.get('kind')))

    @api.post('/assets')
    def import_asset():
        file = upload()
        return jsonify(assets.import_image(file.read(), file.filename)), 201

    @api.get('/assets/<asset_id>/thumbnail')
    def asset_thumbnail(asset_id):
        assets.metadata(asset_id)
        return send_file(assets.path(asset_id, '.jpg'), max_age=0)

    @api.get('/assets/<asset_id>/image')
    def asset_image(asset_id):
        return byte_image(assets.full_png(asset_id))

    @api.get('/assets/<asset_id>/verify')
    def asset_verify(asset_id):
        return jsonify(assets.verify(asset_id))

    @api.post('/projects/<key>/frames')
    def add_frames(key):
        data = body()
        return jsonify(frames.add(key, v.revision(data), data.get('asset_ids'))), 201

    @api.post('/projects/<key>/upload')
    def upload_frames(key):
        current = store.get(key)
        revision = v.integer(request.form.get('revision'), '修订号', 0)
        files = request.files.getlist('files')
        if not files or len(files) + len(current['frames']) > 24:
            raise v.Invalid('请选择图片，总数不得超过24帧')
        if revision != current['revision']:
            raise v.Conflict('工程已更新')
        ids = []
        for file in files:
            asset = assets.import_image(file.read(), file.filename)
            ids.append(asset['id'])
        return jsonify(frames.add(key, revision, ids)), 201

    @api.patch('/projects/<key>/frames/<frame_id>')
    def update_frame(key, frame_id):
        data = body()
        return jsonify(frames.update(key, v.revision(data), frame_id, data))

    @api.post('/projects/<key>/frames/remove')
    def remove_frames(key):
        data = body()
        return jsonify(frames.remove(key, v.revision(data), data.get('frame_ids')))

    @api.post('/projects/<key>/frames/reorder')
    def reorder_frames(key):
        data = body()
        return jsonify(frames.reorder(key, v.revision(data), data.get('frame_ids')))

    @api.post('/projects/<key>/frames/<frame_id>/transform')
    def manual_transform(key, frame_id):
        data = body()
        return jsonify(frames.manual_transform(key, v.revision(data), frame_id, data))

    @api.post('/projects/<key>/reset-transforms')
    def reset_transforms(key):
        data = body()
        return jsonify(frames.reset_transforms(key, v.revision(data)))

    @api.post('/projects/<key>/estimate-exposure')
    def estimate_exposure(key):
        data = body()
        return jsonify(frames.auto_exposure(key, v.revision(data), data.get('mode', 'rank')))

    @api.get('/projects/<key>/exposures')
    def exposures(key):
        return jsonify(exposure_table(store.get(key), assets.metadata))

    @api.get('/projects/<key>/frames/<frame_id>/image')
    def frame_preview(key, frame_id):
        image = frame_image(key, frame_id, request.args.get('aligned') == 'true')
        return image_response(image)

    @api.get('/projects/<key>/frames/<frame_id>/analysis')
    def frame_analysis(key, frame_id):
        return jsonify(image_report(frame_image(key, frame_id)))

    @api.get('/projects/<key>/frames/<frame_id>/mask')
    def mask_image(key, frame_id):
        return image_response(masks.field(store.get(key), frame_id))

    @api.post('/projects/<key>/frames/<frame_id>/mask/<operation>')
    def mask_operation(key, frame_id, operation):
        data = body()
        revision = v.revision(data)
        if operation == 'stroke':
            output = masks.stroke(key, revision, frame_id, data)
        elif operation == 'fill':
            output = masks.fill(key, revision, frame_id, data)
        elif operation == 'process':
            output = masks.process(key, revision, frame_id, data)
        elif operation == 'automatic':
            output = masks.automatic(key, revision, frame_id, data)
        else:
            raise v.Invalid('遮罩操作不存在')
        return jsonify(output)

    @api.get('/projects/<key>/preflight')
    def preflight(key):
        return jsonify(stack_preflight(store.get(key), assets))

    @api.get('/projects/<key>/ranking')
    def ranking(key):
        return jsonify(rank_frames(store.get(key), assets))

    @api.post('/projects/<key>/jobs')
    def start_job(key):
        return jsonify(jobs.start(key, body())), 202

    @api.get('/projects/<key>/jobs')
    def project_jobs(key):
        store.get(key)
        return jsonify(jobs.list(key))

    @api.get('/jobs/<job_id>')
    def get_job(job_id):
        return jsonify(jobs.get(job_id))

    @api.post('/jobs/<job_id>/apply-alignment')
    def apply_alignment(job_id):
        return jsonify(jobs.apply_alignment(job_id, v.revision(body())))

    @api.post('/jobs/<job_id>/cancel')
    def cancel_job(job_id):
        return jsonify(jobs.cancel(job_id))

    @api.post('/jobs/<job_id>/retry')
    def retry_job(job_id):
        return jsonify(jobs.retry(job_id)), 202

    @api.get('/jobs/<job_id>/download')
    def job_download(job_id):
        path, job = jobs.download(job_id)
        return send_file(path, as_attachment=True, download_name=job['filename'], mimetype=job['mimetype'])

    @api.get('/projects/<key>/results')
    def result_list(key):
        return jsonify(results.list(key))

    @api.get('/projects/<key>/current')
    def current_result(key):
        project = store.get(key)
        return jsonify(results.metadata(key) if project['current_result'] else None)

    @api.get('/projects/<key>/results/<result_id>')
    def result_metadata(key, result_id):
        return jsonify(results.metadata(key, result_id))

    @api.post('/projects/<key>/results/<result_id>/select')
    def select_result(key, result_id):
        return jsonify(results.select(key, result_id))

    @api.get('/projects/<key>/result-image/<name>')
    def result_image(key, name):
        output = results.image_path(key, name, request.args.get('result_id'))
        if isinstance(output, bytes):
            return byte_image(output)
        return send_file(output, max_age=0)

    @api.get('/projects/<key>/integrity')
    def integrity(key):
        return jsonify(results.integrity(key, request.args.get('result_id')))

    @api.get('/projects/<key>/analysis')
    def result_analysis(key):
        fields, _ = result_fields(key, request.args.get('result_id'))
        return jsonify(image_report(fields['image'], fields['valid']))

    @api.post('/projects/<key>/export-image')
    def image_export(key):
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        raw, filename, mimetype = export_image(fields, data.get('format', 'png'), data)
        return download(raw, filename, mimetype)

    @api.post('/import-project')
    def project_import():
        return jsonify(import_project(store, assets, upload().read())), 201

    @api.get('/profiles')
    def profile_list():
        return jsonify(profiles.list())

    @api.post('/projects/<key>/profiles')
    def save_profile(key):
        return jsonify(profiles.save(key, body())), 201

    @api.post('/projects/<key>/profiles/<profile_id>')
    def apply_profile(key, profile_id):
        return jsonify(profiles.apply(key, v.revision(body()), profile_id))

    @api.get('/profiles/<profile_id>/download')
    def export_profile(profile_id):
        return download(profiles.export(profile_id), 'stacklight-profile.json', 'application/json')

    @api.post('/profiles/import')
    def import_profile():
        return jsonify(profiles.import_profile(body())), 201

    @api.post('/projects/<key>/batch-plan')
    def plan_batch(key):
        plan = batch_plan(store.get(key), body())
        return jsonify([{'index': index, 'label': item['label']} for index, item in enumerate(plan)])

    @api.post('/projects/<key>/tone-preview')
    def tone_preview(key):
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        project = store.get(key)
        from .settings import merge_settings
        settings = merge_settings(project['settings'], data.get('settings', {}))
        image = tone_map(fields['linear'], settings['tone'])
        return image_response(finish_image(image, settings['finishing']))

    @api.get('/projects/<key>/tone-curve')
    def tone_plot(key):
        return jsonify(tone_curve(store.get(key)['settings']['tone']))

    @api.get('/projects/<key>/response-curve')
    def response_plot(key):
        return jsonify(response_curve(store.get(key)['settings']['hdr']))

    @api.post('/projects/<key>/compare-frames')
    def frame_comparison(key):
        data = body()
        first = frame_image(key, data.get('first'), data.get('aligned', True))
        second = frame_image(key, data.get('second'), data.get('aligned', True))
        kind = v.choice(data.get('kind', 'overlay'), '对比图', ['overlay', 'difference', 'split', 'checker', 'blink', 'metrics'])
        if first.shape != second.shape:
            raise v.Invalid('比较帧必须具有相同画布尺寸')
        if kind == 'metrics':
            return jsonify(compare(first, second))
        if kind == 'difference':
            return image_response(difference_image(first, second))
        if kind == 'split':
            return image_response(split_view(first, second, v.number(data.get('fraction', .5), '分屏位置', 0, 1)))
        if kind == 'checker':
            return image_response(checker_compare(first, second))
        if kind == 'blink':
            return byte_image(blink_animation(first, second))
        return image_response(registration_overlay(first, second))

    @api.post('/projects/<key>/compare-results')
    def result_comparison(key):
        data = body()
        a, _ = results.fields(key, data.get('first'), False)
        b, _ = results.fields(key, data.get('second'), False)
        if data.get('kind') == 'difference':
            if a['image'].shape != b['image'].shape:
                raise v.Invalid('历史结果尺寸不同')
            return image_response(difference_image(a['image'], b['image']))
        return jsonify(compare(a['image'], b['image']))

    @api.get('/projects/<key>/contact')
    def frame_contact(key):
        project = store.get(key)
        images = [frame_image(key, f['id']) for f in project['frames']]
        labels = [str(i + 1) + ' / ' + f['name'] for i, f in enumerate(project['frames'])]
        return byte_image(contact_sheet(images, labels))

    @api.get('/projects/<key>/chart/<kind>')
    def chart(key, kind):
        fields, _ = result_fields(key, request.args.get('result_id'))
        image = fields['image']
        if kind == 'histogram':
            return byte_image(histogram_chart(image))
        if kind == 'waveform':
            return byte_image(waveform(image))
        if kind == 'vectorscope':
            return byte_image(vectorscope(image))
        raise v.Invalid('示波图类型不存在')

    @api.post('/projects/<key>/diagnostic-image')
    def diagnostic_image(key):
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        kind = v.choice(data.get('kind', 'clipping'), '诊断图', ['clipping', 'zebra', 'peaking', 'valid', 'before_after'])
        if kind == 'clipping':
            image = clipping_map(fields['image'])
        elif kind == 'zebra':
            image = zebra_map(fields['image'])
        elif kind == 'peaking':
            image = focus_peaking(fields['image'])
        elif kind == 'valid':
            image = composite_valid(fields['image'], fields['valid'])
        else:
            image = split_view(fields['before_finishing'], fields['image'], v.number(data.get('fraction', .5), '分屏位置', 0, 1))
        return image_response(image)

    @api.post('/projects/<key>/profile-line')
    def profile_line(key):
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        h, w = fields['image'].shape[:2]
        start = v.vector(data.get('start', [0, h // 2]), '起点', 2, 0, max(w, h) - 1)
        end = v.vector(data.get('end', [w - 1, h // 2]), '终点', 2, 0, max(w, h) - 1)
        if any(p[0] >= w or p[1] >= h for p in [start, end]):
            raise v.Invalid('剖面坐标越界')
        count = v.integer(data.get('samples', 256), '采样点数', 2, 2048)
        return jsonify(line_profile(fields['image'], start, end, count))

    @api.post('/projects/<key>/white-balance-estimate')
    def wb_estimate(key):
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        method = v.choice(data.get('method', 'grayworld'), '白平衡估计', ['grayworld', 'whitepatch'])
        return jsonify(white_balance_estimate(fields['image'], method))

    @api.get('/projects/<key>/grid-analysis')
    def grid_analysis(key):
        fields, _ = result_fields(key, request.args.get('result_id'))
        return jsonify(grid_statistics(fields['image']))

    @api.get('/projects/<key>/pyramid-analysis')
    def pyramid_analysis(key):
        fields, _ = result_fields(key, request.args.get('result_id'))
        return jsonify(pyramid_energy(fields['image']))

    @api.get('/assets/<asset_id>/calibration-report')
    def calibration_analysis(asset_id):
        kind = v.choice(request.args.get('kind', 'dark'), '校正类型', ['dark', 'flat'])
        return jsonify(calibration_report(assets.field(asset_id), kind))

    @api.get('/projects/<key>/lens-grid')
    def lens_grid(key):
        return image_response(grid_preview((400, 600), store.get(key)['settings']['lens']))

    @api.get('/storage')
    def storage():
        return jsonify(store.storage())

    @api.get('/projects/<key>/regions')
    def region_list(key):
        return jsonify(store.get(key).get('regions', []))

    @api.post('/projects/<key>/regions')
    def region_create(key):
        data = body()
        return jsonify(regions.add(key, v.revision(data), data)), 201

    @api.patch('/projects/<key>/regions/<region_id>')
    def region_update(key, region_id):
        data = body()
        return jsonify(regions.update(key, v.revision(data), region_id, data))

    @api.post('/projects/<key>/regions/<region_id>/remove')
    def region_remove(key, region_id):
        return jsonify(regions.remove(key, v.revision(body()), region_id))

    @api.get('/projects/<key>/regions/<region_id>/analysis')
    def region_analysis(key, region_id):
        return jsonify(regions.analyze(key, region_id, request.args.get('result_id')))

    @api.get('/projects/<key>/regions/<region_id>/frames')
    def region_frames(key, region_id):
        return jsonify(regions.across_frames(key, region_id))

    @api.get('/projects/<key>/region-overlay')
    def region_overlay(key):
        fields, _ = result_fields(key, request.args.get('result_id'))
        return image_response(regions.annotate(key, fields['image']))

    @api.get('/projects/<key>/spectrum')
    def spectrum(key):
        from .spectrum import power_spectrum
        fields, _ = result_fields(key, request.args.get('result_id'))
        report, image = power_spectrum(fields['image'])
        if request.args.get('image') == 'true':
            return image_response(image)
        return jsonify(report)

    @api.get('/projects/<key>/autocorrelation')
    def autocorrelation(key):
        from .spectrum import autocorrelation as calculate
        fields, _ = result_fields(key, request.args.get('result_id'))
        report, image = calculate(fields['image'])
        if request.args.get('image') == 'true':
            return image_response(image)
        return jsonify(report)

    @api.get('/projects/<key>/detail-analysis')
    def detail_analysis(key):
        from .spectrum import multiscale_detail
        fields, _ = result_fields(key, request.args.get('result_id'))
        return jsonify(multiscale_detail(fields['image']))

    @api.post('/projects/<key>/edge-profile')
    def edge_profile(key):
        from .spectrum import edge_spread
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        h, w = fields['image'].shape[:2]
        bounds = v.vector(data.get('bounds', [0, 0, w, h]), '边缘区域', 4, 0, max(w, h))
        bounds = [int(x) for x in bounds]
        if bounds[2] > w or bounds[3] > h or bounds[2] - bounds[0] < 4 or bounds[3] - bounds[1] < 4:
            raise v.Invalid('边缘区域越界或过小')
        orientation = v.choice(data.get('orientation', 'vertical'), '边缘方向', ['vertical', 'horizontal'])
        return jsonify(edge_spread(fields['image'], bounds, orientation))

    @api.get('/projects/<key>/export-preflight')
    def encoding_preflight(key):
        from .precision import export_preflight
        fields, meta = result_fields(key, request.args.get('result_id'))
        return jsonify(export_preflight(fields, meta))

    @api.post('/projects/<key>/tiles')
    def photo_tiles(key):
        from .tiles import tile_export
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        raw, _ = tile_export(fields['image'], data)
        return download(raw, 'photograph-viewer-tiles.zip')

    @api.get('/projects/<key>/temporal-analysis')
    def temporal_analysis(key):
        from .temporal import temporal_report
        prepared = prepare(store.get(key), assets)
        report, fields = temporal_report(prepared)
        name = request.args.get('image')
        if name:
            if name not in fields:
                raise v.Invalid('时域辅助图不存在')
            return image_response(fields[name])
        return jsonify(report)

    @api.post('/projects/<key>/pixel-trace')
    def trace_pixel(key):
        from .temporal import pixel_trace
        data = body()
        point = v.vector(data.get('point', [.5, .5]), '归一化采样位置', 2, 0, 1)
        radius = v.integer(data.get('radius', 2), '采样半径', 0, 32)
        return jsonify(pixel_trace(prepare(store.get(key), assets), point, radius))

    @api.post('/projects/<key>/calibrate-response')
    def calibrate_response(key):
        from .response import estimate_response
        data = body()
        samples = v.integer(data.get('samples', 96), '响应采样数', 24, 256)
        smoothness = v.number(data.get('smoothness', 30), '响应平滑强度', 1, 100)
        return jsonify(estimate_response(prepare(store.get(key), assets), samples, smoothness))

    @api.post('/assets/group-plan')
    def group_plan():
        from .series import group_assets
        return jsonify(group_assets(assets, body()))

    @api.post('/assets/group-projects')
    def group_projects():
        from .series import instantiate_groups
        return jsonify(instantiate_groups(store, frames, assets, body())), 201

    @api.post('/projects/<key>/calibrate-color')
    def calibrate_color(key):
        from .colorcal import calibrate_patches, apply_color, cube_lut
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        calibration = calibrate_patches(fields['image'], data)
        if data.get('output') == 'preview':
            return image_response(apply_color(fields['image'], calibration))
        if data.get('output') == 'lut':
            return download(cube_lut(calibration), 'color-calibration.cube', 'text/plain')
        return jsonify(calibration)

    @api.post('/projects/<key>/calibrate-camera')
    def camera_calibration(key):
        from .camera import calibrate_camera
        data = body()
        project = store.get(key)
        images = [assets.field(f['asset']) for f in model.enabled_frames(project)]
        return jsonify(calibrate_camera(images, data))

    @api.get('/projects/<key>/frames/<frame_id>/checkerboard')
    def checkerboard_preview(key, frame_id):
        from .camera import detect_board, board_overlay
        image = frame_image(key, frame_id)
        result = detect_board(image)
        if request.args.get('image') == 'true':
            return image_response(board_overlay(image, result))
        return jsonify(result)

    @api.get('/projects/<key>/report')
    def report(key):
        from .reports import html_report
        fields, meta = result_fields(key, request.args.get('result_id'))
        return Response(html_report(store.get(key), fields, meta), mimetype='text/html')

    @api.get('/projects/<key>/proofbook.pdf')
    def proofbook(key):
        from .reports import pdf_proofbook
        fields, meta = result_fields(key, request.args.get('result_id'))
        return download(pdf_proofbook(meta['project'], fields, meta, assets), 'photographic-proof.pdf', 'application/pdf')

    @api.post('/projects/<key>/local-preview')
    def local_preview(key):
        from .local_adjustments import adjustment_spec, apply_adjustment, preview_overlay
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        config = adjustment_spec(data.get('adjustment', {}))
        if data.get('overlay', False):
            image = preview_overlay(fields['before_finishing'], config)
        else:
            image, _ = apply_adjustment(fields['image'], config)
        return image_response(image)

    @api.post('/projects/<key>/delivery')
    def delivery(key):
        from .delivery import delivery_export
        data = body()
        fields, _ = result_fields(key, data.get('result_id'))
        return download(delivery_export(fields['image'], data), 'photograph-delivery.zip')

    @api.get('/projects/<key>/audit')
    def audit(key):
        from .audit import audit_project
        return jsonify(audit_project(store, assets, results, key))

    @api.get('/projects/<key>/provenance')
    def provenance(key):
        from .audit import source_provenance
        return jsonify(source_provenance(store.get(key), assets))

    @api.post('/projects/<key>/duplicate-plan')
    def duplicates(key):
        from .curation import duplicate_plan
        data = body()
        threshold = v.integer(data.get('threshold', 5), '相似阈值', 0, 16)
        return jsonify(duplicate_plan(store.get(key), assets, threshold))

    @api.post('/projects/<key>/disable-duplicates')
    def disable_duplicates(key):
        from .curation import disable_exact_duplicates
        return jsonify(disable_exact_duplicates(store, assets, key, v.revision(body())))

    @api.post('/projects/<key>/sort-frames')
    def sort_frames(key):
        from .curation import sort_frames
        data = body()
        return jsonify(sort_frames(store, assets, key, v.revision(data), data.get('method', 'name')))

    @api.post('/projects/<key>/subset')
    def subset(key):
        from .curation import subset_project
        return jsonify(subset_project(store, key, body())), 201

    @api.get('/projects/<key>/color-management')
    def color_management(key):
        from .color_management import output_color_report
        fields, meta = result_fields(key, request.args.get('result_id'))
        return jsonify(output_color_report(meta, fields))

    @api.post('/projects/<key>/photometric-plan')
    def photometric_plan(key):
        from .photometric import match_plan
        data = body()
        return jsonify(match_plan(store.get(key), assets, v.boolean(data.get('color', False), '匹配色偏')))

    @api.post('/projects/<key>/photometric-match')
    def photometric_match(key):
        from .photometric import apply_match
        data = body()
        return jsonify(apply_match(store, assets, key, v.revision(data), data))

    @api.get('/projects/<key>/exposures.csv')
    def csv_export(key):
        from .exposure_csv import export_csv
        return download(export_csv(store.get(key)), 'exposures.csv', 'text/csv')

    @api.post('/projects/<key>/exposures.csv')
    def csv_import(key):
        from .exposure_csv import import_csv
        data = body()
        return jsonify(import_csv(store, key, v.revision(data), data.get('csv')))

    @api.post('/projects/<key>/exposure-selection')
    def exposure_selection(key):
        from .selection import select_bracket, apply_selection
        data = body()
        if data.get('apply') is True:
            return jsonify(apply_selection(store, assets, key, v.revision(data), data))
        return jsonify(select_bracket(store.get(key), assets, data))

    @api.post('/projects/<key>/exposure-coverage')
    def exposure_coverage(key):
        from .selection import exposure_coverage_map
        data = body()
        fields = exposure_coverage_map(store.get(key), assets, data)
        selected = v.choice(data.get('field', 'coverage'), '覆盖图', fields)
        return image_response(visualization(fields[selected], 'heat'))

    @api.get('/projects/<key>/verify-encoded')
    def verify_encoded(key):
        return jsonify(results.verify_encoded(key, request.args.get('result_id')))

    @api.get('/workflows')
    def workflow_catalog():
        return jsonify(public_workflows())

    return api
