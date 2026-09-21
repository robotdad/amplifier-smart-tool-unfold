"""Real font files through intake, identity exchange, authoring and renderer delivery."""

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFont

from unfold import Brief, Grant, Unfold, UnfoldError
from unfold.backend import Backend
from unfold.fonts import inspect_font
from unfold.intelligence import Production
from unfold.models import Scene
from unfold.store import digest, uid

FIXTURES = Path(__file__).parent / "fixtures/fonts"
REGULAR = FIXTURES / "IBMPlexSerif-Regular.ttf"
BOLD = FIXTURES / "IBMPlexSerif-BoldItalic.ttf"


def scene_for(regular, bold=None):
    elements = [dict(id="wordmark", kind="text", x=100, y=130, width=1080, height=140,
                     text="MADE", font_size=80, opacity=1, font_asset_id=regular,
                     letter_spacing=3, line_height=1.1)]
    if bold:
        elements.append(dict(id="expanded", kind="text", x=100, y=300, width=1080, height=120,
                             text="Motion and Design", font_size=48, opacity=1,
                             font_asset_id=bold, font_weight=700, font_style="italic"))
    return Scene(title="Serif identity", duration=1, elements=elements,
                 tweens=[dict(target="wordmark", at=0, duration=0.3, x=30)],
                 explanation="Editable brand text moves, then holds")


def import_faces(library):
    return [library.import_asset(p, role="font", rights="redistributable",
                                 attribution="IBM Corp. 2017; SIL OFL 1.1") for p in (REGULAR, BOLD)]


def test_import_metadata_and_reject_unsupported(tmp_path):
    library = Unfold(tmp_path / "library")
    regular, bold = import_faces(library)
    assert regular["font"] == dict(family="IBM Plex Serif", weight=400, style="normal", format="truetype")
    assert bold["font"]["weight"] == 700 and bold["font"]["style"] == "italic"
    invalid = tmp_path / "bad.ttf"
    invalid.write_text("not a font")
    with pytest.raises(UnfoldError, match="invalid font"):
        library.import_asset(invalid, role="font")
    unsupported = tmp_path / "font.woff2"
    shutil.copyfile(REGULAR, unsupported)
    with pytest.raises(UnfoldError, match="static .ttf or .otf"):
        library.import_asset(unsupported, role="font")
    assert len(library.assets()) == 2
    with pytest.raises(UnfoldError, match="characters"):
        inspect_font(REGULAR, text="\U0001f984")


def test_pack_role_roundtrip_omissions_and_remapping(tmp_path):
    library = Unfold(tmp_path / "first")
    regular, bold = import_faces(library)
    guidance = {"typography": {"display": {"font_asset_id": regular["id"]},
                               "heading": {"font_asset_id": bold["id"], "font_weight": 700,
                                           "font_style": "italic"}}}
    pack = library.save_pack("Serif team", guidance, [regular["id"], bold["id"]])
    exported = library.export_pack(pack["current_version"], tmp_path / "identity.zip")
    assert exported["manifest"]["assets"][0]["font"] == regular["font"]
    fresh = Unfold(tmp_path / "fresh")
    receipt = fresh.import_pack(exported["path"])
    version = fresh.inspect(receipt["version_id"])
    roles = version["guidance"]["typography"]
    assert roles["display"]["font_asset_id"] != regular["id"]
    assert roles["display"]["font_asset_id"] in version["assets"]
    assert fresh.asset(roles["heading"]["font_asset_id"])["font"] == bold["font"]
    assert not version["prerequisites"]
    library.update_asset(bold["id"], rights="restricted")
    omitted = library.export_pack(pack["current_version"], tmp_path / "restricted.zip")
    assert omitted["manifest"]["omissions"][0]["id"] == bold["id"]
    assert omitted["manifest"]["omissions"][0]["attribution"] == bold["attribution"]
    blocked = Unfold(tmp_path / "blocked")
    receipt = blocked.import_pack(omitted["path"])
    version = blocked.inspect(receipt["version_id"])
    carried = blocked.export_pack(version["id"], tmp_path / "carried.zip")
    another = Unfold(tmp_path / "another")
    carried_receipt = another.import_pack(carried["path"])
    assert another.inspect(carried_receipt["version_id"])["prerequisites"]
    with pytest.raises(UnfoldError, match="prerequisites"):
        blocked.create(Brief(title="Brand", intent="Brand", identity_version=version["id"]),
                       Grant(provider="openai", model="unused"))
    with pytest.raises(UnfoldError, match="supplies weight"):
        library.save_pack("Invalid face", {"typography": {"body": {
            "font_asset_id": regular["id"], "font_weight": 700}}}, [regular["id"]])
    with pytest.raises(UnfoldError, match="font in the pack"):
        library.save_pack("Missing face", guidance, [])


