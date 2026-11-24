"""Local browser workflow for drum artwork, slot measurements and immutable proofs."""

from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import alignment, demo, layout, model, proof, raster
from .store import Library, stamp

MAX_DRUM_FRAME_BYTES = 100 * 1024 * 1024
MODE_NOTES = {
    "ribbon": "The artwork band includes the blank gluing tab and printer resolution.",
    "cut-guide": "The separate cut sheet identifies frame centers, boundaries and the gluing flap.",
    "slot-ruler": "Print the nominal opposite-frame viewing slots at actual size.",
    "direct-loop": "Step through the source artwork at its print framing.",
    "optical-loop": "Approximate one slit exposure at the current rotation and exposure time.",
    "neighbors": "Red shows the previous silhouette and teal shows the next silhouette.",
    "coverage": "Print clipping map: red is artwork cut by the cell, blue is an edge the original photo does not cover after moving.",
    "motion": "Trace the center of visible ink across the full loop.",
    "contrast": "Blue marks very dark values and ochre marks very light values on the selected paper.",
    "scan": "Read light openings inside the scan rectangle. Orange boxes touch the captured edge.",
    "scale": "Measure the printed 100 mm line with a ruler before cutting.",
    "assembly": "Bring the active ends together as they will look when the tab sits underneath.",
}


def payload():
    return model.document(request.get_json(silent=True))


def upload():
    asset = request.files.get("file")
    if asset is None:
        raise Invalid("Choose an image file.")
    data = asset.stream.read(raster.MAX_BYTES + 1)
    return raster.decode(data), data, model.filename(asset.filename)


def form_revision():
    return model.integer(request.form.get("revision"), "the current drum revision", 1, 1_000_000_000)


def check_revision(draft, revision):
    if type(revision) is not int or revision != draft["revision"]:
        raise Conflict("This drum changed. Reload it before saving.")


def resolve_reference_index(draft, value=None):
    frames = draft["frames"]
    if not frames:
        raise Invalid("Import frames before aligning fixed scenery.")
    if value is None:
        saved = (draft.get("alignment") or {}).get("reference_frame")
        value = saved if saved is not None else draft["settings"].get("proof_frame", 0)
    if isinstance(value, dict):
        value = value.get("id")
    if isinstance(value, str):
        try:
            model.identifier(value)
        except Invalid:
            return model.integer(value, "reference frame", 0, len(frames) - 1)
        index = find_index(frames, value)
        if index is None:
            raise Invalid("The reference frame is unavailable in this drum.")
        return index
    index = model.integer(value, "reference frame", 0, len(frames) - 1)
    return index


def find_index(frames, key):
    return next((i for i, frame in enumerate(frames) if frame["id"] == key), None)


def alignment_inputs(data, draft):
    saved = draft.get("alignment") or {}
    reference = data.get("reference_frame", data.get("reference", data.get("ref", data.get("reference_index"))))
    reference_index = resolve_reference_index(draft, reference)

    raw_regions = data.get("regions", data.get("marks", data.get("landmarks")))
    if raw_regions is None:
        raw_regions = saved.get("regions")
    regions = model.reference_regions(raw_regions)

    space = str(data.get("space", data.get("coordinate_space", saved.get("space", "source")))).lower()
    if space in {"frame", "image", "negative", "source-normalized"}:
        space = "source"
    elif space in {"print", "prepared", "cell-normalized"}:
        space = "cell"
    model.option(space, ("cell", "source"), "reference coordinate space")

    max_shift = model.max_alignment_shift(data, draft["settings"])
    fallback_tol = draft["settings"].get(
        "align_edge_tolerance", model.DEFAULT_SETTINGS["align_edge_tolerance"])
    tolerance = data.get("cut_tolerance", data.get("edge_tolerance", fallback_tol))
    tolerance = model.number(tolerance, "edge ink tolerance", 0, .25)
    return reference_index, regions, space, max_shift, tolerance


