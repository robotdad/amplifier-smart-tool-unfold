"""Mechanical exact-layer boundaries; real renderer checks are explicitly gated."""
import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic

import psutil
import pytest
from PIL import Image

from unfold.backend import Backend
from unfold.layers import LAYER_FILES, _acquire, check_budget, stage_layer, validate_labels
from unfold.models import Scene, UnfoldError


def scene():
    return Scene.model_validate({
        "title": "Typed labels", "explanation": "Mechanical layer fixture", "duration": 1,
        "background": "#f6f4ee",
        "elements": [{"id": "foreground", "kind": "card", "x": 620, "y": 350, "width": 40,
                      "height": 60, "fill": "#10f020", "border": "#10f020", "radius": 0, "opacity": 1}],
        "tweens": [{"target": "foreground", "at": 0, "duration": 0, "opacity": 1}],
        "scene3d": {"background": "transparent", "environment": "lab_white", "post": "clean",
                    "nodes": [
                        {"id": "front", "shape": "sphere", "material": "matte", "color": "#f02010",
                         "position": [0, 1.5, 0], "size": 2, "ring": False, "entrance": "none",
                         "idle_motion": "none", "label": 'Label <literal> & "A"'},
                        {"id": "back", "shape": "sphere", "color": "#1020f0", "position": [2, 1.5, 2],
                         "label": "Second label", "appear_at": 0.2}],
                    "camera": [{"at": 0, "target": "front", "azimuth": -90, "elevation": 15, "distance": 10}]},
    })


def label():
    return {"text": "<literal>", "x": 1, "y": 2, "width": 120, "height": 41,
            "opacity": 0.5, "color": [255, 200, 100, 1], "background": [1, 2, 3, 0.5],
            "border": [2, 3, 4, 0.85], "shadow": [2, 3, 4, 0.35]}


@pytest.mark.parametrize("mutate", [
    lambda item: item.update(html="<script>"),
    lambda item: item.update(text="x"*41),
    lambda item: item.update(x=float("nan")),
    lambda item: item.update(y=1e20),
    lambda item: item.update(width=-1),
    lambda item: item.update(opacity=2),
    lambda item: item.update(border="url(file:///secret)"),
    lambda item: item.update(color=[256, 0, 0, 1]),
    lambda item: item.update(background=[0, 0, 0, -1]),
    lambda item: item.update(shadow=[True, 0, 0, 1]),
])
def test_invalid_label_packets_refused(mutate):
    value=label()
    mutate(value)
    with pytest.raises(ValueError):
        validate_labels([value])


def test_label_and_pixel_bounds():
    assert validate_labels([label()])[0]["text"] == "<literal>"
    with pytest.raises(ValueError):
        validate_labels([label()]*29)
    s=scene().model_copy(update={"duration": 60})
    with pytest.raises(UnfoldError) as caught:
        check_budget(s)
    assert caught.value.code == "RESOURCE_LIMIT"


def helper(tmp_path, body):
    script=tmp_path/"helper.py"
    script.write_text(
        "import sys,json,struct,time,os,subprocess\n"
        "def send(obj,data=b''):\n"
        " b=json.dumps(obj).encode();sys.stdout.buffer.write(struct.pack('>I',len(b))+b+data);sys.stdout.buffer.flush()\n"
        "send(dict(kind='identity',browser='fixture',babylon='9.28.0',renderer='SwiftShader fixture',premultipliedAlpha=False))\n"
        +body
    )
    out=tmp_path/"frames"
    out.mkdir()
    return [sys.executable,str(script)],out


