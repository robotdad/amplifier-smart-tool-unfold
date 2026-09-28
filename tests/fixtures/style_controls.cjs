// Real-runtime controls test. No shader/math/renderer substitution or paid calls.
const fs = require("node:fs"), path = require("node:path"), http = require("node:http");
const assert = require("node:assert/strict");
const {createRequire} = require("node:module");
const backend = path.resolve(process.argv[2]), root = path.resolve(process.argv[3]);
const req = createRequire(path.join(backend, "package.json")), puppeteer = req("puppeteer-core");
const report = {checks: 0, errors: [], snapshots: {}};
function check(value, message) {assert(value, message); report.checks++;}
function same(a,b,message) {assert.deepEqual(a,b,message);report.checks++;}
const server = http.createServer((request,response) => {
  const file = path.resolve(root, "." + new URL(request.url,"http://localhost").pathname);
  if (!file.startsWith(root + path.sep)) return response.writeHead(403).end();
  try {response.setHeader("Content-Type",file.endsWith(".html")?"text/html":file.endsWith(".js")?"text/javascript":"application/octet-stream");
    response.end(fs.readFileSync(file));} catch {response.writeHead(404).end();}
});
(async () => {
  await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
  let executablePath = process.env.HYPERFRAMES_BROWSER_PATH;
  if (!executablePath) {
    const {getInstalledBrowsers} = req("@puppeteer/browsers"), home=require("node:os").homedir();
    for (const cacheDir of [path.join(home,".cache/hyperframes/chrome"),path.join(home,".cache/puppeteer")]) {
      executablePath=(await getInstalledBrowsers({cacheDir})).find(b=>b.browser==="chrome-headless-shell")?.executablePath;
      if(executablePath)break;
    }
  }
  const browser=await puppeteer.launch({executablePath,headless:true,
    args:["--no-sandbox","--disable-dev-shm-usage","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"],
    protocolTimeout:120000});
  try {
    async function open(name) {
      const page=await browser.newPage();await page.setViewport({width:1280,height:720,deviceScaleFactor:1});
      page.on("pageerror",e=>report.errors.push(String(e)));
      await page.goto(`http://127.0.0.1:${server.address().port}/${name}/index.html`);
      await page.evaluate(async()=>await window.__unfoldScene3DReady);
      return page;
    }
    async function snap(page,t) {
      return page.evaluate(t=>{
        window.dispatchEvent(new CustomEvent("hf-seek",{detail:{time:t}}));
        const B=window.BABYLON,s=B.EngineStore.LastCreatedScene;
        const pipe=s.postProcessRenderPipelineManager._renderPipelines.pipe;
        const state={babylon:B.Engine.Version,
          bloom:pipe.bloomEnabled,bloomWeight:pipe.bloomWeight,
          glow:s.effectLayers.filter(l=>l.name==="glow").map(l=>l.intensity),
          dof:pipe.depthOfFieldEnabled,grain:pipe.grainEnabled,
          floor:!!s.getMeshByName("floor"),
          grid:!!s.textures.find(tex=>tex.name==="grid"),
          floorEmissive:!!s.getMaterialByName("floorm")?.emissiveTexture,
          nodes:{},screens:{},png:document.getElementById("scene3d").toDataURL().split(",")[1]};
        for(const id of ["still","spin","late"]) {
          const m=s.getMeshByName("n_"+id),r=s.getMeshByName("r_"+id);
          if(m)state.nodes[id]={position:m.position.asArray(),scale:m.scaling.asArray(),
            rotation:m.rotation.asArray(),albedo:m.material.albedoColor.asArray(),
            ring:r?{rotation:r.rotation.asArray(),scale:r.scaling.asArray()}:null};
        }
        for(const id of ["screen","late_screen"]) {
          const p=s.getTransformNodeByName("sp_"+id);if(p)state.screens[id]=p.scaling.asArray();
        }
        return state;
      },t);
    }
    const states={};
    for(const name of ["legacy","omitted","explicit-legacy","bloom-off","glow-off","adjusted","no-floor","space","cinematic-off","plain"]) {
      const page=await open(name);
      states[name]=[];
      for(const t of [0,0.4,1,1.2,0]) {
        const state=await snap(page,t);
        fs.writeFileSync(path.join(root,name+"-frame"+t+".png"),Buffer.from(state.png,"base64"));
        // Keep pixel evidence but don't claim universal rerender repeatability.
        delete state.png; states[name].push(state);
      }
      await page.close();
    }
    report.babylon=states.plain[0].babylon;
    same(states.legacy,states.omitted,"missing fields preserve normalized legacy state");
    same(states.legacy,states["explicit-legacy"],"explicit legacy choices preserve state");
    check(states.legacy[0].bloom && states.legacy[0].glow[0]===0.75,"legacy glow/bloom");
    check(states.legacy[0].grid,"legacy grid present");
    check(states.legacy[0].nodes.still.scale[0]===0.0001,"legacy frame-zero entrance");
    same(states.legacy[0].screens.screen,[0.0001,0.0001,0.0001],"legacy screen scale-in retained");
    check(states.legacy[1].nodes.still.position[1]!==states.legacy[2].nodes.still.position[1],"legacy bob retained");
    check(!states["bloom-off"][0].bloom && states["bloom-off"][0].glow[0]===0.75,"bloom-only subtraction");
    check(states["glow-off"][0].bloom && !states["glow-off"][0].glow.length,"glow-only subtraction");
    check(states.adjusted[0].bloomWeight===0.2 && states.adjusted[0].glow[0]===0.4,"nonzero numeric overrides");
    check(!states["no-floor"][0].floor,"floor false still wins");
    check(!states.space[0].floor,"grid control does not add a space floor");
    const cinematic=states["cinematic-off"][0];
    check(!cinematic.bloom && !cinematic.glow.length,"cinematic halo overrides");
    check(cinematic.dof && cinematic.grain,"cinematic non-halo treatment preserved");
    for(const name of ["bloom-off","glow-off","plain"]) {
      same(states[name][0].dof,states.legacy[0].dof,"unrequested DOF unchanged");
      same(states[name][0].grain,states.legacy[0].grain,"unrequested grain unchanged");
    }
    const p=states.plain;
    check(!p[0].bloom && !p[0].glow.length,"both halo passes absent");
    check(p[0].floor && !p[0].grid && !p[0].floorEmissive,"plain floor without grid");
    same(p[0].nodes.still.scale,[1,1,1],"node full-size frame0");
    same(p[0].screens.screen,[1,1,1],"video full-size frame0");
    same(p[0].nodes.late.scale,[0,0,0],"node hidden before appearance");
    same(p[0].screens.late_screen,[0,0,0],"screen hidden before appearance");
    same(p[2].nodes.late.scale,[1,1,1],"node visible at exact appearance");
    same(p[2].screens.late_screen,[1,1,1],"screen visible at exact appearance");
    same(p[0].nodes.still,p[1].nodes.still,"no bob/tumble/ring motion");
    same(p[0].nodes.still,p[2].nodes.still,"stationary over interval");
    same(p[0],p[4],"backward seek resets selected control state");
    same(p[0].nodes.spin.position,p[3].nodes.spin.position,"explicit spin without bob");
    check(Math.abs(p[3].nodes.spin.rotation[1]-1.2*Math.PI/4)<1e-12,"explicit spin preserved");
    check(p[0].nodes.still.albedo[0]>p[0].nodes.still.albedo[1]*5,"red material tint");
    check(p[0].nodes.spin.albedo[2]>p[0].nodes.spin.albedo[0]*5,"gold preset accepts explicit blue tint");
    check(!report.errors.length,"no browser errors");
    report.snapshots=states;
  } finally {await browser.close();}
})().catch(e=>{report.errors.push(String(e.stack||e));process.exitCode=1;}).finally(()=>{
  server.close();fs.writeFileSync(path.join(root,"browser-report.json"),JSON.stringify(report,null,2));
  console.log(JSON.stringify({checks:report.checks,errors:report.errors,babylon:report.babylon}));
});