def test_staged_font_validated_before_promotion(tmp_path):
    library = Unfold(tmp_path / "library")
    directory = library.store.root / "staging"
    directory.mkdir()
    for valid in (True, False):
        path = directory / f"{valid}.ttf"
        path.write_bytes(REGULAR.read_bytes() if valid else b"broken font")
        info = path.stat()
        args = (str(path.relative_to(library.store.root)), info.st_dev, info.st_ino,
                digest(path), "Uploaded face", "font")
        if valid:
            assert library.import_staged_asset(*args)["font"]["family"] == "IBM Plex Serif"
        else:
            with pytest.raises(UnfoldError, match="invalid font"):
                library.import_staged_asset(*args)
    assert len(library.assets()) == 1
    assert len(list((library.store.root / "assets").iterdir())) == 1


def test_backend_face_requirements_and_patch_preserve_editable_text(tmp_path, monkeypatch):
    library = Unfold(tmp_path / "library")
    regular, bold = import_faces(library)
    backend = Backend(tmp_path / "backend")
    backend.gsap.parent.mkdir(parents=True)
    backend.gsap.write_text("// stub for author-only validation")
    monkeypatch.setattr(Backend, "require", lambda self: None)
    source = tmp_path / "source"
    scene = scene_for(regular["id"], bold["id"])
    with pytest.raises(UnfoldError, match="not available"):
        backend.author(scene, source)
    wrong = scene.model_copy(deep=True)
    wrong.elements[0].font_weight = 700
    resources = {a["id"]: a for a in (regular, bold)}
    with pytest.raises(UnfoldError, match="supplies weight"):
        backend.author(wrong, source, resources)
    wrong = scene.model_copy(deep=True)
    wrong.elements[0].font_style = "italic"
    with pytest.raises(UnfoldError, match="supplies weight"):
        backend.author(wrong, source, resources)
    operation = uid()
    library.store.put("operation", dict(id=operation, status="running"))
    production = Production(dict(library=str(library.store.root), backend=str(backend.root),
                                 operation_id=operation,
                                 grant=Grant(provider="openai", model="unused").model_dump(),
                                 brief=dict(duration=1), resources=resources))
    production.call("author", scene.model_dump_json())
    production.call("patch", json.dumps({"elements": {"wordmark": {"text": "EDITED"}}}))
    assert production.scene.elements[0].text == "EDITED"
    assert production.scene.elements[0].font_asset_id == regular["id"]
    assert production.scene.tweens == scene.tweens
    source = production.directory / "source"
    document = (source / "index.html").read_text()
    assert "document.fonts.load" in document and "font-synthesis:none" in document
    assert 'font-weight:700;font-style:italic' in document
    assert "letter-spacing:3.0px" in document and "line-height:1.1" in document
    assert "EDITED" in document and '<img' not in document
    retained = backend.retained_resources(source)
    Path(regular["path"]).unlink()
    backend.author(production.scene, tmp_path / "again", retained)
    assert (source / "index.html").read_bytes() == (tmp_path / "again/index.html").read_bytes()
    library.backend = backend
    revision = seed(library, production.scene, retained)
    revision_source = Path(library.inspect(revision)["source_path"])
    next((revision_source / "fonts").iterdir()).unlink()
    with pytest.raises(UnfoldError, match="missing or changed") as missing:
        library.render(revision)
    assert missing.value.code == "MISSING_FONT"
    (revision_source / "fonts.json").write_text(json.dumps({regular["id"]: {}}))
    assert library.inspect(revision)["source_error"]["code"] == "INVALID_FONT"
    next((source / "fonts").iterdir()).unlink()
    with pytest.raises(UnfoldError, match="missing or changed"):
        backend.source_hash(source)


