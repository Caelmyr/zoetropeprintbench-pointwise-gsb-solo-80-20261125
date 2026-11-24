"""Local browser workflow for drum artwork, slot measurements and immutable proofs."""

from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import demo, layout, lineage, model, proof, raster
from .store import Library

MAX_DRUM_FRAME_BYTES = 100 * 1024 * 1024
MODE_NOTES = {
    "ribbon": "The artwork band includes the blank gluing tab and printer resolution.",
    "cut-guide": "The separate cut sheet identifies frame centers, boundaries and the gluing flap.",
    "slot-ruler": "Print the nominal opposite-frame viewing slots at actual size.",
    "direct-loop": "Step through the source artwork at its print framing.",
    "optical-loop": "Approximate one slit exposure at the current rotation and exposure time.",
    "neighbors": "Red shows the previous silhouette and teal shows the next silhouette.",
    "lineage": "Align the current loop against a retained proof: each old frame is used once and unreliable frames are flagged.",
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


def retained_lineage_proof(library, draft, proof_key):
    record = library.proof(draft, proof_key)
    if not record.get("frames"):
        raise Invalid("The retained proof does not carry a complete frame loop and cannot anchor a lineage.")
    return record


def lineage_paintings(library, record, draft):
    old_frames, old_settings = record["frames"], record["source_settings"]
    old_track = layout.uniform_track(len(old_frames), old_settings)
    old_painted = []
    for index, frame in enumerate(old_frames):
        cell = old_track.cells[index]
        source = raster.decode(library.blob(frame["blob"]).read_bytes())
        width, height = proof.screen_size(old_track, cell)
        old_painted.append(lineage.frame_image(source, frame, old_track, cell, width, height))
    new_track = layout.uniform_track(len(draft["frames"]), draft["settings"])
    new_painted = [proof.screen_frame(
        lambda frame: raster.decode(library.blob(frame["blob"]).read_bytes()), draft, new_track, index,
    ) for index in range(len(draft["frames"]))]
    return old_frames, old_painted, new_painted


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

    def _validated_mapping(payload, record, draft):
        if not isinstance(payload, dict) or not isinstance(payload.get("pairs"), list):
            raise Invalid("The adopted lineage mapping is malformed.")
        used_old, seen_new = set(), set()
        pairs = []
        for item in payload["pairs"]:
            new_index = model.integer(item.get("new_frame"), "a current frame", 1, len(draft["frames"])) - 1
            if new_index in seen_new:
                raise Invalid("Each current frame can appear only once in the lineage.")
            seen_new.add(new_index)
            old_value = item.get("old_frame")
            entry = {
                "new_frame": new_index + 1, "new_title": draft["frames"][new_index]["title"],
                "old_frame": None, "old_title": None, "pose_score": item.get("pose_score"),
                "continuity_score": item.get("continuity_score"),
                "reliable": bool(item.get("reliable")), "anchor": bool(item.get("anchor")),
                "status": model.option(item.get("status", "unmatched"),
                                       ("matched", "anchor", "uncertain", "unmatched"), "lineage status"),
            }
            if old_value not in (None, ""):
                old_index = model.integer(old_value, "an old frame", 1, len(record["frames"])) - 1
                if old_index in used_old:
                    raise Invalid("Each retained frame can be paired with only one current frame.")
                used_old.add(old_index)
                entry["old_frame"] = old_index + 1
                entry["old_title"] = record["frames"][old_index]["title"]
            pairs.append(entry)
        if len(seen_new) != len(draft["frames"]):
            raise Invalid("The lineage must cover every current frame.")
        dropped = []
        for item in payload.get("dropped", []):
            old_index = model.integer(item.get("old_frame"), "an old frame", 1, len(record["frames"])) - 1
            dropped.append({"old_frame": old_index + 1,
                            "old_title": record["frames"][old_index]["title"]})
        questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
        return {"pairs": pairs, "dropped": dropped}, questions

    @api.post("/drafts/<key>/proofs")
    def retain_proof(key):
        data = payload()
        draft = library.draft(key)
        revision = data.get("revision")
        check_revision(draft, revision)
        mode = model.option(data.get("mode"), model.PROOF_MODES, "proof mode")
        if mode == "scan" and draft["scan"] is None:
            raise Invalid("Import a slit photograph before measuring openings.")
        extra = None
        if mode == "lineage":
            record = retained_lineage_proof(library, draft, model.identifier(data.get("lineage_proof")))
            anchor_old = model.integer(data.get("anchor_old"), "the old anchor frame", 0, model.MAX_IMAGES - 1)
            anchor_new = model.integer(data.get("anchor_new"), "the current anchor frame", 0, model.MAX_IMAGES - 1)
            if anchor_old >= len(record["frames"]) or anchor_new >= len(draft["frames"]):
                raise Invalid("The anchor frame must exist in both loops.")
            fixed_mapping, questions = (None, None)
            if data.get("mapping"):
                fixed_mapping, questions = _validated_mapping(data["mapping"], record, draft)
            image, summary, rows, dpi, extra = proof.render(
                "lineage", draft, library,
                {"saved_proof": record, "anchor_old": anchor_old, "anchor_new": anchor_new,
                 "fixed_mapping": fixed_mapping, "questions": questions},
            )
        else:
            if "frame" in data:
                active = model.integer(data["frame"], "proof frame", 0, model.MAX_IMAGES - 1)
                draft["settings"]["proof_frame"] = active
            image, summary, rows, dpi = proof.render(mode, draft, library)
        result = proof.header(mode, draft, summary, rows, image.size, dpi, extra)
        if mode == "lineage":
            result["lineage_old_frames"] = record["frames"]
        result["image"] = library.keep(raster.png(image, dpi))
        updated, saved = library.retain(key, revision, result)
        return jsonify(draft=updated, proof=saved), 201

    @api.post("/drafts/<key>/lineage/analyze")
    def lineage_analyze(key):
        data = payload()
        draft = library.draft(key)
        record = retained_lineage_proof(library, draft, model.identifier(data.get("lineage_proof")))
        anchor_old = model.integer(data.get("anchor_old"), "the old anchor frame", 0, model.MAX_IMAGES - 1)
        anchor_new = model.integer(data.get("anchor_new"), "the current anchor frame", 0, model.MAX_IMAGES - 1)
        if anchor_old >= len(record["frames"]) or anchor_new >= len(draft["frames"]):
            raise Invalid("The anchor frame must exist in both loops.")
        old_frames, old_painted, new_painted = lineage_paintings(library, record, draft)
        alignment, questions = lineage.build_analysis(old_painted, new_painted, anchor_old, anchor_new)
        mapping, dropped = lineage.correspondence(alignment, old_frames, draft["frames"])
        score_rows = [
            {"old_frame": old + 1, "new_frame": new + 1, "pose_score": round(float(value), 4)}
            for old, row in enumerate(alignment.score_table) for new, value in enumerate(row)
        ]
        return jsonify(
            anchor_old_frame=anchor_old + 1, anchor_new_frame=anchor_new + 1,
            pairs=mapping, dropped=dropped, questions=questions, score_table=score_rows,
            old_frames=[{"frame": index + 1, "title": frame["title"]}
                        for index, frame in enumerate(old_frames)],
        )

    @api.get("/drafts/<key>/lineage/<proof_key>/old-frame/<int:frame_number>")
    def lineage_old_frame(key, proof_key, frame_number):
        draft = library.draft(key)
        record = retained_lineage_proof(library, draft, proof_key)
        if not 1 <= frame_number <= len(record["frames"]):
            raise Invalid("That old frame is outside the retained loop.")
        frame = record["frames"][frame_number - 1]
        track = layout.uniform_track(len(record["frames"]), record["source_settings"])
        cell = track.cells[frame_number - 1]
        source = raster.decode(library.blob(frame["blob"]).read_bytes())
        image = lineage.frame_image(source, frame, track, cell, *proof.screen_size(track, cell))
        return Response(raster.png(image), mimetype="image/png")

    @api.get("/drafts/<key>/lineage/<proof_key>/overlay/<int:frame_number>")
    def lineage_overlay(key, proof_key, frame_number):
        draft = library.draft(key)
        record = library.proof(draft, proof_key)
        if record.get("mode") != "lineage" or not record.get("lineage"):
            raise Invalid("Open a retained cross-revision lineage proof to view its overlays.")
        if not 1 <= frame_number <= len(draft["frames"]):
            raise Invalid("That current frame is outside this drum.")
        new_index = frame_number - 1
        new_track = layout.uniform_track(len(draft["frames"]), draft["settings"])
        new_image = proof.screen_frame(
            lambda frame: raster.decode(library.blob(frame["blob"]).read_bytes()),
            draft, new_track, new_index,
        )
        pair = next(item for item in record["lineage"]["pairs"] if item["new_frame"] == frame_number)
        if pair["old_frame"] is None:
            image = lineage.unmatched_canvas(
                new_image, draft["settings"]["stock"],
                f"new {frame_number} · no reliable old counterpart",
            )
        else:
            old_index = pair["old_frame"] - 1
            old_frame = record["lineage_old_frames"][old_index]
            old_track = layout.uniform_track(len(record["lineage_old_frames"]), record["source_settings"])
            old_cell = old_track.cells[old_index]
            old_source = raster.decode(library.blob(old_frame["blob"]).read_bytes())
            old_image = lineage.frame_image(old_source, old_frame, old_track, old_cell,
                                            *proof.screen_size(old_track, old_cell))
            image = lineage.overlay_pair(
                new_image, old_image, draft["settings"]["stock"],
                f"new {frame_number} / old {pair['old_frame']}"
                + ("  (uncertain)" if pair["status"] == "uncertain" else ""),
            )
        return Response(raster.png(image), mimetype="image/png")

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
