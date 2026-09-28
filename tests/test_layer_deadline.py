"""One absolute render deadline through regeneration and every atlas decode.

Clocks/process doubles pin admission semantics; a real owned-process timeout test
proves descendant cleanup. No browser/model/full video needed for this boundary.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from time import monotonic

import psutil
import pytest
from PIL import Image

import unfold.backend as module
import unfold.layers as layers
from unfold.backend import Backend, decode_screen_atlases, probe_video_duration
from unfold.models import Scene, UnfoldError
from unfold.store import digest


def scene(transparent=True):
    return Scene.model_validate({
        "title": "Clock", "explanation": "Deadline seam", "duration": 1,
        "elements": [{"id": "text", "kind": "text", "text": "Keep me",
                      "x": 10, "y": 10, "width": 100, "height": 40}],
        "tweens": [{"target": "text", "at": 0, "duration": 0, "opacity": 1}],
        **({"scene3d": {"background": "transparent", "nodes": [{"id": "n", "position": [0,1,0]}],
                       "camera": [{"at": 0}]}} if transparent else {}),
    })


@pytest.fixture
def rig(tmp_path,monkeypatch):
    b=Backend(tmp_path/"backend")
    monkeypatch.setattr(b,"require",lambda:None)
    b.gsap=tmp_path/"gsap.js"
    b.gsap.write_text("// bounded fixture")
    b.babylon=tmp_path/"babylon.js"
    b.babylon.write_text("// bounded fixture")
    clock=[0.0]
    monkeypatch.setattr(module,"monotonic",lambda:clock[0])
    monkeypatch.setattr(layers,"monotonic",lambda:clock[0])
    return b,clock


def test_independent_repro_no_second_author_admitted_at_241(rig,tmp_path,monkeypatch):
    b,clock=rig
    s=scene()
    source=tmp_path/"source"
    b.author(s,source)
    calls=[]
    author=b.author
    def elapsed(*args,**kwargs):
        calls.append((clock[0],kwargs.get("deadline")))
        result=author(*args,**kwargs)  # real generation; only elapsed time is injected
        clock[0]+=241
        return result
    monkeypatch.setattr(b,"author",elapsed)
    monkeypatch.setattr(layers,"stage_layer",lambda *a,**kw:pytest.fail("Expired stage admitted"))
    with pytest.raises(UnfoldError) as caught:
        b.render(source,tmp_path/"never.mp4")
    assert caught.value.code=="RESOURCE_LIMIT"
    assert calls==[(0.0,240.0)]
    assert not (tmp_path/"never.mp4").exists()
    assert not list(tmp_path.glob("unfold-validate-*"))


def test_expired_after_hash_refuses_before_validation_author(rig,tmp_path,monkeypatch):
    b,clock=rig
    s=scene()
    b.author(s,tmp_path/"source")
    original=b.source_hash
    def expired(*a,**kw):
        result=original(*a,**kw)
        clock[0]=241
        return result
    monkeypatch.setattr(b,"source_hash",expired)
    monkeypatch.setattr(b,"author",lambda *a,**kw:pytest.fail("Author entered after deadline"))
    with pytest.raises(UnfoldError) as caught:
        b.render(tmp_path/"source",tmp_path/"no.mp4")
    assert caught.value.code=="RESOURCE_LIMIT"


def test_expired_stage_and_direct_author_do_not_start_work(rig,tmp_path,monkeypatch):
    b,clock=rig
    clock[0]=241
    monkeypatch.setattr(b,"require",lambda:pytest.fail("Expired author checked dependencies"))
    monkeypatch.setattr(b,"retained_resources",lambda *a,**kw:pytest.fail("Expired stage read resources"))
    for call in (lambda:b.author(scene(),tmp_path/"author",deadline=240),
                 lambda:layers.stage_layer(b,scene(),tmp_path,tmp_path/"stage",240)):
        with pytest.raises(UnfoldError) as caught:
            call()
        assert caught.value.code=="RESOURCE_LIMIT"
    assert not (tmp_path/"author").exists() and not (tmp_path/"stage").exists()


@pytest.mark.parametrize("expire_phase",["resources","acquisition_author","acquisition"])
def test_every_stage_admission_uses_same_deadline(rig,tmp_path,monkeypatch,expire_phase):
    b,clock=rig
    s=scene()
    source=tmp_path/"source"
    b.author(s,source)
    stage=tmp_path/"stage"
    stage.mkdir()
    events=[]
    resource=b.retained_resources
    author=b.author
    def resources(*a,**kw):
        events.append(("resources",clock[0],kw["deadline"]))
        result=resource(*a,**kw)
        if expire_phase=="resources":
            clock[0]=241
        return result
    def generate(*a,**kw):
        events.append(("author",clock[0],kw["deadline"]))
        result=author(*a,**kw)
        if expire_phase=="acquisition_author":
            clock[0]=241
        return result
    def acquire(*a):
        events.append(("acquire",clock[0],a[-1]))
        clock[0]=241
        return {}
    monkeypatch.setattr(b,"retained_resources",resources)
    monkeypatch.setattr(b,"author",generate)
    monkeypatch.setattr(layers,"_acquire",acquire)
    with pytest.raises(UnfoldError) as caught:
        layers.stage_layer(b,s,source,stage,240)
    assert caught.value.code=="RESOURCE_LIMIT"
    assert [e[0] for e in events]=={
        "resources":["resources"],"acquisition_author":["resources","author"],
        "acquisition":["resources","author","acquire"],
    }[expire_phase]
    assert all(t<240 and d==240 for _,t,d in events)
    assert not (stage/"composition").exists()


def fake_media(clock,calls,step=2):
    def run(argv,timeout=180,**kwargs):
        calls.append({"exe":argv[0],"clock":clock[0],"timeout":timeout,"deadline":kwargs.get("deadline")})
        clock[0]+=step
        if argv[0]=="ffprobe":
            return "1"
        output=Path(argv[-1])
        if "%" in output.name:
            output=output.with_name("f00001.png")
        Image.new("RGB",(512,2),(100,120,140)).save(output)
        return ""
    return run


def video_scene(tmp_path):
    s=scene()
    resource={}
    screens=[]
    for i in (1,2):
        identity=f"{i:032x}"
        video=tmp_path/f"source-{i}.mp4"
        video.write_bytes(b"synthetic media identity, decoder replaced in timing test"+bytes([i]))
        resource[identity]={"role":"video","path":str(video),"sha256":digest(video)}
        screens.append({"id":f"s{i}","asset_id":identity,"position":[i,1,0]})
    data=s.model_dump()
    data["scene3d"]["screens"]=screens
    return Scene.model_validate(data),resource


def test_multiple_video_assets_share_one_remaining_allowance(rig,tmp_path,monkeypatch):
    b,clock=rig
    s,resources=video_scene(tmp_path)
    calls=[]
    monkeypatch.setattr(module,"run",fake_media(clock,calls))
    b.author(s,tmp_path/"authored",resources,deadline=20)
    assert [(c["exe"],c["clock"],c["timeout"],c["deadline"]) for c in calls]==[
        ("ffprobe",0,20,20),("ffmpeg",2,18,20),("ffmpeg",4,16,20),
        ("ffprobe",6,14,20),("ffmpeg",8,12,20),("ffmpeg",10,10,20),
    ]
    assert clock[0]==12
    saved=json.loads((tmp_path/"authored/screens.json").read_text())
    assert set(saved)=={"s1","s2"}


def test_stage_regenerations_and_multiple_assets_do_not_reset_deadline(rig,tmp_path,monkeypatch):
    b,clock=rig
    s,resources=video_scene(tmp_path)
    calls=[]
    monkeypatch.setattr(module,"run",fake_media(clock,calls,step=1))
    source=tmp_path/"source"
    b.author(s,source,resources)
    calls.clear()
    clock[0]=0
    stage=tmp_path/"stage"
    stage.mkdir()
    admissions=[]
    author=b.author
    def generate(*a,**kw):
        admissions.append((Path(a[1]).name,clock[0],kw["deadline"]))
        return author(*a,**kw)
    def acquire(*args):
        assert args[-1]==50 and clock[0]==6
        clock[0]=20  # acquisition consumes the existing allowance
        return {"fixture":"not browser-executable"}
    monkeypatch.setattr(b,"author",generate)
    monkeypatch.setattr(layers,"_acquire",acquire)
    final=layers.stage_layer(b,s,source,stage,50)
    assert admissions==[("acquisition",0,50),("composition",20,50)]
    assert [c["deadline"] for c in calls]==[50]*12
    assert [c["timeout"] for c in calls]==[30,49,48,30,46,45,30,29,28,27,26,25]
    assert clock[0]==26 and (final/"layer-manifest.json").exists()


def test_png_packing_expiry_stops_before_next_page(rig,tmp_path,monkeypatch):
    _,clock=rig
    def decode(argv,timeout,**kwargs):
        out=Path(argv[-1])
        if out.name=="probe.png":
            Image.new("RGB",(512,4096),"red").save(out)
        else:
            # Nine tall tiles require two pages (eight columns, one row).
            for n in range(1,10):
                Image.new("RGB",(512,4096),"red").save(out.with_name(f"f{n:05d}.png"))
        return ""
    monkeypatch.setattr(module,"run",decode)
    save=Image.Image.save
    pages=[]
    def pack(self,fp,*a,**kw):
        result=save(self,fp,*a,**kw)
        if Path(fp).name.startswith("s_"):
            pages.append(Path(fp).name)
            clock[0]=11
        return result
    monkeypatch.setattr(Image.Image,"save",pack)
    with pytest.raises(UnfoldError) as caught:
        decode_screen_atlases(tmp_path/"input",tmp_path/"media","s",0,1,deadline=10)
    assert caught.value.code=="RESOURCE_LIMIT"
    assert pages==["s_0.png"] and not (tmp_path/"media/screens/s_1.png").exists()


def test_expiry_during_first_video_stops_before_second_asset(rig,tmp_path,monkeypatch):
    b,clock=rig
    s,resources=video_scene(tmp_path)
    calls=[]
    monkeypatch.setattr(module,"run",fake_media(clock,calls,step=3))
    with pytest.raises(UnfoldError) as caught:
        b.author(s,tmp_path/"authored",resources,deadline=7)
    assert caught.value.code=="RESOURCE_LIMIT"
    assert [(c["clock"],c["timeout"]) for c in calls]==[(0,7),(3,4),(6,1)]
    assert sum(c["exe"]=="ffprobe" for c in calls)==1


def test_legacy_media_timeouts_and_author_bytes_unchanged_without_deadline(rig,tmp_path,monkeypatch):
    b,clock=rig
    s,resources=video_scene(tmp_path)
    calls=[]
    monkeypatch.setattr(module,"run",fake_media(clock,calls))
    b.author(s,tmp_path/"legacy",resources)
    assert [c["timeout"] for c in calls]==[30,60,600,30,60,600]
    assert all(c["deadline"] is None for c in calls)
    clock[0]=0
    b.author(s,tmp_path/"bounded",resources,deadline=1000)
    def inventory(p):
        return {str(f.relative_to(p)):f.read_bytes() for f in p.rglob("*") if f.is_file()}
    assert inventory(tmp_path/"legacy")==inventory(tmp_path/"bounded")


def test_ordinary_2d_render_does_not_opt_into_new_preparation_deadline(rig,tmp_path,monkeypatch):
    b,clock=rig
    s=scene(False)
    source=tmp_path/"source"
    b.author(s,source)
    author=b.author
    seen=[]
    def generate(*a,**kw):
        seen.append(kw)
        return author(*a,**kw)
    def capture(args,**kw):
        assert args[-2:]==["--quality","standard"]
        assert not kw["force_screenshot"] and "dynamic_frames" not in kw
        (tmp_path/"out.mp4").write_bytes(b"offline fixture")
    monkeypatch.setattr(b,"author",generate)
    monkeypatch.setattr(b,"_run_cli",capture)
    monkeypatch.setattr(b,"probe",lambda path,alpha=False:dict(width=1280,height=720,frame_count=30,
                      fps="30/1",duration=1,encoded_duration=1))
    b.render(source,tmp_path/"out.mp4")
    assert seen==[{}] and clock[0]==0


def test_run_caps_remaining_after_spawn_and_keeps_flags(rig,monkeypatch):
    _,clock=rig
    seen={}
    class Stream:
        def close(self):
            pass
    class Process:
        stdout=Stream()
        stderr=Stream()
        returncode=0
        def communicate(self,timeout):
            seen["timeout"]=timeout
            return "ok",""
        def poll(self):
            return 0
    def spawn(argv,**kw):
        seen.update(argv=argv,env=kw["env"])
        clock[0]+=2
        return Process()
    monkeypatch.setenv("OPENAI_API_KEY","must not pass")
    monkeypatch.setattr(module.subprocess,"Popen",spawn)
    assert module.run(["ffmpeg","unchanged-filter"],timeout=600,deadline=10)=="ok"
    assert seen["timeout"]==8 and seen["argv"]==["ffmpeg","unchanged-filter"]
    assert "OPENAI_API_KEY" not in seen["env"] and "HF_STATIC_DEDUP" not in seen["env"]


def test_final_capture_and_output_probe_share_original_deadline(rig,tmp_path,monkeypatch):
    b,clock=rig
    s=scene()
    source=tmp_path/"source"
    b.author(s,source)
    events=[]
    def stage(backend,scene,directory,staging,deadline):
        events.append(("stage",deadline))
        clock[0]=100
        return Path(staging)
    def run(argv,timeout=180,**kw):
        events.append((argv[0],timeout,kw["deadline"]))
        if argv[0]=="node":
            assert kw["dynamic_frames"] and not kw.get("force_screenshot")
            (tmp_path/"out.mp4").write_bytes(b"fake media, only deadlines verified")
            clock[0]=120
            return ""
        assert argv[0]=="ffprobe"
        return json.dumps({"streams":[{"codec_type":"video","codec_name":"h264","nb_frames":"30",
            "avg_frame_rate":"30/1","duration":"1","width":1280,"height":720}]})
    monkeypatch.setattr(layers,"stage_layer",stage)
    monkeypatch.setattr(module,"run",run)
    b.render(source,tmp_path/"out.mp4")
    assert events==[("stage",240),("node",140,240),("ffprobe",180,240)]
    # run() itself further caps probe's ordinary180 default to the remaining120.


def test_probe_subprocess_effective_timeout_is_capped(rig,monkeypatch):
    b,clock=rig
    clock[0]=20
    class Stream:
        def close(self):
            pass
    class Process:
        stdout=Stream()
        stderr=Stream()
        returncode=0
        def communicate(self,timeout):
            assert timeout==5
            return json.dumps({"streams":[]}),""
        def poll(self):
            return 0
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**kw:Process())
    with pytest.raises(UnfoldError) as caught:
        b.probe("not-media",deadline=25)
    assert caught.value.code=="INVALID_RENDER"  # timeout verified without claiming a video


def test_expired_subprocess_does_not_launch(rig,monkeypatch):
    _,clock=rig
    clock[0]=11
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**kw:pytest.fail("Expired child launched"))
    with pytest.raises(UnfoldError) as caught:
        module.run(["ffmpeg"],deadline=10)
    assert caught.value.code=="RESOURCE_LIMIT"


def test_deadline_governed_ffmpeg_timeout_cleans_descendants(tmp_path,monkeypatch):
    # A tiny executable FFmpeg stand-in proves real process supervision without
    # depending on adversarial media latency. It spawns one observable descendant.
    helper=tmp_path/"ffmpeg-fixture.py"
    pids=tmp_path/"pids"
    helper.write_text(
        "import os,sys,subprocess,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"open({str(pids)!r},'w').write(str(os.getpid())+' '+str(child.pid))\n"
        "time.sleep(60)\n"
    )
    actual=module.subprocess.Popen
    def spawn(argv,**kw):
        assert argv[0]=="ffmpeg"
        return actual([sys.executable,str(helper)],**kw)
    monkeypatch.setattr(module.subprocess,"Popen",spawn)
    start=monotonic()
    with pytest.raises(UnfoldError) as caught:
        decode_screen_atlases(tmp_path/"unused.mp4",tmp_path/"media","screen",0,1,
                              deadline=module.monotonic()+1)
    assert caught.value.code=="RESOURCE_LIMIT"
    assert monotonic()-start<8
    for pid in map(int,pids.read_text().split()):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_duration_probe_cap_and_exhaustion(rig,monkeypatch):
    _,clock=rig
    calls=[]
    def run(argv,**kw):
        calls.append(kw)
        return "1"
    monkeypatch.setattr(module,"run",run)
    assert probe_video_duration("fixture",deadline=100)==1
    assert calls==[{"timeout":30,"deadline":100}]
    clock[0]=101
    with pytest.raises(UnfoldError):
        probe_video_duration("fixture",deadline=100)
    assert len(calls)==1


def test_deadline_hash_and_copy_are_incremental_and_byte_preserving(rig,tmp_path,monkeypatch):
    _,clock=rig
    source=tmp_path/"large"
    source.write_bytes(b"x"*(3*1024*1024))
    assert module.deadline_digest(source,10)==digest(source)
    module.deadline_copy(source,tmp_path/"copy",10)
    assert (tmp_path/"copy").read_bytes()==source.read_bytes()
    steps=iter([0,0,1,11,12])
    monkeypatch.setattr(module,"monotonic",lambda:next(steps))
    with pytest.raises(UnfoldError):
        module.deadline_copy(source,tmp_path/"partial",10)
    assert (tmp_path/"partial").stat().st_size<source.stat().st_size


@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="Needs local FFmpeg, not renderer")
def test_real_ffmpeg_deadline_and_default_atlases_are_byte_identical(tmp_path):
    video=tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=red:s=32x32:r=15:d=0.2",
                    "-c:v","libx264","-pix_fmt","yuv420p",str(video)],check=True,timeout=15)
    old=decode_screen_atlases(video,tmp_path/"old","s",0,0.2)
    new=decode_screen_atlases(video,tmp_path/"new","s",0,0.2,deadline=module.monotonic()+30)
    assert new==old
    for page in new["pages"]:
        filename=Path(page["file"]).name
        assert (tmp_path/"old/screens"/filename).read_bytes()==(tmp_path/"new/screens"/filename).read_bytes()


@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="Needs local FFmpeg, not renderer")
def test_real_ffmpeg_is_stopped_at_shared_deadline():
    # Throttled synthetic input to a null sink. No candidate video/browser render.
    start=monotonic()
    with pytest.raises(UnfoldError) as caught:
        module.run(["ffmpeg","-v","error","-nostdin","-re","-f","lavfi","-i",
                    "color=red:s=32x32:r=15","-t","60","-f","null","-"],
                   timeout=600,deadline=module.monotonic()+0.5)
    assert caught.value.code=="RESOURCE_LIMIT"
    assert monotonic()-start<5