def seed(library, scene, resources):
    project, revision = uid(), uid()
    source = library.store.workspace(uid()) / "source"
    checksum = library.backend.author(scene, source, resources)
    library.store.put("project", dict(id=project, kind="project", name="Brand", current_revision=revision,
                                      revisions=[revision]))
    library.store.put("revision", dict(id=revision, kind="revision", project_id=project,
                                      brief=dict(title=scene.title, intent="Brand", duration=1),
                                      source=str(source.relative_to(library.store.root)),
                                      source_sha256=checksum, artifacts=[]))
    return revision


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
def test_serif_render_fresh_pack_rerender_overlay_and_rights(tmp_path):
    original = Unfold(tmp_path / "original")
    regular, bold = import_faces(original)
    pack = original.save_pack("Brand", {"typography": {"display": {"font_asset_id": regular["id"]}}},
                              [regular["id"], bold["id"]])
    exported = original.export_pack(pack["current_version"], tmp_path / "pack.zip")
    library = Unfold(tmp_path / "fresh", os.environ["UNFOLD_TEST_BACKEND"])
    receipt = library.import_pack(exported["path"])
    faces = [library.asset(i) for i in library.inspect(receipt["version_id"])["assets"]]
    shutil.rmtree(original.store.root)
    scene = scene_for(faces[0]["id"], faces[1]["id"])
    # One face deliberately cannot be redistributed in the delivery.
    revision = seed(library, scene, {a["id"]: a for a in faces})
    library.update_asset(faces[1]["id"], rights="restricted")
    first = library.render(revision)
    for face in faces:
        Path(face["path"]).unlink()
    again = library.render(revision)
    assert first["frame_count"] == again["frame_count"] == 30
    f1 = library.sample_output(first["id"], [0, 0.5])["frames"]
    f2 = library.sample_output(again["id"], [0.5])["frames"]
    assert ImageChops.difference(Image.open(f1[1]["path"]), Image.open(f2[0]["path"])).getbbox() is None
    def ink(path):
        return Image.open(path).convert("L").crop((0, 100, 1280, 270)).point(
            lambda value: 255 if value > 150 else 0).getbbox()

    initial, settled = ink(f1[0]["path"]), ink(f1[1]["path"])
    # Check actual displacement and supplied-font metrics, not codec noise or a serif fallback.
    assert 29 <= settled[0] - initial[0] <= 31
    glyphs = ImageFont.truetype(str(REGULAR), 80).getmask("MADE").getbbox()
    expected_width = glyphs[2] - glyphs[0] + 3 * 3  # three letter gaps
    assert abs(initial[2] - initial[0] - expected_width) <= 2
    # Font selection must affect actual encoded pixels, not just the HTML metadata.
    default = scene.model_copy(deep=True)
    for e in default.elements:
        e.font_asset_id = None
    default_revision = seed(library, default, {})
    sans = library.render(default_revision)
    sans_frame = library.sample_output(sans["id"], [0.5])["frames"][0]
    assert ImageChops.difference(Image.open(f1[1]["path"]), Image.open(sans_frame["path"])).getbbox()
    delivery = library.configure_delivery(revision)
    overlay = library.render_delivery(delivery["id"], mode="overlay")
    frame = library.sample_output(overlay["id"], [0.5])["frames"][0]
    assert Image.open(frame["path"]).convert("RGBA").getpixel((10, 10))[3] == 0
    handoff = library.export_handoff(overlay["id"], tmp_path / "handoff.zip")
    assert handoff["manifest"]["fonts"][0]["font"]["family"] == "IBM Plex Serif"
    omission = handoff["manifest"]["omissions"][0]
    assert omission["rights"] == "restricted" and omission["attribution"] == faces[1]["attribution"]
    with zipfile.ZipFile(handoff["path"]) as z:
        assert z.read(handoff["manifest"]["fonts"][0]["file"]) == REGULAR.read_bytes()
        assert not any(faces[1]["id"] in n for n in z.namelist())


