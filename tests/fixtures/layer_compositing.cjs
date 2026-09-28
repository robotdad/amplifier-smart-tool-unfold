// Actual browser pixels, no replacements of Babylon, post processing or alpha.
const fs=require("node:fs"),path=require("node:path"),http=require("node:http");
const {createRequire}=require("node:module");
const req=createRequire(path.join(path.resolve(process.argv[2]),"package.json"));
const P=req("puppeteer-core"),root=path.resolve(process.argv[3]),report={cases:{},errors:[]};
const server=http.createServer((request,response)=>{
 const file=path.resolve(root,"."+new URL(request.url,"http://localhost").pathname);
 if(!file.startsWith(root+path.sep))return response.writeHead(403).end();
 try{response.setHeader("Content-Type",file.endsWith(".html")?"text/html":file.endsWith(".js")?"text/javascript":"application/octet-stream");response.end(fs.readFileSync(file));}
 catch{response.writeHead(404).end();}
});
(async()=>{
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 let executablePath=process.env.HYPERFRAMES_BROWSER_PATH;
 if(!executablePath){
  const {getInstalledBrowsers}=req("@puppeteer/browsers"),home=require("node:os").homedir();
  for(const cacheDir of [path.join(home,".cache/hyperframes/chrome"),path.join(home,".cache/puppeteer")]){
   executablePath=(await getInstalledBrowsers({cacheDir})).find(b=>b.browser==="chrome-headless-shell")?.executablePath;
   if(executablePath)break;
  }
 }
 const browser=await P.launch({executablePath,headless:true,
 args:["--no-sandbox","--disable-dev-shm-usage","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader","--disable-gpu-compositing"],
 protocolTimeout:120000});
 try{
  for(const name of ["baseline","opaque","environment","clear-clean","clear-cinematic","clear-neon","clear-dreamy","clear-noir","clear-space"]){
   const page=await browser.newPage();await page.setViewport({width:1280,height:720,deviceScaleFactor:1});
   page.on("pageerror",e=>report.errors.push({name,error:String(e)}));
   await page.goto(`http://127.0.0.1:${server.address().port}/${name}/index.html`);
   await page.evaluate(async()=>await window.__unfoldScene3DReady);
   const state=await page.evaluate(()=>{
    window.__timelines.unfold.seek(.5,false);
    if(window.__unfoldScene3DRenderAt)window.__unfoldScene3DRenderAt(.5);
    const s=window.BABYLON?.EngineStore.LastCreatedScene;
    if(!s)return {};
    return {babylon:BABYLON.Engine.Version,clear:s.clearColor.asArray(),
      environment_ready:s.environmentTexture?.isReady(),floor:!!s.getMeshByName("floor"),
      stars:!!s.getMeshByName("stars"),sky:!!s.meshes.find(m=>m.name==="hdrSkyBox"),
      context:s.getEngine()._gl.getContextAttributes(),
      png:document.getElementById("scene3d").toDataURL().split(",")[1]};
   });
   if(state.png){fs.writeFileSync(path.join(root,name+"-canvas.png"),Buffer.from(state.png,"base64"));delete state.png;}
   report.cases[name]=state;
   await page.screenshot({path:path.join(root,name+".png")});await page.close();
  }
 }finally{await browser.close();}
})().catch(e=>{report.errors.push({error:String(e.stack||e)});process.exitCode=1;}).finally(()=>{
 server.close();fs.writeFileSync(path.join(root,"browser-report.json"),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
});