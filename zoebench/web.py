"""Local browser workflow for drum artwork, slot measurements and immutable proofs."""

from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import demo, layout, model, placement, proof, raster
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
PLACEMENT_NOTE = (
    "Fit one equal-width cell per measured opening, in picture order from the join, with every cell center inside "
    "the projection of its opening and no cell crossing the join."
)


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
            placement_note=PLACEMENT_NOTE,
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
            try:
                track = placement.active_track(draft, library) or layout.uniform_track(
                    len(draft["frames"]), draft["settings"],
                )
            except Invalid:
                # The frame strip is a preview; stale measured positions are reported on the proofs.
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

    @api.get("/drafts/<key>/placement.csv")
    def placement_csv(key):
        draft = library.draft(key)
        layout_record = draft.get("layout")
        if layout_record is None:
            raise Invalid("This drum prints the nominal equal band, so there are no measured positions to download.")
        rows = placement.rows(layout_record)
        return send_file(BytesIO(proof.rows_csv(rows)), mimetype="text/csv", as_attachment=True,
                         download_name="measured-placement.csv")

    @api.get("/drafts/<key>/placement")
    def current_placement(key):
        draft = library.draft(key)
        record = draft.get("layout")
        if record is None:
            return jsonify({"adopted": False, "stale": False})
        stale = placement.stale(draft, record)
        return jsonify({
            "adopted": True, "stale": stale, "cell_width_mm": record["cell_width_mm"],
            "cells": placement.rows(record), "report": record.get("report", {}),
            "scan_filename": record.get("scan_filename"),
        })

    @api.post("/drafts/<key>/placement/recommend")
    def recommend_placement(key):
        draft = library.draft(key)
        report = placement.recommendation(draft, library)
        report["revision"] = draft["revision"]
        return jsonify(report)

    @api.post("/drafts/<key>/placement")
    def adopt_placement(key):
        data = payload()
        draft = library.draft(key)
        check_revision(draft, data.get("revision"))
        report = placement.recommendation(draft, library)
        if report.get("blocked", {}).get("reason") == "count":
            raise Invalid(report["blocked"]["message"])
        if not report.get("feasible"):
            blocked = report.get("blocked") or {}
            raise Invalid(blocked.get("message") or "The measured openings cannot take this placement.")
        width = model.number(data.get("cell_width"), "the recommended cell width", 8, 680)
        if abs(width - report["cell_width_mm"]) > .01:
            raise Invalid("The measured openings changed. Recommend the placement again before adopting it.")

        def change(draft):
            draft["layout"] = placement.record(report, draft)
            draft["settings"]["cell_width"] = report["cell_width_mm"]

        return jsonify(library.change(key, draft["revision"], change)), 201

    @api.delete("/drafts/<key>/placement")
    def revert_placement(key):
        data = payload()

        def change(draft):
            if draft.get("layout") is None:
                raise Invalid("This drum already prints the nominal band.")
            draft["layout"] = None

        return jsonify(library.change(key, data.get("revision"), change))

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
        track = None
        if mode != "scan":
            track = placement.active_track(draft, library) or layout.uniform_track(
                len(draft["frames"]), draft["settings"],
            )
        image, summary, rows, dpi = proof.render(mode, draft, library, track)
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
