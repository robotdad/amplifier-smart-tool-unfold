// Owned straight-alpha acquisition from validated generated source only.
// Raw bytes use a bounded binary stdout protocol; filesystem writes stay in Python.
const fs=require("node:fs"),path=require("node:path"),os=require("node:os"),http=require("node:http");
const {createRequire}=require("node:module"),{once}=require("node:events"),readline=require("node:readline");
const [backend,source,w,h,n,milliseconds]=process.argv.slice(2);
const width=Number(w),height=Number(h),count=Number(n),budget=Number(milliseconds);
let browser,server,closed=false,timer;
async function close(){
  if(closed)return;closed=true;
  if(browser)await browser.close();
  if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}
}
process.on("SIGTERM",()=>{close().finally(()=>process.exit(1));});
async function write(bytes){if(!process.stdout.write(bytes))await once(process.stdout,"drain");}
async function packet(value,raw){
  const body=Buffer.from(JSON.stringify(value));if(body.length>65536)throw Error("Packet size");
  const head=Buffer.alloc(4);head.writeUInt32BE(body.length);
  await write(head);await write(body);if(raw)await write(raw);
}
(async()=>{
  if(![[1280,720],[1920,1080]].some(([a,b])=>a===width&&b===height)||
    !Number.isInteger(count)||count<1||count>1800||count*width*height>512*1024*1024||
    !Number.isFinite(budget)||budget<1||budget>5000000)throw Error("Acquisition bounds");
  timer=setTimeout(()=>{close().finally(()=>process.exit(1));},budget);
  const req=createRequire(path.join(path.resolve(backend),"package.json"));
  if(req("hyperframes/package.json").version!=="0.8.33"||req("babylonjs/package.json").version!=="9.28.0")
    throw Error("Pinned backend required");
  const P=req("puppeteer-core"),{getInstalledBrowsers}=req("@puppeteer/browsers");
  let executablePath=process.env.HYPERFRAMES_BROWSER_PATH;
  if(!executablePath)for(const cacheDir of [path.join(os.homedir(),".cache/hyperframes/chrome"),path.join(os.homedir(),".cache/puppeteer")]){
    executablePath=(await getInstalledBrowsers({cacheDir})).find(b=>b.browser==="chrome-headless-shell")?.executablePath;
    if(executablePath)break;
  }
  if(!executablePath)throw Error("Pinned renderer browser unavailable");
  const root=fs.realpathSync(source),allowed=new Map();let size=0;
  function walk(dir){
    for(const name of fs.readdirSync(dir)){
      const file=path.join(dir,name),stat=fs.lstatSync(file);
      if(stat.isSymbolicLink())throw Error("Symlink in acquisition source");
      if(stat.isDirectory()){walk(file);continue;}
      if(!stat.isFile()||stat.size>256*1024*1024)throw Error("Invalid source file");
      size+=stat.size;if(size>1024*1024*1024||allowed.size>=1024)throw Error("Source file budget");
      allowed.set(path.relative(root,file).split(path.sep).join("/"),{file,size:stat.size});
    }
  }
  walk(root);
  const token=require("node:crypto").randomBytes(24).toString("hex");
  server=http.createServer((q,r)=>{
    let key;try{key=decodeURIComponent(new URL(q.url,"http://localhost").pathname);}catch{return r.writeHead(400).end();}
    const prefix="/"+token+"/";if(!key.startsWith(prefix)||!["GET","HEAD"].includes(q.method))return r.writeHead(403).end();
    const entry=allowed.get(key.slice(prefix.length));if(!entry)return r.writeHead(404).end();
    let fd;try{
      fd=fs.openSync(entry.file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
      const st=fs.fstatSync(fd);if(!st.isFile()||st.size!==entry.size)throw Error("Source changed");
      const ext=path.extname(entry.file);
      r.setHeader("Content-Type",ext===".html"?"text/html":ext===".js"?"text/javascript":ext===".png"?"image/png":"application/octet-stream");
      r.setHeader("Content-Length",st.size);
      if(q.method==="HEAD"){fs.closeSync(fd);return r.end();}
      fs.createReadStream(entry.file,{fd,autoClose:true}).on("error",()=>r.destroy()).pipe(r);
    }catch{if(fd!==undefined)fs.closeSync(fd);r.writeHead(403).end();}
  });
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  const origin=`http://127.0.0.1:${server.address().port}/${token}/`;
  browser=await P.launch({executablePath,headless:true,args:["--no-sandbox","--disable-dev-shm-usage",
    "--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader","--disable-gpu-compositing",
    "--force-color-profile=srgb"],timeout:Math.min(budget,30000)});
  const page=await browser.newPage();await page.setViewport({width,height,deviceScaleFactor:1});
  await page.setRequestInterception(true);
  page.on("request",q=>(q.url().startsWith(origin)||q.url().startsWith("data:")||q.url().startsWith("blob:"))?q.continue():q.abort());
  await page.goto(origin+"index.html",{waitUntil:"load",timeout:Math.min(budget,30000)});
  await page.evaluate(async()=>{await window.__unfoldScene3DReady;await document.fonts.ready;});
  const state=await page.evaluate(()=>{
    const g=BABYLON.EngineStore.LastCreatedScene.getEngine()._gl,e=g.getExtension("WEBGL_debug_renderer_info");
    return {babylon:BABYLON.Engine.Version,renderer:g.getParameter(e.UNMASKED_RENDERER_WEBGL),
      premultipliedAlpha:g.getContextAttributes().premultipliedAlpha};
  });
  await packet({kind:"identity",browser:await browser.version(),...state});
  const rl=readline.createInterface({input:process.stdin});
  const iterator=rl[Symbol.asyncIterator]();
  for(let index=0;index<count;index++){
    const frame=await page.evaluate(({index,width,height})=>{
      window.__timelines.unfold.seek(index/30,false);
      window.__unfoldScene3DRenderAt(index/30);
      const canvas=document.getElementById("scene3d"),g=BABYLON.EngineStore.LastCreatedScene.getEngine()._gl;
      if(canvas.width!==width||canvas.height!==height||g.getContextAttributes().premultipliedAlpha)throw Error("Framebuffer mismatch");
      const old=g.getParameter(g.FRAMEBUFFER_BINDING);g.bindFramebuffer(g.FRAMEBUFFER,null);
      const bytes=new Uint8Array(width*height*4);g.readPixels(0,0,width,height,g.RGBA,g.UNSIGNED_BYTE,bytes);
      const error=g.getError();g.bindFramebuffer(g.FRAMEBUFFER,old);if(error!==g.NO_ERROR)throw Error("Readback failed");
      const parts=[];for(let i=0;i<bytes.length;i+=16384)parts.push(String.fromCharCode(...bytes.subarray(i,i+16384)));
      return {rgba:btoa(parts.join("")),labels:window.__unfoldLayerLabels.extract()};
    },{index,width,height});
    const raw=Buffer.from(frame.rgba,"base64");if(raw.length!==width*height*4)throw Error("Invalid raw byte count");
    await packet({kind:"frame",index,width,height,labels:frame.labels},raw);
    if((await iterator.next()).done)throw Error("Owner acknowledgement missing");
  }
  rl.close();await close();await packet({kind:"done",count});
})().catch(async error=>{console.error(String(error).slice(0,2000));await close();process.exitCode=1;})
 .finally(()=>{clearTimeout(timer);});