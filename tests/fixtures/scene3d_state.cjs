// Unit probes of the actual runtime with explicit test doubles, not WebGL acceptance.
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const source = fs.readFileSync(process.argv[2], "utf8");
const context = vm.createContext({
  window: {}, document: {readyState: "loading", addEventListener() {}},
});
vm.runInContext(source.replace(/\}\)\(\);\s*$/,
  "globalThis.probes={spriteBatch}; dotTexture=()=>null;})();"), context);

// Compose emits an oblique transform with nonzero off-diagonal coefficients,
// as Babylon does. The test is about clearing buffers, not matrix correctness.
const B = {
  MeshBuilder: {CreatePlane() {
    return {buffers: {}, thinInstanceSetBuffer(name, data) {this.buffers[name] = data;}};
  }},
  StandardMaterial: class {},
  Color3: class {},
  Engine: {},
  Quaternion: {Identity() {return {};}},
  Vector3: class {set(...xyz) {this.xyz = xyz;}},
  Matrix: {
    Identity() {
      return {decompose() {}, copyToArray(out, offset) {
        out.set([0.7, 0.1, 0.6, 0, 0.2, 0.9, 0.1, 0, -0.6, 0.2, 0.7, 0, 1, 2, 3, 1], offset);
      }};
    },
    ComposeToRef() {},
  },
};
function batch() {return context.probes.spriteBatch(B, {}, "fixture", 2, 0.2, 1, false);}
const camera = {getViewMatrix() {return {invertToRef() {}};}};
const fresh = batch(), reused = batch();
fresh.hide(0);
reused.orient(camera);
reused.set(0, {}, 1, [1, 0.5, 0.2]);
reused.set(1, {}, 1, [0.3, 0.4, 0.5]);
const neighbour = Array.from(reused.mesh.buffers.matrix.slice(16));
reused.hide(0);
assert.deepEqual(Array.from(reused.mesh.buffers.matrix.slice(0, 16)),
  Array.from(fresh.mesh.buffers.matrix.slice(0, 16)));
assert.deepEqual(Array.from(reused.mesh.buffers.color.slice(0, 4)), [0, 0, 0, 0]);
assert.deepEqual(Array.from(reused.mesh.buffers.matrix.slice(16)), neighbour);
reused.set(0, {}, 1, [1, 0.5, 0.2]);
assert.equal(reused.mesh.buffers.color[3], 1); // reappearance still works
reused.hide(0);
assert.deepEqual(Array.from(reused.mesh.buffers.matrix.slice(0, 16)),
  Array.from(fresh.mesh.buffers.matrix.slice(0, 16)));

// Execute renderAt verbatim. ProjectToRef records the transform the runtime supplied;
// the independent oracle requires this time's camera, never the prior render's.
const renderText = source.slice(source.indexOf("    function renderAt(t) {"),
  source.indexOf("    // HyperFrames"));
function projectSequence(times) {
  const ctx = vm.createContext({});
  vm.runInContext(`
    const W=1920,H=1080,DURATION=3;
    const clamp=(x,a,b)=>Math.min(b,Math.max(a,x));
    const camera={target:{set(){}},getViewMatrix(){}};
    let transform={alpha:999,fov:999};
    const scene={
      updateTransformMatrix(){transform={alpha:camera.alpha,fov:camera.fov};},
      getTransformMatrix(){return transform;},
      render(){this.updateTransformMatrix();}
    };
    const B={Vector3:{ProjectToRef(pos,world,tm,vp,out){
      out.x=tm.alpha*W;out.y=tm.fov*H;
    }}}, M={IdentityReadOnly:{}}, vp={},scr={};
    const EASE={'power2.out':p=>1-(1-p)*(1-p)};
    const momentWeight=(m,t)=>(t-m.at)/m.duration;
    const S={moments:[{kind:'shockwave',at:0,duration:3,strength:1,node:'n'}]};
    const nodes=[],screens=[],videoSurfaces=[],links=[],effects=[],labels=[];
    const byId=new Map([['n',{mesh:{position:{}}}]]);
    const pipe={depthOfFieldEnabled:false};
    const moments={set(s){globalThis.shocks=s;}};
    const cameraAt=t=>[0,0,0,-90+45*t,25,16,40+10*t];
    let lastT=-1;
    ${renderText}
    ${times.map(t => `renderAt(${t});`).join("\n")}
  `, ctx);
  return JSON.parse(JSON.stringify(ctx.shocks));
}
const direct = projectSequence([1]);
assert.deepEqual(direct, projectSequence([0, 1]));
assert.deepEqual(direct, projectSequence([2, 1]));
assert.deepEqual(direct, projectSequence([0, 0.75, 1.5, 2.25, 3, 0, 1]));
assert(Math.abs(direct[0][0] - -Math.PI / 4) < 1e-12);
assert(Math.abs(direct[0][1] - (1 - 50 * Math.PI / 180)) < 1e-12);
console.log("sprite and camera state regressions passed (test doubles; no WebGL)");