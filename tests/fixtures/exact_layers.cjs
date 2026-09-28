// Real browser checks of the packaged acquisition/presentation, no shader mocks.
const fs=require("node:fs"),path=require("node:path"),http=require("node:http"),assert=require("node:assert/strict");
const req=require("node:module").createRequire(path.join(path.resolve(process.argv[2]),"package.json"));
const P=req("puppeteer-core"),root=path.resolve(process.argv[3]),report={checks:[]};
const server=http.createServer((q,r)=>{
 const f=path.resolve(root,"."+new URL(q.url,"http://localhost").pathname);
 if(!f.startsWith(root+path.sep))return r.writeHead(403).end();
 try{r.setHeader("Content-Type",f.endsWith(".html")?"text/html":f.endsWith(".js")?"text/javascript":"application/octet-stream");r.end(fs.readFileSync(f));}
 catch{r.writeHead(404).end();}
});
(async()=>{
 await new Promise(r=>server.listen(0,"127.0.0.1",r));
 const origin=`http://127.0.0.1:${server.address().port}`;
 let executablePath=process.env.HYPERFRAMES_BROWSER_PATH;
 if(!executablePath){
  const {getInstalledBrowsers}=req("@puppeteer/browsers"),home=require("node:os").homedir();
  for(const cacheDir of [path.join(home,".cache/hyperframes/chrome"),path.join(home,".cache/puppeteer")]){
   executablePath=(await getInstalledBrowsers({cacheDir})).find(x=>x.browser==="chrome-headless-shell")?.executablePath;
   if(executablePath)break;
  }
 }
 const browser=await P.launch({executablePath,headless:true,args:["--no-sandbox","--disable-dev-shm-usage",
  "--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader","--disable-gpu-compositing","--force-color-profile=srgb"]});
 try{
  const pages=[];
  for(const source of ["source","stage-1/composition","stage-2/composition"]){
   const p=await browser.newPage();await p.setViewport({width:1280,height:720,deviceScaleFactor:1});
   await p.goto(origin+"/"+source+"/index.html");await p.evaluate(async()=>{await window.__unfoldScene3DReady;await document.fonts.ready;});
   pages.push(p);
  }
  const [original,first,second]=pages;
  for(const frame of [0,15,29,15,0]){
   const t=frame/30;
   await original.evaluate(t=>{window.__timelines.unfold.seek(t,false);window.__unfoldScene3DRenderAt(t);},t);
   const labels=await original.evaluate(()=>window.__unfoldLayerLabels.extract());
   const raw=await original.evaluate(()=>document.getElementById("scene3d").toDataURL().split(",")[1]);
   fs.writeFileSync(path.join(root,`raw-${frame}.png`),Buffer.from(raw,"base64"));
   for(const p of [first,second]){
    const state=await p.evaluate(async t=>{
     window.__timelines.unfold.seek(t,false);
     const work=[];window.dispatchEvent(new CustomEvent("hf-seek",{detail:{time:t,waitUntil:p=>work.push(p)}}));
     if(work.length!==1)throw Error("Missing synchronous completion registration");await Promise.all(work);
     return {frame:document.getElementById("scene3d").dataset.frame,labels:window.__unfoldLayerLabels.extract(),
       canvas:document.querySelectorAll("canvas").length};
    },t);
    assert.equal(state.frame,String(frame));assert.equal(state.canvas,0);assert.deepEqual(state.labels,labels);
   }
   // Matched label-only oracle: normal image/canvas hidden, original foreground unchanged.
   for(const p of [original,first])await p.evaluate(()=>document.getElementById("scene3d").style.visibility="hidden");
   await original.screenshot({path:path.join(root,`label-original-${frame}.png`)});
   await first.screenshot({path:path.join(root,`label-replay-${frame}.png`)});
   for(const p of [original,first])await p.evaluate(()=>document.getElementById("scene3d").style.visibility="");
   report.checks.push({frame,labels:labels.length});
  }
  // Two overlapping asynchronous requests must finish on the newer frame with
  // matching labels, not let an older decode publish late.
  report.overlap=await first.evaluate(async()=>{
   await Promise.all([window.__unfoldLayerSeek(29/30),window.__unfoldLayerSeek(1/30)]);
   const data=JSON.parse(document.getElementById("unfold-layer-frames").textContent);
   return {frame:document.getElementById("scene3d").dataset.frame,
     labels:window.__unfoldLayerLabels.extract(),expected:data.frames[1].labels};
  });
  assert.equal(report.overlap.frame,"1");assert.deepEqual(report.overlap.labels,report.overlap.expected);
  // New context prevents an already-decoded image from masking a simulated I/O failure.
  const context=await browser.createBrowserContext(),p=await context.newPage();
  await p.setViewport({width:1280,height:720,deviceScaleFactor:1});await p.setCacheEnabled(false);
  await p.setRequestInterception(true);
  p.on("request",q=>q.url().endsWith("frame_000011.png")?q.abort():q.continue());
  await p.goto(origin+"/stage-1/composition/index.html");await p.evaluate(async()=>await window.__unfoldScene3DReady);
  report.fault=await p.evaluate(async()=>{
   let rejected=false;try{await window.__unfoldLayerSeek(10/30);}catch{rejected=true;}
   return {rejected,hidden:[document.getElementById("scene3d"),...document.querySelectorAll(".label3d")]
     .every(el=>el.style.visibility==="hidden")};
  });
  assert(report.fault.rejected&&report.fault.hidden);await context.close();
  for(const p of pages)await p.close();
 }finally{await browser.close();}
})().catch(e=>{report.error=String(e.stack||e);process.exitCode=1;}).finally(()=>{
 server.close();fs.writeFileSync(path.join(root,"browser.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
});