def append_frames(draft, items, library):
    all_frames = draft["frames"] + items
    if len(all_frames) > model.MAX_IMAGES:
        raise Invalid(f"A drum holds at most {model.MAX_IMAGES} frames.")
    blobs = {frame["blob"] for frame in all_frames}
    blobs.update(frame["contact"]["sheet"] for frame in all_frames if "contact" in frame)
    size = sum(library.blob(digest).stat().st_size for digest in blobs)
    if size > MAX_DRUM_FRAME_BYTES:
        raise Invalid("Use no more than 100 MiB of source frame artwork per drum.")
    draft["frames"] = all_frames


def install(app, root):
    library = Library(root)
    api = Blueprint("zoebench", __name__, url_prefix="/zoe")

    @app.get("/")
    def zoetrope_desk():
        return render_template("zoe.html")

    @api.get("/health")
    def health():
        return jsonify(status="ready", service="zoebench")

    @api.get("/config")
    def config():
        return jsonify(
            settings=model.DEFAULT_SETTINGS,
            modes={key: {"title": title, "note": MODE_NOTES[key]} for key, title in model.PROOF_MODES.items()},
        )

    @api.get("/drafts")
    def list_drafts():
        return jsonify(library.list())

    @api.post("/drafts")
    def new_draft():
        data = payload()
        return jsonify(library.create(model.string(data.get("title"), "a drum name"), model.settings(data.get("settings", {})))), 201

    @api.post("/demo")
    def new_demo():
        return jsonify(demo.create(library)), 201

    @api.post("/demo-alignment")
    def new_alignment_demo():
        return jsonify(demo.create_alignment(library)), 201

    @api.get("/drafts/<key>")
    def draft_detail(key):
        return jsonify(library.draft(key))

    @api.put("/drafts/<key>/settings")
    def update_settings(key):
        value = payload()

        def change(draft):
            draft["settings"] = model.settings(value.get("settings", {}), draft["settings"])

        return jsonify(library.change(key, value.get("revision"), change))

    @api.post("/drafts/<key>/frames")
    def add_frame(key):
        revision = form_revision()
        check_revision(library.draft(key), revision)
        image, original, filename = upload()
        title = model.string(request.form.get("title") or Path(filename).stem, "a frame title")
        frame = demo.frame_record(library, image, original, title, filename)
        return jsonify(library.change(key, revision, lambda draft: append_frames(draft, [frame], library))), 201

    @api.post("/drafts/<key>/contact")
    def add_contact(key):
        revision = form_revision()
        check_revision(library.draft(key), revision)
        image, original, filename = upload()
        rows = model.integer(request.form.get("rows"), "contact rows", 1, 12)
        cols = model.integer(request.form.get("columns"), "contact columns", 1, 12)
        count = model.integer(request.form.get("count"), "included contact frames", 1, min(rows * cols, model.MAX_IMAGES))
        gutter = model.integer(request.form.get("gutter"), "inside trim in pixels", 0, 150)
        cells = raster.contact_slices(image, rows, cols, count, gutter)
        source_name = Path(filename).stem[:72]
        sheet = library.keep(original)
        items = []
        for index, cell in enumerate(cells):
            name = f"{source_name} {index + 1:02}"
            item = demo.frame_record(library, cell, raster.png(cell), name, f"{name}.png")
            item["contact"] = {
                "sheet": sheet, "filename": filename, "rows": rows, "columns": cols,
                "gutter": gutter, "index": index,
            }
            items.append(item)
        return jsonify(library.change(key, revision, lambda draft: append_frames(draft, items, library))), 201

    @api.post("/drafts/<key>/prepare")
    def add_prepared(key):
        data = payload()
        revision = data.get("revision")
        check_revision(library.draft(key), revision)
        results = app.extensions["results"]
        project_id = model.string(data.get("project"), "the preparation project", 80)
        result_id = model.string(data.get("result"), "the prepared result", 80)
        prepared = results.metadata(project_id, result_id)
        if prepared["stale"]:
            raise Invalid("Rebuild the preparation result before sending it to this drum.")
        result_path = results.image_path(project_id, "image", result_id)
        content = result_path.read_bytes()
        pixels = raster.decode(content)
        title = (prepared["name"].strip() or "Prepared artwork")[:100]
        frame = demo.frame_record(library, pixels, content, title, model.filename(f"{title}.png"))
        frame["preparation"] = {"project": project_id, "result": result_id}
        updated = library.change(key, revision, lambda draft: append_frames(draft, [frame], library))
        return jsonify(draft=updated, title=title), 201

    @api.put("/drafts/<key>/frames/<frame_key>")
    def update_frame(key, frame_key):
        data = payload()

        def change(draft):
            frame = model.find(draft["frames"], frame_key, "source frame")
            frame["transform"] = model.frame_transform(data.get("transform", {}), frame["transform"])
            cal_data = None
            for alias in ("calibration", "registration", "alignment_layer", "base_transform"):
                if isinstance(data.get(alias), dict):
                    cal_data = data[alias]
                    break
            if cal_data is not None:
                frame["calibration"] = model.frame_calibration(
                    cal_data, model.calibration_of(frame),
                )

        return jsonify(library.change(key, data.get("revision"), change))

    @api.put("/drafts/<key>/frames/<frame_key>/calibration")
    def update_frame_calibration(key, frame_key):
        data = payload()

        def change(draft):
            frame = model.find(draft["frames"], frame_key, "source frame")
            cal_data = data.get("calibration") if isinstance(data.get("calibration"), dict) else data
            frame["calibration"] = model.frame_calibration(cal_data, model.calibration_of(frame))

        return jsonify(library.change(key, data.get("revision"), change))

    @api.put("/drafts/<key>/order")
    def reorder_frames(key):
        data = payload()

        def change(draft):
            draft["frames"] = model.frame_list_order(data.get("frames"), draft["frames"])

        return jsonify(library.change(key, data.get("revision"), change))

    @api.delete("/drafts/<key>/frames/<frame_key>")
    def remove_frame(key, frame_key):
        data = payload()

        def change(draft):
            draft["frames"].remove(model.find(draft["frames"], frame_key, "source frame"))

        return jsonify(library.change(key, data.get("revision"), change))

    @api.get("/drafts/<key>/frames/<frame_key>/<view>")
    def frame_asset(key, frame_key, view):
        draft = library.draft(key)
        frame = model.find(draft["frames"], frame_key, "source frame")
        raw = library.blob(frame["blob"])
        model.option(view, ("thumb", "original", "prepared"), "frame view")
        if view == "original":
            return send_file(raw, download_name=frame["filename"], as_attachment=True)
        if view == "thumb":
            pixels = raster.decode(raw.read_bytes())
            pixels.thumbnail((190, 210))
        else:
            track = layout.uniform_track(len(draft["frames"]), draft["settings"])
            index = next(index for index, item in enumerate(draft["frames"]) if item["id"] == frame_key)
            cal_flag = request.args.get("cal", request.args.get("calibration", "1"))
            use_calibration = str(cal_flag).lower() not in {"0", "off", "false", "none", "original"}
            pixels = proof.styled_frame(
                lambda _: raster.decode(raw.read_bytes()), draft, track, index, use_calibration,
            )
        return Response(raster.png(pixels), mimetype="image/png")

    @api.post("/drafts/<key>/scan")
    def add_scan(key):
        revision = form_revision()
        draft = library.draft(key)
        check_revision(draft, revision)
        image, original, filename = upload()
        registration = model.source_scan({}, image.width, image.height, draft["settings"]["circumference"])
        item = {"blob": library.keep(original), "filename": filename, "width": image.width,
                "height": image.height, "registration": registration}
        return jsonify(library.change(key, revision, lambda saved: saved.update(scan=item))), 201

    @api.put("/drafts/<key>/scan")
    def register_scan(key):
        data = payload()

        def change(draft):
            scan = draft["scan"]
            if scan is None:
                raise Invalid("Import a slot scan before saving the measurement rectangle.")
            scan["registration"] = model.source_scan(
                data.get("registration", {}), scan["width"], scan["height"], draft["settings"]["circumference"],
            )

        return jsonify(library.change(key, data.get("revision"), change))

    @api.get("/drafts/<key>/scan/image")
    def scan_image(key):
        draft = library.draft(key)
        if draft["scan"] is None:
            raise Invalid("This drum does not have a slot scan yet.")
        image = raster.decode(library.blob(draft["scan"]["blob"]).read_bytes())
        image.thumbnail((1600, 500))
        return Response(raster.png(image), mimetype="image/png")

    def load_source(frame):
        return raster.decode(library.blob(frame["blob"]).read_bytes())

    def revision_or_current(data, draft):
        revision = (data or {}).get("revision")
        return revision if type(revision) is int else draft["revision"]

    @api.get("/drafts/<key>/alignment")
    def alignment_detail(key):
        draft = library.draft(key)
        return jsonify(setup=draft.get("alignment"), plan=draft.get("alignment_plan"))

    @api.put("/drafts/<key>/alignment")
    def save_alignment(key):
        data = payload()
        draft = library.draft(key)
        revision = revision_or_current(data, draft)
        reference_index, regions, space, max_shift, _tol = alignment_inputs(data, draft)

        def change(saved):
            saved["alignment"] = {
                "reference_frame": saved["frames"][reference_index]["id"],
                "regions": regions, "space": space, "max_shift_mm": max_shift,
                "updated": stamp(),
            }

        return jsonify(library.change(key, revision, change))

    def compute_plan(key, data):
        draft = library.draft(key)
        reference_index, regions, space, max_shift, tolerance = alignment_inputs(data, draft)
        plan = alignment.suggest_plan(
            draft, load_source, reference_index, regions, space, max_shift, tolerance,
        )
        return draft, reference_index, regions, space, max_shift, plan

    @api.post("/drafts/<key>/alignment/suggest")
    @api.post("/drafts/<key>/align")
    @api.post("/drafts/<key>/align/suggest")
    @api.post("/drafts/<key>/registration/suggest")
    def alignment_suggest(key):
        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise Invalid("Provide a JSON object of alignment options.")
        given_revision = data.get("revision")
        draft, reference_index, regions, space, max_shift, plan = compute_plan(key, data)
        if type(given_revision) is not int:
            # Stateless callers still get the full proposal in the response.
            return jsonify({**plan, "plan": plan, "alignment_plan": plan, "draft_id": key})

        check_revision(draft, given_revision)

        def change(saved):
            saved["alignment"] = {
                "reference_frame": saved["frames"][reference_index]["id"],
                "regions": regions, "space": space, "max_shift_mm": max_shift,
                "updated": stamp(),
            }
            saved["settings"]["align_max_shift_mm"] = max_shift
            saved["alignment_plan"] = {**plan, "created": stamp()}

        return jsonify(library.change(key, given_revision, change))

    def _choice_targets(data, draft):
        raw = data.get("frames", data.get("choices", data.get("adjustments")))
        if raw is None:
            plan = draft.get("alignment_plan") or {}
            raw = [
                {"frame": row["frame"], "shift_x_mm": row["shift_x_mm"],
                 "shift_y_mm": row["shift_y_mm"], "status": row["status"],
                 "confidence": row.get("confidence"), "score": row.get("score"),
                 "reason": row.get("reason"), "dx": row.get("dx"), "dy": row.get("dy")}
                for row in plan.get("frames", []) if row.get("apply", row.get("reliable"))
            ]
        if not isinstance(raw, list):
            raise Invalid("Provide a list of frame alignment choices.")
        targets = []
        for item in raw:
            if not isinstance(item, dict):
                raise Invalid("Each alignment choice must be an object.")
            if item.get("apply") is False or item.get("included") is False:
                continue
            frame_key = item.get("frame", item.get("id", item.get("frame_id")))
            frame = model.find(draft["frames"], frame_key, "aligned frame")
            shift_x = item.get("shift_x_mm", item.get("shift_x", item.get("x", 0)))
            shift_y = item.get("shift_y_mm", item.get("shift_y", item.get("y", 0)))
            status = item.get("status", "manual")
            targets.append((frame, {
                "shift_x": shift_x, "shift_y": shift_y,
                "dx": item.get("dx", 0.0) or 0.0, "dy": item.get("dy", 0.0) or 0.0,
                "status": status if status in model.CALIBRATION_STATUSES else "manual",
                "confidence": item.get("confidence"), "score": item.get("score"),
                "reason": item.get("reason"), "method": "landmark-translation",
                "updated": stamp(),
            }))
        return targets

    @api.post("/drafts/<key>/alignment/apply")
    @api.post("/drafts/<key>/align/apply")
    @api.post("/drafts/<key>/registration/apply")
    def alignment_apply(key):
        data = payload()
        draft = library.draft(key)
        has_choices = any(isinstance(data.get(name), list) for name in ("frames", "choices", "adjustments"))
        if not has_choices and not (draft.get("alignment_plan") or {}).get("frames"):
            setup = draft.get("alignment") or {}
            if setup.get("regions"):
                ref_index = resolve_reference_index(draft, setup.get("reference_frame"))
                plan = alignment.suggest_plan(
                    draft, load_source, ref_index, setup["regions"],
                    setup.get("space", "cell"),
                    float(setup.get("max_shift_mm", draft["settings"].get(
                        "align_max_shift_mm", model.DEFAULT_SETTINGS["align_max_shift_mm"]))),
                    draft["settings"].get("align_edge_tolerance",
                                          model.DEFAULT_SETTINGS["align_edge_tolerance"]),
                )
                data["frames"] = [
                    {"frame": row["frame"], "shift_x_mm": row["shift_x_mm"],
                     "shift_y_mm": row["shift_y_mm"], "status": row["status"],
                     "confidence": row.get("confidence"), "score": row.get("score"),
                     "reason": row.get("reason"), "dx": row.get("dx") or 0, "dy": row.get("dy") or 0}
                    for row in plan["frames"] if row.get("apply", row.get("reliable"))
                ]
        revision = revision_or_current(data, draft)

        def change(draft):
            targets = _choice_targets(data, draft)
            for frame, values in targets:
                frame["calibration"] = model.frame_calibration(
                    values, model.calibration_of(frame),
                )

        return jsonify(library.change(key, revision, change))

    @api.post("/drafts/<key>/alignment/reset")
    @api.post("/drafts/<key>/align/reset")
    def alignment_reset(key):
        data = payload()
        revision = revision_or_current(data, library.draft(key))

        def change(draft):
            for frame in draft["frames"]:
                frame["calibration"] = model.frame_calibration({})
            if data.get("clear_plan"):
                draft.pop("alignment_plan", None)

        return jsonify(library.change(key, revision, change))

    @api.post("/drafts/<key>/proofs")
    def retain_proof(key):
        data = payload()
        draft = library.draft(key)
        revision = data.get("revision")
        check_revision(draft, revision)
        mode = model.option(data.get("mode"), model.PROOF_MODES, "proof mode")
        if mode == "scan" and draft["scan"] is None:
            raise Invalid("Import a slit photograph before measuring openings.")
        if "frame" in data:
            active = model.integer(data["frame"], "proof frame", 0, model.MAX_IMAGES - 1)
            draft["settings"]["proof_frame"] = active
        image, summary, rows, dpi = proof.render(mode, draft, library)
        result = proof.header(mode, draft, summary, rows, image.size, dpi)
        result["image"] = library.keep(raster.png(image, dpi))
        updated, saved = library.retain(key, revision, result)
        return jsonify(draft=updated, proof=saved), 201

    @api.get("/drafts/<key>/proofs/<proof_key>")
    def proof_detail(key, proof_key):
        return jsonify(library.proof(library.draft(key), proof_key))

    @api.get("/drafts/<key>/proofs/<proof_key>/<format>")
    def proof_asset(key, proof_key, format):
        result = library.proof(library.draft(key), proof_key)
        mode = result["mode"]
        model.option(format, ("image", "png", "csv", "zip"), "proof attachment")
        if format in {"image", "png"}:
            return send_file(library.blob(result["image"]), mimetype="image/png", download_name=f"{mode}.png",
                             as_attachment=format == "png")
        if format == "csv":
            return send_file(BytesIO(proof.rows_csv(result["rows"])), mimetype="text/csv", as_attachment=True,
                             download_name=f"{mode}.csv")
        return send_file(BytesIO(proof.packet(library, result)), mimetype="application/zip", as_attachment=True,
                         download_name=f"{mode}-evidence.zip")

    app.register_blueprint(api)