def test_raw_pipe_roundtrip_and_sanitization(tmp_path,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","must-not-reach")
    monkeypatch.setenv("PRODUCER_BROWSER_GPU_MODE","hardware")
    argv,out=helper(tmp_path,
        "assert 'OPENAI_API_KEY' not in os.environ and 'PRODUCER_BROWSER_GPU_MODE' not in os.environ\n"
        "send(dict(kind='frame',index=0,width=1280,height=720,labels=[]),bytes([12,34,56,78])*(1280*720))\n"
        "assert sys.stdin.readline()=='\\n'\n"
        "send(dict(kind='done',count=1))\n")
    result=_acquire(argv,out,1280,720,1,monotonic()+10)
    with Image.open(out/"frame_000001.png") as im:
        assert im.getpixel((0,0)) == (12,34,56,78)
    assert result["count"] == 1 and result["frames"][0]["labels"] == []


@pytest.mark.parametrize("limit", ["bytes", "labels"])
def test_owner_budgets_fail_before_acknowledging_frame(tmp_path,monkeypatch,limit):
    argv,out=helper(tmp_path,
        "send(dict(kind='frame',index=0,width=1280,height=720,labels=[]),b'\\0'*(1280*720*4))\n"
        "sys.stdin.readline();time.sleep(60)\n")
    monkeypatch.setattr("unfold.layers.MAX_BYTES" if limit=="bytes" else "unfold.layers.MAX_LABEL_BYTES",1)
    with pytest.raises(UnfoldError) as caught:
        _acquire(argv,out,1280,720,1,monotonic()+10)
    assert caught.value.code == "RESOURCE_LIMIT"


def test_cancelled_owner_stops_still_owned_process(tmp_path,monkeypatch):
    import unfold.layers as layers

    pidfile=tmp_path/"owner-child"
    argv,out=helper(tmp_path,f"open({str(pidfile)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    real=layers.queue.Queue.get
    calls=0
    def cancel(self,*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            raise KeyboardInterrupt()
        return real(self,*args,**kwargs)
    monkeypatch.setattr(layers.queue.Queue,"get",cancel)
    with pytest.raises(KeyboardInterrupt):
        _acquire(argv,out,1280,720,1,monotonic()+10)
    if pidfile.exists():
        pid=int(pidfile.read_text())
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


@pytest.mark.parametrize("body", [
    "sys.stdout.buffer.write(struct.pack('>I',100000));sys.stdout.buffer.flush()\n",
    "send(dict(kind='frame',index=0,width=1280,height=720,labels=[]),b'truncated')\n",
    "send(dict(kind='done',count=0))\n",
    "send(dict(kind='frame',index=1,width=1280,height=720,labels=[]),b'\\0'*(1280*720*4))\n",
])
def test_malformed_acquisition_fails_closed(tmp_path,body):
    argv,out=helper(tmp_path,body)
    with pytest.raises(UnfoldError) as caught:
        _acquire(argv,out,1280,720,1,monotonic()+10)
    assert caught.value.code == "LAYER_ACQUISITION_FAILED"


def test_partial_blocked_read_deadline_stops_child_and_descendant(tmp_path):
    pidfile=tmp_path/"pids"
    argv,out=helper(tmp_path,
        f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"open({str(pidfile)!r},'w').write(str(os.getpid())+' '+str(p.pid))\n"
        "sys.stdout.buffer.write(b'\\x00');sys.stdout.buffer.flush();time.sleep(60)\n")
    start=monotonic()
    with pytest.raises(UnfoldError) as caught:
        _acquire(argv,out,1280,720,1,monotonic()+1)
    assert caught.value.code == "RESOURCE_LIMIT"
    assert monotonic()-start < 8
    for pid in map(int,pidfile.read_text().split()):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_final_stage_timeout_stops_capture_descendants(tmp_path):
    from unfold.backend import run

    script=tmp_path/"capture.py"
    pidfile=tmp_path/"capture-pids"
    script.write_text(
        "import sys,subprocess,os,time\n"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"open({str(pidfile)!r},'w').write(str(os.getpid())+' '+str(p.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(UnfoldError) as caught:
        run([sys.executable,str(script)],timeout=1,dynamic_frames=True)
    assert caught.value.code=="BACKEND_FAILED"
    for pid in map(int,pidfile.read_text().split()):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_helpers_are_source_bound_and_nonopted_source_unchanged(tmp_path,monkeypatch):
    b=Backend(tmp_path/"backend")
    monkeypatch.setattr(b,"require",lambda:None)
    b.gsap=tmp_path/"gsap.js"
    b.babylon=tmp_path/"babylon.js"
    b.gsap.write_text("// fixture")
    b.babylon.write_text("// fixture")
    s=scene()
    b.author(s,tmp_path/"source")
    assert all((tmp_path/"source"/name).is_file() for name in LAYER_FILES)
    (tmp_path/"source/layer_player.js").unlink()
    with pytest.raises(UnfoldError) as caught:
        b.render(tmp_path/"source",tmp_path/"never.mp4")
    assert caught.value.code=="SOURCE_CHANGED"
    for setting in (None,"environment"):
        plain=s.model_copy(deep=True)
        plain.scene3d.background=setting
        b.author(plain,tmp_path/str(setting))
        assert not any((tmp_path/str(setting)/name).exists() for name in LAYER_FILES)


needs_renderer=pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"),reason="Needs pinned renderer/browser")


@needs_renderer
def test_independent_label_pair_raw_alpha_atomic_seeks_and_fault(tmp_path):
    b=Backend(os.environ["UNFOLD_TEST_BACKEND"])
    s=scene()
    b.author(s,tmp_path/"source")
    for n in (1,2):
        stage=tmp_path/f"stage-{n}"
        stage.mkdir()
        stage_layer(b,s,tmp_path/"source",stage,monotonic()+180)
    manifests=[json.loads((tmp_path/f"stage-{n}/composition/layer-manifest.json").read_text()) for n in (1,2)]
    assert manifests[0]["frames"]==manifests[1]["frames"]
    for i in range(30):
        assert (tmp_path/f"stage-1/composition/layer-frames/frame_{i+1:06d}.png").read_bytes() == \
               (tmp_path/f"stage-2/composition/layer-frames/frame_{i+1:06d}.png").read_bytes()
    result=subprocess.run(
        ["node",str(Path(__file__).parent/"fixtures/exact_layers.cjs"),str(b.root),str(tmp_path)],
        capture_output=True,text=True,timeout=180,
    )
    (tmp_path/"browser.log").write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/"browser.json").read_text())
    assert report["fault"]["rejected"] and report["fault"]["hidden"]
    from PIL import ImageChops

    for i in (0,15,29):
        reference=Image.open(tmp_path/f"raw-{i}.png").convert("RGBA")
        acquired=Image.open(tmp_path/f"stage-1/composition/layer-frames/frame_{i+1:06d}.png").convert("RGBA")
        assert reference.tobytes()==acquired.tobytes()
        alpha=acquired.getchannel("A").histogram()
        assert alpha[0]>1000 and alpha[255]>1000 and sum(alpha[1:255])>50
        original=Image.open(tmp_path/f"label-original-{i}.png").convert("RGB")
        replay=Image.open(tmp_path/f"label-replay-{i}.png").convert("RGB")
        assert ImageChops.difference(original,replay).getbbox() is None