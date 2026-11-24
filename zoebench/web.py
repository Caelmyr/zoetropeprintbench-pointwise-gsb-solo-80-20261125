"""Local browser workflow for drum artwork, slot measurements and immutable proofs."""

import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import demo, layout, model, proof, raster, stencil, stencil_view
from .store import Library

MAX_DRUM_FRAME_BYTES = 100 * 1024 * 1024
PLAN_CACHE = {}
PLAN_CACHE_LIMIT = 200
MODE_NOTES = {
    "ribbon": "The artwork band includes the blank gluing tab and printer resolution.",
    "cut-guide": "The separate cut sheet identifies frame centers, boundaries and the gluing flap.",
    "slot-ruler": "Print the nominal opposite-frame viewing slots at actual size.",
    "direct-loop": "Step through the source artwork at its print framing.",
    "optical-loop": "Approximate one slit exposure at the current rotation and exposure time.",
    "neighbors": "Red shows the previous silhouette and teal shows the next silhouette.",
    "coverage": "Compare visible ink, clipped ink and ink inside the quiet margin.",
    "motion": "Trace the center of visible ink across the full loop.",
    "contrast": "Blue marks very dark values and ochre marks very light values on the selected paper.",
    "scan": "Read light openings inside the scan rectangle. Orange boxes touch the captured edge.",
    "scale": "Measure the printed 100 mm line with a ruler before cutting.",
    "assembly": "Bring the active ends together as they will look when the tab sits underneath.",
    "stencil": "Millimetre paper-stencil cut sheet: dark shapes are removed, teal ties stay and registration marks align repeat brushing.",
    "stencil-brush": "See brushing through the stencil at the original ribbon cell, separating tie-blocked ink from paper that was always blank.",
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


def plan_parameters(data, draft):
    settings = draft["settings"]
    raw_width = data.get("bridge_mm", data.get(
        "min_bridge_mm", data.get("bridge_width_mm", data.get(
            "min_bridge_width_mm", data.get("tie_width_mm", data.get(
                "bridge_width", data.get("min_bridge_width",
                                        settings.get("bridge_mm", 2.0))))))))
    params = {
        "frame": data.get("frame", data.get("frame_index", settings.get("proof_frame", 0))),
        "frame_id": data.get("frame_id"),
        "bridge_mm": stencil.bridge_width(raw_width),
        "marks": data.get("marks", data.get("protect", data.get("protected_marks", data.get("lines")))),
        "ink_threshold": model.number(data.get("ink_threshold", .5), "ink threshold", .05, .95),
        "ink_mode": model.option(data.get("ink_mode", "auto"),
                                 ("auto", "alpha", "dark"), "ink mode"),
    }
    if data.get("protect_radius_mm") is not None:
        params["protect_radius_mm"] = model.number(
            data["protect_radius_mm"], "protected-line radius (mm)", .3, 12)
    return params


def cache_plan(key, params):
    plan_id = uuid4().hex
    PLAN_CACHE[plan_id] = {"key": key, "params": params, "ts": time.time()}
    if len(PLAN_CACHE) > PLAN_CACHE_LIMIT:
        stale = sorted(PLAN_CACHE, key=lambda item: PLAN_CACHE[item]["ts"])
        for old in stale[:len(PLAN_CACHE) - PLAN_CACHE_LIMIT]:
            PLAN_CACHE.pop(old, None)
    return plan_id


def parameters_for(data, key, library):
    plan_id = data.get("plan_id")
    if plan_id:
        cached = PLAN_CACHE.get(plan_id)
        if not cached or cached["key"] != key:
            raise Invalid("This tie layout expired. Plan it again before adopting.")
        return cached["params"]
    return plan_parameters(data, library.draft(key))


def current_compatibility(draft, record):
    track = layout.uniform_track(len(draft["frames"]), draft["settings"])
    index, frame = stencil.resolve_frame(draft, record.get("frame"), record.get("frame_id"))
    return stencil.compatibility(record, frame, track, track.cells[index])


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
            pixels = proof.styled_frame(lambda _: raster.decode(raw.read_bytes()), draft, track, index)
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

    # ------------------------------------------------------------------
    # Reusable paper stencil: plan ties, adopt an edition, check refits.
    # ------------------------------------------------------------------

    @api.post("/drafts/<key>/stencil/plan")
    @api.post("/drafts/<key>/stencil/bridges")
    @api.post("/drafts/<key>/stencil/bridges/plan")
    @api.post("/drafts/<key>/stencil/bridge-plan")
    @api.post("/drafts/<key>/stencil/tie-plan")
    def stencil_plan_route(key):
        data = request.get_json(silent=True) or {}
        draft = library.draft(key)
        track = layout.uniform_track(len(draft["frames"]), draft["settings"])
        params = plan_parameters(data, draft)
        plan, _canvas, _ink = stencil.plan_for_draft(draft, track, library, **params)
        plan_id = cache_plan(key, {k: v for k, v in params.items() if k != "frame_id"}
                             | {"frame_id": params.get("frame_id")})
        edition = stencil.adoption_record(plan, draft["revision"])
        compatibility = None
        if draft.get("stencil"):
            compatibility = current_compatibility(draft, draft["stencil"])
        return jsonify({
            "plan_id": plan_id,
            "preview_url": f"/zoe/drafts/{key}/stencil/plans/{plan_id}/image",
            "plan": edition, "stencil": edition, **edition,
            "compatibility": compatibility,
        })

    @api.get("/drafts/<key>/stencil/plans/<plan_id>/image")
    def stencil_plan_image(key, plan_id):
        cached = PLAN_CACHE.get(plan_id)
        if not cached or cached["key"] != key:
            raise Invalid("This tie preview expired. Plan the stencil again.")
        draft = library.draft(key)
        track = layout.uniform_track(len(draft["frames"]), draft["settings"])
        plan, canvas, ink = stencil.plan_for_draft(draft, track, library, **cached["params"])
        image = stencil_view.render_plan_preview(canvas, ink, plan, track.pixels_per_mm)
        return Response(raster.png(image), mimetype="image/png")

    def adopt_stencil_change(key, data):
        expected = data.get("revision")
        if expected is not None:
            expected = model.integer(expected, "the current drum revision", 1, 1_000_000_000)
        else:
            expected = library.draft(key)["revision"]
        params = parameters_for(data, key, library)

        def change(draft):
            track = layout.uniform_track(len(draft["frames"]), draft["settings"])
            plan, _c, _i = stencil.plan_for_draft(draft, track, library, **params)
            draft["stencil"] = stencil.adoption_record(plan, draft["revision"])

        updated = library.change(key, expected, change)
        compatibility = current_compatibility(updated, updated["stencil"])
        return updated, compatibility

    @api.post("/drafts/<key>/stencil/adopt")
    @api.post("/drafts/<key>/stencil")
    def stencil_adopt(key):
        data = request.get_json(silent=True) or {}
        updated, compatibility = adopt_stencil_change(key, data)
        record = updated["stencil"]
        return jsonify({**updated, "draft": updated, "stencil": record, "plan": record,
                        "compatibility": compatibility}), 200

    @api.put("/drafts/<key>/stencil")
    def stencil_put(key):
        return stencil_adopt(key)

    @api.get("/drafts/<key>/stencil")
    def stencil_detail(key):
        draft = library.draft(key)
        record = draft.get("stencil")
        if not record:
            return jsonify(stencil=None, plan=None, compatibility=None, compatible=None)
        compatibility = current_compatibility(draft, record)
        return jsonify(stencil=record, plan=record, compatibility=compatibility,
                       **{k: compatibility[k] for k in
                          ("matches", "compatible", "usable", "reusable", "method", "verdict")})

    @api.get("/drafts/<key>/stencil/compatibility")
    @api.get("/drafts/<key>/stencil/fit")
    @api.get("/drafts/<key>/stencil/refit")
    @api.post("/drafts/<key>/stencil/check")
    @api.post("/drafts/<key>/stencil/refit")
    def stencil_compatibility(key):
        draft = library.draft(key)
        record = draft.get("stencil")
        if not record:
            raise Invalid("Adopt a stencil before checking whether the cut sheet still fits.")
        compatibility = current_compatibility(draft, record)
        return jsonify(compatibility)

    def adopted_image_response(key, kind, as_png=False):
        draft = library.draft(key)
        if not draft.get("stencil"):
            raise Invalid("Adopt a stencil edition before printing its sheet.")

        def source(frame):
            return raster.decode(library.blob(frame["blob"]).read_bytes())

        track = layout.uniform_track(len(draft["frames"]), draft["settings"])
        draft["_stencil_request"] = {}
        if kind == "brush":
            image, _summary, _rows, _dpi = proof.stencil_brush(draft, track, source)
            dpi = track.dpi
            name = "stencil-brush"
        else:
            image, _summary, _rows, dpi = proof.stencil_cut(draft, track, source)
            dpi = dpi or track.dpi
            name = "stencil-cut-sheet"
        if as_png:
            return send_file(BytesIO(raster.png(image, dpi)), mimetype="image/png",
                             as_attachment=True, download_name=f"{name}.png")
        return Response(raster.png(image, dpi), mimetype="image/png")

    @api.get("/drafts/<key>/stencil/sheet.png")
    def stencil_sheet_png(key):
        return adopted_image_response(key, "cut", as_png=True)

    @api.get("/drafts/<key>/stencil/brush.png")
    def stencil_brush_png(key):
        return adopted_image_response(key, "brush", as_png=True)

    @api.get("/drafts/<key>/stencil/image")
    def stencil_image(key):
        kind = request.args.get("view", "cut")
        model.option(kind, ("cut", "sheet", "brush"), "stencil image view")
        return adopted_image_response(key, "brush" if kind == "brush" else "cut")

    @api.post("/drafts/<key>/proofs")
    def retain_proof(key):
        data = payload()
        draft = library.draft(key)
        revision = data.get("revision")
        check_revision(draft, revision)
        mode = model.option(model.canonical_mode(data.get("mode")), model.PROOF_MODES, "proof mode")
        if mode == "scan" and draft["scan"] is None:
            raise Invalid("Import a slit photograph before measuring openings.")
        if "frame" in data:
            active = model.integer(data["frame"], "proof frame", 0, model.MAX_IMAGES - 1)
            draft["settings"]["proof_frame"] = active
        if mode in {"stencil", "stencil-brush"} and data.get("adopt"):
            _params = parameters_for(data, key, library) if (data.get("plan_id") or any(
                k in data for k in ("bridge_mm", "marks", "protect"))) else None

            def adopt_before_proof(saved):
                track = layout.uniform_track(len(saved["frames"]), saved["settings"])
                params = _params or plan_parameters(data, saved)
                plan, _c, _i = stencil.plan_for_draft(saved, track, library, **params)
                saved["stencil"] = stencil.adoption_record(plan, saved["revision"])

            draft = library.change(key, revision, adopt_before_proof)
            revision = draft["revision"]
        # Pass panel/body stencil options to the renderer without persisting them.
        draft["_stencil_request"] = data if mode in {"stencil", "stencil-brush", "cut-guide"} else {}
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
