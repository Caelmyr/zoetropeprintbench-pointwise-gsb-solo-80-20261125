"""Local browser workflow for drum artwork, slot measurements and immutable proofs."""

from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import demo, layout, model, proof, raster, separation
from .store import Library

MAX_DRUM_FRAME_BYTES = 100 * 1024 * 1024
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
            incoming = value.get("settings", {})
            draft["settings"] = model.settings(incoming, draft["settings"])
            tone_source = incoming if isinstance(incoming, dict) else value
            tones = separation.coerce_tones(tone_source) or separation.coerce_tones(value)
            if tones is not None:
                plan = separation.normalize_state(draft.get("separation"), draft)
                plan["tones"] = [
                    {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
                    {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
                ]
                draft["separation"] = plan

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
            plan = draft.get("separation")
            if plan:
                plan["marks"] = [mark for mark in plan.get("marks", [])
                                 if mark.get("frame") != frame_key]

        return jsonify(library.change(key, data.get("revision"), change))

    # ------------------------------------------------------------------
    # Ink separation: global primaries, assignment, marks and adoption
    # ------------------------------------------------------------------

    def separation_world(key):
        draft = library.draft(key)
        if len(draft["frames"]) < 2:
            raise Invalid("Load at least two frames to plan a whole-loop separation.")
        track = layout.uniform_track(len(draft["frames"]), draft["settings"])

        def source(frame):
            return raster.decode(library.blob(frame["blob"]).read_bytes())

        return draft, track, source

    def current_revision(key, data):
        """Use the supplied optimistic revision, but tolerate API callers
        that omit it by writing against the current draft."""
        revision = (data or {}).get("revision") if isinstance(data, dict) else None
        if isinstance(revision, str):
            try:
                revision = int(revision)
            except ValueError:
                revision = None
        if type(revision) is not int:
            revision = library.draft(key)["revision"]
        return revision

    def separation_response(saved):
        state = separation.public_state(saved)
        return jsonify(draft=saved, separation=state, plan=state, result=state)

    @api.get("/drafts/<key>/separation")
    @api.get("/drafts/<key>/ink-separation")
    @api.get("/drafts/<key>/inks")
    @api.get("/drafts/<key>/palette")
    def get_separation(key):
        draft = library.draft(key)
        return jsonify(separation=separation.public_state(draft),
                       revision=draft["revision"])

    @api.put("/drafts/<key>/separation")
    @api.post("/drafts/<key>/separation")
    @api.put("/drafts/<key>/inks")
    @api.post("/drafts/<key>/inks")
    def save_separation(key):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)

        def change(draft):
            plan = separation.normalize_state(draft.get("separation"), draft)
            tones = separation.coerce_tones(data)
            if tones is not None:
                plan["tones"] = [
                    {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
                    {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
                ]
            assignment_in = data.get("assignment") if isinstance(data.get("assignment"), dict) else None
            mapping_in = data.get("mapping") if isinstance(data.get("mapping"), dict) else None
            if assignment_in is not None:
                plan["assignment"].update(separation.coerce_assignment(plan, assignment_in))
            if mapping_in is not None:
                plan["assignment"].update(separation.coerce_assignment(plan, mapping_in))
            if isinstance(data.get("locked"), dict):
                plan["locked"] = separation.coerce_assignment(plan, data["locked"]) or data["locked"]
            if "adopted" in data:
                plan["adopted"] = bool(data["adopted"])
            if data.get("remove_mark"):
                target = data["remove_mark"]
                plan["marks"] = [m for m in plan["marks"] if m.get("id") != target]
            if isinstance(data.get("marks"), list):
                plan["marks"] = [m for m in data["marks"] if isinstance(m, dict)]
            if plan.get("swatches"):
                assignment, edges, marks, summary = separation.evaluate_assignment(
                    plan, plan["assignment"])
                plan["assignment"] = assignment
                plan["edge_view"] = edges
                plan["mark_preview"] = marks
                plan["summary"] = summary
            draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved)

    @api.post("/drafts/<key>/separation/sample")
    @api.post("/drafts/<key>/palette/sample")
    @api.post("/drafts/<key>/inks/sample")
    def sample_separation(key):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)
        _, track, source = separation_world(key)

        draft = library.draft(key)
        plan = separation.build_state(draft, track, source, payload=data)
        if "adopted" in data:
            plan["adopted"] = bool(data["adopted"])

        def change(saved_draft):
            saved_draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved), 201

    @api.post("/drafts/<key>/separation/suggest")
    @api.post("/drafts/<key>/inks/suggest")
    def suggest_separation(key):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)
        draft, track, source = separation_world(key)

        plan = separation.normalize_state(draft.get("separation"), draft)
        tones = separation.coerce_tones(data)
        if tones is not None:
            plan["tones"] = [
                {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
                {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
            ]
        if not plan["swatches"]:
            plan = separation.build_state(draft, track, source, payload=data, existing=plan)
        else:
            assignment, edges, marks, summary = separation.suggest_assignment(plan)
            plan["assignment"] = assignment
            plan["edge_view"] = edges
            plan["mark_preview"] = marks
            plan["summary"] = summary

        def change(saved_draft):
            saved_draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved)

    @api.post("/drafts/<key>/separation/adopt")
    @api.post("/drafts/<key>/inks/adopt")
    def adopt_separation(key):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)
        draft, track, source = separation_world(key)

        plan = separation.normalize_state(draft.get("separation"), draft)
        tones = separation.coerce_tones(data)
        if tones is not None:
            plan["tones"] = [
                {"id": "tone-1", "value": tones[0]["value"], "name": "Gray ink 1"},
                {"id": "tone-2", "value": tones[1]["value"], "name": "Gray ink 2"},
            ]
        if isinstance(data.get("assignment"), dict):
            plan["assignment"].update(separation.coerce_assignment(plan, data["assignment"]))
        if isinstance(data.get("mapping"), dict):
            plan["assignment"].update(separation.coerce_assignment(plan, data["mapping"]))
        if not plan["swatches"]:
            plan = separation.build_state(draft, track, source, payload=data, existing=plan)
        elif data.get("resuggest") or not plan.get("assignment"):
            assignment, edges, marks, summary = separation.suggest_assignment(plan)
            plan["assignment"] = assignment
            plan["edge_view"] = edges
            plan["mark_preview"] = marks
            plan["summary"] = summary
        plan["adopted"] = True

        def change(saved_draft):
            saved_draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved)

    @api.post("/drafts/<key>/separation/marks")
    @api.put("/drafts/<key>/separation/marks")
    @api.post("/drafts/<key>/inks/marks")
    @api.post("/drafts/<key>/marks")
    def add_separation_mark(key):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)
        draft, track, source = separation_world(key)
        payload = data.get("mark") if isinstance(data.get("mark"), dict) else data
        plan = separation.add_mark(draft.get("separation"), payload, draft, track, source)

        def change(saved_draft):
            saved_draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved), 201

    @api.delete("/drafts/<key>/separation/marks/<mark_id>")
    @api.post("/drafts/<key>/separation/marks/<mark_id>/delete")
    def delete_separation_mark(key, mark_id):
        data = request.get_json(silent=True) or {}
        revision = current_revision(key, data)

        def change(draft):
            plan = separation.normalize_state(draft.get("separation"), draft)
            plan["marks"] = [mark for mark in plan["marks"] if mark.get("id") != mark_id]
            if plan["swatches"]:
                assignment, edges, marks, summary = separation.evaluate_assignment(
                    plan, plan["assignment"])
                plan["assignment"] = assignment
                plan["edge_view"] = edges
                plan["mark_preview"] = marks
                plan["summary"] = summary
            draft["separation"] = plan

        saved = library.change(key, revision, change)
        return separation_response(saved)

    @api.get("/drafts/<key>/frames/<frame_key>/<view>")
    def frame_asset(key, frame_key, view):
        draft = library.draft(key)
        frame = model.find(draft["frames"], frame_key, "source frame")
        raw = library.blob(frame["blob"])
        model.option(view, ("thumb", "original", "prepared", "ink", "separated"), "frame view")
        if view == "original":
            return send_file(raw, download_name=frame["filename"], as_attachment=True)
        if view == "thumb":
            pixels = raster.decode(raw.read_bytes())
            pixels.thumbnail((190, 210))
        elif view in ("ink", "separated"):
            track = layout.uniform_track(len(draft["frames"]), draft["settings"])
            index = next(index for index, item in enumerate(draft["frames"]) if item["id"] == frame_key)
            cell = track.cells[index]
            width, height = proof.screen_size(track, cell)
            plan = separation.normalize_state(draft.get("separation"), draft)
            if plan.get("swatches") and plan.get("assignment"):
                pixels = separation.separated_cell(
                    raster.decode(raw.read_bytes()), frame, (width, height),
                    (cell.width, track.height), plan, draft["settings"]["stock"])
            else:
                pixels = proof.styled_frame(lambda _: raster.decode(raw.read_bytes()),
                                            draft, track, index)
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
        image, summary, rows, dpi, extra = proof.render_full(mode, draft, library)
        result = proof.header(mode, draft, summary, rows, image.size, dpi, extra)
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
            return send_file(BytesIO(proof.proof_csv(result)), mimetype="text/csv", as_attachment=True,
                             download_name=f"{mode}.csv")
        return send_file(BytesIO(proof.packet(library, result)), mimetype="application/zip", as_attachment=True,
                         download_name=f"{mode}-evidence.zip")

    app.register_blueprint(api)
