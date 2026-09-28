/* Private, per-render exact frame presentation. No fetch/CSP extension or cache.
 * HyperFrames 0.8.33 awaits hf-seek.detail.waitUntil before native capture.
 */
(() => {
  "use strict";
  let resolveReady,rejectReady;
  window.__unfoldScene3DReady=new Promise((a,b)=>{resolveReady=a;rejectReady=b;});
  function start(){
    const data=JSON.parse(document.getElementById("unfold-layer-frames").textContent);
    if(data.version!==1||data.fps!==30||!Number.isInteger(data.count)||data.count<1||data.count>1800||
       ![[1280,720],[1920,1080]].some(([w,h])=>data.width===w&&data.height===h)||
       data.count*data.width*data.height>512*1024*1024||!Array.isArray(data.frames)||data.frames.length!==data.count)
      throw Error("Invalid bounded layer manifest");
    for(const f of data.frames){
      if(!f||Object.keys(f).sort().join()!=="labels,sha256"||!/^[a-f0-9]{64}$/.test(f.sha256))
        throw Error("Invalid frame record");
      window.__unfoldLayerLabels.validate(f.labels);
    }
    let displayed=document.getElementById("scene3d"),labels=[],current=-1,pending=Promise.resolve();
    function hide(){displayed.style.visibility="hidden";labels.forEach(el=>el.style.visibility="hidden");}
    function seek(time){
      if(typeof time!=="number"||!Number.isFinite(time))return Promise.reject(Error("Invalid seek"));
      const index=Math.min(data.count-1,Math.max(0,Math.floor(time*30+1e-6)));
      pending=pending.then(async()=>{
        if(current===index)return;
        hide();
        const image=new Image(data.width,data.height);
        image.id="scene3d";image.style.cssText="position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none";
        image.src="layer-frames/frame_"+String(index+1).padStart(6,"0")+".png";
        await image.decode();
        if(image.naturalWidth!==data.width||image.naturalHeight!==data.height)throw Error("Layer dimensions changed");
        const nextLabels=window.__unfoldLayerLabels.build(data.frames[index].labels);
        // No await between DOM replacement and matching labels. Capture observes
        // both or rejects; an earlier delayed decode can never overwrite newer work.
        image.dataset.frame=String(index);displayed.replaceWith(image);
        labels.forEach(el=>el.remove());
        let prior=image;
        for(const el of nextLabels){prior.after(el);prior=el;}
        displayed=image;labels=nextLabels;current=index;
        for(let i=0;i<labels.length;i++){
          const rect=labels[i].getBoundingClientRect(),want=data.frames[index].labels[i];
          if(Math.abs(rect.width-want.width)>0.01||Math.abs(rect.height-want.height)>0.01)
            throw Error("Label layout differs from acquisition runtime");
        }
      }).catch(error=>{hide();document.documentElement.dataset.scene3dError=String(error);throw error;});
      return pending;
    }
    window.__unfoldLayerSeek=seek;
    window.addEventListener("hf-seek",event=>{
      const task=seek(event.detail?.time??0);
      if(typeof event.detail?.waitUntil==="function")event.detail.waitUntil(task);
      else task.catch(()=>{}); // uncompiled preview; never a capture-ready claim
    });
    seek(0).then(resolveReady,rejectReady);
  }
  function boot(){try{start();}catch(error){document.documentElement.dataset.scene3dError=String(error);rejectReady(error);}}
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot,{once:true});else boot();
})();