def test_otf_face_and_variable_font_rejection(tmp_path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont, newTable

    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    charstrings = {}
    for name in (".notdef", "A"):
        pen = T2CharStringPen(600, None)
        pen.moveTo((50, 0))
        pen.lineTo((300, 700))
        pen.lineTo((550, 0))
        pen.closePath()
        charstrings[name] = pen.getCharString()
    builder.setupCFF("UnfoldFixture", {"FullName": "Unfold Fixture", "FamilyName": "Unfold Fixture",
                                      "Weight": "Regular"}, charstrings, {})
    builder.setupHorizontalMetrics({g: (600, 0) for g in charstrings})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Unfold Fixture", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    path = tmp_path / "fixture.otf"
    builder.save(path)
    library = Unfold(tmp_path / "library")
    assert library.import_asset(path, role="font")["font"]["format"] == "opentype"
    with TTFont(REGULAR) as font:
        font["fvar"] = newTable("fvar")
        font["fvar"].axes, font["fvar"].instances = [], []
        font.save(tmp_path / "variable.ttf")
    with pytest.raises(UnfoldError, match="variable"):
        library.import_asset(tmp_path / "variable.ttf", role="font")


def test_pack_cannot_misdeclare_face_metadata(tmp_path):
    library = Unfold(tmp_path / "library")
    regular, _ = import_faces(library)
    pack = library.save_pack("Brand", {}, [regular["id"]])
    export = library.export_pack(pack["current_version"], tmp_path / "original.zip")
    with zipfile.ZipFile(export["path"]) as source, zipfile.ZipFile(tmp_path / "fake.zip", "w") as target:
        for name in source.namelist():
            raw = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(raw)
                manifest["assets"][0]["font"]["weight"] = 700
                raw = json.dumps(manifest).encode()
            target.writestr(name, raw)
    with pytest.raises(UnfoldError, match="metadata does not match"):
        library.import_pack(tmp_path / "fake.zip")


def test_existing_freeform_typography_guidance_remains_portable(tmp_path):
    library = Unfold(tmp_path / "library")
    pack = library.save_pack("Legacy", {"typography": "Keep text restrained"})
    exported = library.export_pack(pack["current_version"], tmp_path / "legacy.zip")
    fresh = Unfold(tmp_path / "fresh")
    receipt = fresh.import_pack(exported["path"])
    assert fresh.inspect(receipt["version_id"])["guidance"] == {"typography": "Keep text restrained"}


@pytest.mark.parametrize("message", [
    '[Browser:ERROR] Loading the font data:font/ttf violates the following Content Security Policy directive',
    '[Browser:ERROR] Failed to decode downloaded font',
    '[FrameCapture:sub_timeline_readiness_timeout] Sub-composition timelines did not become ready',
])
def test_font_capture_errors_are_not_accepted(monkeypatch, message):
    import subprocess

    from unfold.backend import run

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout=message, stderr=""))
    with pytest.raises(UnfoldError) as failure:
        run(["renderer"], reject_font_errors=True)
    assert failure.value.code == "FONT_LOAD_FAILED"
