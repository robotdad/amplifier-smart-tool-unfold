/* Unfold scene3d runtime (Babylon.js 9.28).
 *
 * Reads the validated scene description from #unfold-scene3d (data only; no model-authored code)
 * and draws a full-frame 3D layer into #scene3d beneath Unfold's 2D elements.
 *
 * Determinism contract (HyperFrames renders frames in parallel, independent browser workers):
 *   - every captured frame is a pure function of t: renderAt(t) never reads a wall clock;
 *   - randomness comes from a seeded PRNG keyed by the scene seed and element id;
 *   - stream/orbit/burst/field particles are closed-form functions of t;
 *   - only `flock` integrates state; it steps at a fixed 1/60 s from its own start time and replays
 *     from 0.5 s checkpoints on backward seeks, so any worker reaches identical state;
 *   - the timeline is only registered after every shader/effect is compiled (readiness gate).
 */
(() => {
  "use strict";
  let resolveReady, rejectReady;
  window.__unfoldScene3DReady = new Promise((res, rej) => { resolveReady = res; rejectReady = rej; });
  const fail = (err) => {
    document.documentElement.dataset.scene3dError = String((err && err.stack) || err);
    rejectReady(err);
  };
  const start = () => { try { build(); } catch (err) { fail(err); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  // ------------------------------------------------------------------ helpers
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const frac = (x) => x - Math.floor(x);
  const EASE = {
    "none": (p) => p,
    "power2.out": (p) => 1 - (1 - p) * (1 - p),
    "power3.out": (p) => 1 - Math.pow(1 - p, 3),
    "power2.inOut": (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2),
  };
  const backOut = (p) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
  function hashString(s) { let h = 2166136261 >>> 0; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function mulberry32(a) { return function () { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

  // Legacy role palette; a validated optional node colour can override its material tint.
  const ROLE = {
    request: [0.30, 0.62, 1.00], response: [0.34, 1.00, 0.62], agent: [0.72, 0.52, 1.00],
    tool: [1.00, 0.56, 0.20], data: [0.22, 0.95, 0.95], error: [1.00, 0.24, 0.30],
    cache: [1.00, 0.84, 0.28], neutral: [0.82, 0.86, 0.94],
  };

  // Environment presets: sky gradient, studio light panels (HDR), fog, floor, label ink.
  const ENV = {
    studio_dark: { top: [0.015, 0.02, 0.035], horizon: [0.07, 0.09, 0.14], bottom: [0.01, 0.012, 0.02],
      panels: [[-0.55, 0.55, -0.62, 0.34, 7.0, [1, 0.97, 0.92]], [0.7, 0.35, 0.6, 0.22, 3.2, [0.7, 0.8, 1.0]], [0.0, 0.95, 0.0, 0.3, 2.2, [1, 1, 1]]],
      fog: [0.02, 0.026, 0.045], fogDensity: 0.016, floor: [0.035, 0.04, 0.055], grid: "#2a3d62", ink: "light", exposure: 1.35 },
    deep_space: { top: [0.004, 0.004, 0.012], horizon: [0.05, 0.02, 0.09], bottom: [0.002, 0.002, 0.006],
      panels: [[-0.6, 0.3, -0.7, 0.18, 9.0, [0.8, 0.6, 1.0]], [0.8, 0.1, 0.5, 0.25, 2.5, [0.3, 0.6, 1.0]]],
      fog: [0.01, 0.006, 0.02], fogDensity: 0.01, floor: null, grid: null, ink: "light", exposure: 1.2, stars: true },
    dusk: { top: [0.05, 0.04, 0.16], horizon: [0.95, 0.42, 0.22], bottom: [0.06, 0.03, 0.05],
      panels: [[0.0, 0.08, -1.0, 0.35, 6.0, [1.0, 0.6, 0.35]], [-0.8, 0.5, 0.3, 0.25, 1.5, [0.5, 0.5, 1.0]]],
      fog: [0.2, 0.1, 0.12], fogDensity: 0.016, floor: [0.08, 0.05, 0.06], grid: "#5a3346", ink: "light", exposure: 1.0 },
    lab_white: { top: [0.85, 0.88, 0.93], horizon: [0.95, 0.96, 0.98], bottom: [0.55, 0.57, 0.6],
      panels: [[-0.5, 0.7, -0.5, 0.4, 5.0, [1, 1, 1]], [0.6, 0.5, 0.6, 0.3, 3.0, [1, 1, 1]]],
      fog: [0.88, 0.9, 0.94], fogDensity: 0.012, floor: [0.8, 0.82, 0.86], grid: "#b8c2d4", ink: "dark", exposure: 0.9 },
    neon_grid: { top: [0.008, 0.004, 0.02], horizon: [0.45, 0.05, 0.4], bottom: [0.004, 0.002, 0.01],
      panels: [[-0.9, 0.12, -0.4, 0.2, 5.0, [1.0, 0.2, 0.8]], [0.9, 0.12, -0.4, 0.2, 5.0, [0.2, 0.9, 1.0]]],
      fog: [0.03, 0.005, 0.04], fogDensity: 0.02, floor: [0.01, 0.005, 0.02], grid: "#ff2fd0", gridGlow: true, ink: "light", exposure: 1.1 },
  };

  // Post presets: whole-frame passes. Only seek-safe effects (no TAA / motion blur / temporal AO).
  const POST = {
    clean:     { bloom: [0.8, 0.35, 64], dof: null, ca: 0, grain: 0, vignette: 1.2, contrast: 1.1, saturation: 0, sharpen: 0.15 },
    cinematic: { bloom: [0.6, 0.6, 96], dof: 3.2, ca: 10, grain: 7, vignette: 2.4, contrast: 1.22, saturation: 0, sharpen: 0.2 },
    neon:      { bloom: [0.35, 1.0, 128], dof: null, ca: 22, grain: 5, vignette: 2.8, contrast: 1.3, saturation: 25, sharpen: 0.1 },
    dreamy:    { bloom: [0.3, 0.9, 128], dof: 1.8, ca: 6, grain: 4, vignette: 2.0, contrast: 1.0, saturation: -10, sharpen: 0 },
    noir:      { bloom: [0.7, 0.4, 64], dof: 2.8, ca: 0, grain: 14, vignette: 3.2, contrast: 1.45, saturation: -100, sharpen: 0.25 },
  };

  const CAPS = { stream: 2500, flock: 400, orbit: 1500, burst: 600, field: 3000 };

  // ------------------------------------------------------------------ build
  function build() {
    const B = window.BABYLON;
    if (!B) throw new Error("Babylon.js is not loaded");
    const dataEl = document.getElementById("unfold-scene3d");
    const canvas = document.getElementById("scene3d");
    if (!dataEl || !canvas) throw new Error("scene3d data or canvas missing");
    const DATA = JSON.parse(dataEl.textContent);
    const S = DATA.scene3d, W = DATA.width, H = DATA.height, DURATION = DATA.duration;
    const TRANSPARENT = !!DATA.transparent;
    const env = ENV[S.environment] || ENV.studio_dark;
    S.__lightInk = env.ink === "dark";
    const post = POST[S.post] || POST.cinematic;
    const overrides = S.post_overrides || {};
    const bloomWeight = overrides.bloom_weight ?? post.bloom[1];
    const glowIntensity = overrides.glow_intensity ?? 0.75;
    const glOk = typeof B.Engine.isSupported === "function" ? B.Engine.isSupported() : !!B.Engine.IsSupported;
    if (!glOk) throw new Error("WebGL is unavailable");

    const V = B.Vector3, M = B.Matrix, Q = B.Quaternion, C3 = B.Color3, C4 = B.Color4;
    const engine = new B.Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true, antialias: true, premultipliedAlpha: false }, false);
    engine.setHardwareScalingLevel(1);
    const caps = engine.getCaps();
    if (caps && caps.parallelShaderCompile) caps.parallelShaderCompile = undefined;
    const scene = new B.Scene(engine);
    scene.animationsEnabled = false;       // nothing may advance on its own clock
    scene.skipPointerMovePicking = true;
    scene.clearColor = TRANSPARENT ? new C4(0, 0, 0, 0) : new C4(env.bottom[0], env.bottom[1], env.bottom[2], 1);
    scene.fogMode = B.Scene.FOGMODE_EXP2; scene.fogDensity = env.fogDensity;
    scene.fogColor = new C3(env.fog[0], env.fog[1], env.fog[2]);

    // --- camera
    const camera = new B.ArcRotateCamera("cam", -Math.PI / 2, 1.1, 16, V.Zero(), scene);
    camera.minZ = 0.1; camera.maxZ = 600;

    // --- procedural HDR environment (analytic -> float cubemap + synchronous SH irradiance)
    const envTex = buildEnvironment(B, scene, env, true, 96);
    scene.environmentTexture = envTex;
    if (!TRANSPARENT) {
      // The visible backdrop must not show the studio light panels (they read as a grey smear);
      // lighting/reflections keep them via envTex.
      const bgTex = buildEnvironment(B, scene, env, false, 64);
      const sky = scene.createDefaultSkybox(bgTex, true, 1000, 0.05, false);
      if (sky) { sky.infiniteDistance = true; sky.material.fogEnabled = false; }
    }

    // --- lights (key casts contact shadows; environment does the rest)
    const hemi = new B.HemisphericLight("hemi", new V(0.1, 1, 0.1), scene); hemi.intensity = 0.15;
    const key = new B.DirectionalLight("key", new V(-0.45, -1, 0.55), scene); key.intensity = 2.2; key.position = new V(12, 24, -12);
    const shadow = new B.ShadowGenerator(1024, key);
    shadow.usePercentageCloserFiltering = true; shadow.filteringQuality = B.ShadowGenerator.QUALITY_MEDIUM;
    shadow.bias = 0.0015; shadow.normalBias = 0.02; shadow.darkness = 0.25;

    // --- video surfaces (screens and video nodes) share one decoded-atlas path
    const MEDIA = DATA.screens || {};
    const videoSurfaces = [];
    function videoMaterial(id) {
      const m = MEDIA[id];
      if (!m || !m.pages || !m.pages.length) throw new Error("scene3d video has no decoded frames: " + id);
      const pm = new B.StandardMaterial("svm_" + id, scene);
      pm.disableLighting = true; pm.fogEnabled = false;
      pm.diffuseColor = new C3(0, 0, 0); pm.specularColor = new C3(0, 0, 0);
      // pages load through <img> (img-src 'self'); the page forbids fetch/XHR (connect-src 'none')
      const images = m.pages.map((pg) => {
        const img = new Image();
        const loaded = new Promise((res, rej) => {
          img.onload = () => res(img);
          img.onerror = () => rej(new Error("scene3d video frames failed to load: " + pg.file));
        });
        img.src = pg.file;
        return loaded;
      });
      return { m, pm, images };
    }

    // --- stable node geometry
    const byId = new Map();
    const rngFor = (id) => mulberry32((hashString(id) ^ (S.seed >>> 0)) >>> 0);
    const nodes = S.nodes.map((n, i) => {
      const col = n.color ? B.Color3.FromHexString(n.color).toLinearSpace().asArray() : (ROLE[n.role] || ROLE.neutral);
      const mesh = makeShape(B, scene, n.shape, n.size, "n_" + n.id);
      mesh.material = makeMaterial(B, scene, n.material, col, "m_" + n.id);
      // Fixed-colour material presets retain their physical parameters, not their old hue.
      if (n.color && ["chrome", "gold", "obsidian"].includes(n.material))
        mesh.material.albedoColor = new C3(...col);
      let video = null, vmesh = null, facing = false;
      if (n.media) {   // a library video on the node's surface, following the node as it moves
        const surface = n.media.surface === "auto" ? MEDIA_AUTO[n.shape] : n.media.surface;
        facing = surface === "facing_camera";
        video = videoMaterial(n.id);
        if (surface === "one_face" || (facing && n.shape === "cube")) {
          // the clip sits on one face (the front of a box, the top of a platform); the node keeps its material
          const w = n.shape === "platform" ? n.size * 1.7 : n.size * 1.1 * 0.86;
          vmesh = B.MeshBuilder.CreatePlane("vf_" + n.id, { width: w, height: w * video.m.aspect }, scene);
          vmesh.parent = mesh; vmesh.material = video.pm;
          if (n.shape === "platform") { vmesh.rotation.x = Math.PI / 2; vmesh.position.y = n.size * 0.09 + 0.012; }   // normal +Y: faces up
          else vmesh.position.z = -(n.size * 0.55) - 0.012;   // planes face -Z: the node's front
        } else {
          if (n.shape === "cube") faceUVs(B, mesh);   // every face shows the whole frame, upright
          // Babylon's built-in UVs run differently per shape (measured with a quadrant clip at spin 0):
          // correct them so the clip reads upright and unmirrored from the front, like a screen.
          else if (VIDEO_UV_FLIP[n.shape]) flipUVs(B, mesh, ...VIDEO_UV_FLIP[n.shape]);
          mesh.material = video.pm; vmesh = mesh;
        }
      }
      shadow.addShadowCaster(mesh);
      const base = new V(n.position[0], n.position[1] + (n.shape === "platform" ? 0 : 0.0), n.position[2]);
      let ring = null;
      if (n.ring) {
        ring = B.MeshBuilder.CreateTorus("r_" + n.id, { diameter: n.size * 1.9, thickness: Math.max(0.018, n.size * 0.028), tessellation: 128 }, scene);
        const rm = new B.StandardMaterial("rm_" + n.id, scene); rm.disableLighting = true;
        rm.emissiveColor = new C3(col[0] * 1.6, col[1] * 1.6, col[2] * 1.6); ring.material = rm;
      }
      const rec = { n, i, mesh, ring, base, col, phase: rngFor(n.id)() * 6.283 };
      if (video) {
        Object.assign(rec, { id: n.id, m: video.m, pm: video.pm, images: video.images, pages: null,
          playFrom: video.m.play_from, rate: n.media.rate, loop: n.media.loop, vmesh, facing });
        videoSurfaces.push(rec);
      }
      byId.set(n.id, rec);
      return rec;
    });
    const centroid = nodes.reduce((a, r) => a.addInPlace(r.base), V.Zero()).scaleInPlace(1 / nodes.length);

    // --- video screens: frame atlases decoded at author time; the tile shown is a pure function of t.
    // No <video>, no VideoTexture: those play on a wall clock and would differ between render workers.
    const SCREENS = DATA.screens || {};
    const screens = (S.screens || []).map((sc) => {
      const m = SCREENS[sc.id];
      if (!m || !m.pages || !m.pages.length) throw new Error("scene3d screen has no decoded frames: " + sc.id);
      const col = ROLE[sc.role] || ROLE.neutral;
      const w = sc.width, h = sc.width * m.aspect;
      const base = new V(sc.position[0], sc.position[1], sc.position[2]);
      const pivot = new B.TransformNode("sp_" + sc.id, scene);
      pivot.position.copyFrom(base);
      pivot.rotation.set(sc.tilt * Math.PI / 180, sc.yaw * Math.PI / 180, 0);
      const pic = B.MeshBuilder.CreatePlane("sv_" + sc.id, { width: w, height: h }, scene);
      pic.parent = pivot;
      const pm = new B.StandardMaterial("svm_" + sc.id, scene);
      pm.disableLighting = true; pm.fogEnabled = false;
      pm.diffuseColor = new C3(0, 0, 0); pm.specularColor = new C3(0, 0, 0);
      // Atlas pages load through <img> (allowed by img-src 'self'); the page forbids fetch/XHR
      // (connect-src 'none'), so they are handed to Babylon as decoded images, never as URLs.
      const images = m.pages.map((pg) => {
        const img = new Image();
        const loaded = new Promise((res, rej) => {
          img.onload = () => res(img);
          img.onerror = () => rej(new Error("scene3d screen frames failed to load: " + pg.file));
        });
        img.src = pg.file;
        return loaded;
      });
      pic.material = pm;
      let frame = null;
      if (sc.frame === "bezel") {
        frame = B.MeshBuilder.CreateBox("sb_" + sc.id, { width: w + 0.18, height: h + 0.18, depth: 0.1 }, scene);
        frame.material = makeMaterial(B, scene, "obsidian", col, "sbm_" + sc.id);
        shadow.addShadowCaster(frame);
      } else if (sc.frame === "floating") {
        frame = B.MeshBuilder.CreatePlane("sb_" + sc.id, { width: w + 0.1, height: h + 0.1 }, scene);
        const fm = new B.StandardMaterial("sbm_" + sc.id, scene); fm.disableLighting = true;
        fm.emissiveColor = new C3(col[0] * 0.9, col[1] * 0.9, col[2] * 0.9); frame.material = fm;
      }
      if (frame) { frame.parent = pivot; frame.position.z = 0.06; }   // behind the picture (planes face -Z)
      const rec = { n: { id: sc.id, label: sc.label, appear_at: sc.appear_at, entrance: sc.entrance, size: h * 0.6 },
        sc, m, mesh: pivot, pic, frame, pm, images, pages: null, base, col, playFrom: m.play_from,
        id: sc.id, rate: sc.rate, loop: sc.loop };
      videoSurfaces.push(rec);
      byId.set(sc.id, rec);
      return rec;
    });

    // --- floor with a procedural grid; fog fades it to the horizon
    let floorY = Math.min(...nodes.map((r) => r.base.y - (r.n.shape === "platform" ? 0.1 : r.n.size * 0.75))) - 0.35;
    if (S.floor && env.floor && !TRANSPARENT) {
      const ground = B.MeshBuilder.CreateGround("floor", { width: 500, height: 500, subdivisions: 1 }, scene);
      ground.position.y = floorY;
      const gm = new B.PBRMaterial("floorm", scene);
      if (S.floor_grid === false) {
        gm.albedoColor = new C3(...env.floor);
        gm.metallic = 0.02; gm.roughness = 0.7; gm.environmentIntensity = 0.35;
      } else {
        const grid = new B.DynamicTexture("grid", { width: 1024, height: 1024 }, scene, true);
        const g = grid.getContext();
        g.fillStyle = "#" + env.floor.map((c) => Math.round(Math.pow(c, 1 / 2.2) * 255).toString(16).padStart(2, "0")).join("");
        g.fillRect(0, 0, 1024, 1024);
        g.strokeStyle = env.grid; g.globalAlpha = 0.55; g.lineWidth = 2;
        for (let x = 0; x <= 1024; x += 64) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 1024); g.stroke(); g.beginPath(); g.moveTo(0, x); g.lineTo(1024, x); g.stroke(); }
        grid.update(); grid.uScale = grid.vScale = 40; grid.anisotropicFilteringLevel = 8;
        gm.albedoTexture = grid; gm.metallic = 0.02; gm.roughness = 0.7; gm.environmentIntensity = 0.35;
        if (env.gridGlow) { gm.emissiveTexture = grid; gm.emissiveColor = new C3(0.9, 0.9, 0.9); }
      }
      ground.material = gm; ground.receiveShadows = true;
    }

    // --- deep space starfield (static, seeded)
    if (env.stars && !TRANSPARENT) {
      const rs = mulberry32(99173 ^ S.seed);
      const stars = spriteBatch(B, scene, "stars", 1400, 0.9, 1.0);
      for (let i = 0; i < 1400; i++) {
        const u = rs() * 2 - 1, a = rs() * Math.PI * 2, r = 260 + rs() * 60, s = Math.sqrt(1 - u * u);
        const b = 0.25 + Math.pow(rs(), 6) * 2.5;
        stars.set(i, new V(Math.cos(a) * s * r, Math.abs(u) * r * 0.9 + 5, Math.sin(a) * s * r), 0.5 + rs() * 1.6, [b * 0.8, b * 0.85, b]);
      }
      stars.flush(); stars.static = true;
    }

    // --- links: glowing arcs, drawn on by progress (tube rebuilt in place)
    const LINK_N = 64;
    const links = S.links.map((l) => {
      const a = byId.get(l.from_node), b = byId.get(l.to_node);
      const col = ROLE[l.role] || ROLE.neutral;
      const pts = arcPoints(B, a.base, b.base, l.lift, LINK_N);
      const path = pts.map((p) => p.clone());
      const tube = B.MeshBuilder.CreateTube("l_" + l.id, { path, radius: 0.028, tessellation: 12, updatable: true }, scene);
      const lm = new B.StandardMaterial("lm_" + l.id, scene); lm.disableLighting = true;
      lm.emissiveColor = new C3(col[0] * 1.3, col[1] * 1.3, col[2] * 1.3); lm.alpha = 0.9; tube.material = lm;
      return { l, tube, pts, path };
    });

    // --- effects
    const effects = S.effects.map((e) => makeEffect(B, scene, e, byId, rngFor, S));

    // --- post-processing: build once; afterwards only uniforms change (no shader rebuilds mid-render)
    if (glowIntensity > 0) {
      const glow = new B.GlowLayer("glow", scene, { mainTextureSamples: 1, blurKernelSize: 64 }); glow.intensity = glowIntensity;
      for (const fx of effects) for (const m of fx.meshes) glow.addExcludedMesh(m);
      // neon nodes already emit; the glow blur on top washes their face to white. Bloom supplies the halo.
      for (const r of nodes) if (r.n.material === "neon") glow.addExcludedMesh(r.mesh);
      // the picture shows true colour; its frame sits right behind it, so the glow blur from the
      // frame's faint role emissive would wash over the picture
      for (const r of screens) { glow.addExcludedMesh(r.pic); if (r.frame) glow.addExcludedMesh(r.frame); }
      for (const r of videoSurfaces) if (!r.sc) glow.addExcludedMesh(r.vmesh);   // video nodes: true colour
    }
    const pipe = new B.DefaultRenderingPipeline("pipe", true, scene, [camera]);
    pipe.samples = 4; pipe.fxaaEnabled = true;
    pipe.bloomEnabled = bloomWeight > 0;
    [pipe.bloomThreshold, pipe.bloomWeight, pipe.bloomKernel] = [post.bloom[0], bloomWeight, post.bloom[2]]; pipe.bloomScale = 0.5;
    pipe.imageProcessingEnabled = true;
    const ip = pipe.imageProcessing;
    ip.toneMappingEnabled = true; ip.toneMappingType = B.ImageProcessingConfiguration.TONEMAPPING_ACES;
    ip.exposure = env.exposure; ip.contrast = post.contrast;
    ip.vignetteEnabled = post.vignette > 0; ip.vignetteWeight = post.vignette; ip.vignetteStretch = 0.2;
    if (post.saturation) { ip.colorCurvesEnabled = true; const cc = new B.ColorCurves(); cc.globalSaturation = post.saturation; ip.colorCurves = cc; }
    const dofBase = post.dof;
    const anyFocusPull = S.moments.some((m) => m.kind === "focus_pull");
    pipe.depthOfFieldEnabled = !!(dofBase || anyFocusPull);
    if (pipe.depthOfFieldEnabled) { pipe.depthOfFieldBlurLevel = B.DepthOfFieldEffectBlurLevel.Low; pipe.depthOfField.focalLength = 50; pipe.depthOfField.fStop = dofBase || 22; }
    pipe.chromaticAberrationEnabled = post.ca > 0; if (post.ca) pipe.chromaticAberration.aberrationAmount = post.ca;
    pipe.grainEnabled = post.grain > 0; if (post.grain) { pipe.grain.intensity = post.grain; pipe.grain.animated = false; }
    pipe.sharpenEnabled = post.sharpen > 0; if (post.sharpen) pipe.sharpen.edgeAmount = post.sharpen;
    const moments = buildMoments(B, camera, S.moments);

    // --- DOM labels projected from 3D (text via textContent only)
    const root = document.getElementById("root") || document.body;
    const inkLight = env.ink === "light";
    const labels = [...nodes, ...screens].filter((r) => r.n.label).map((r) => {
      const el = document.createElement("div");
      el.className = "label3d"; el.textContent = r.n.label;
      const c = r.col.map((x) => Math.round(Math.pow(Math.min(1, x), 1 / 2.2) * 255));
      el.style.cssText = "position:absolute;left:0;top:0;z-index:1;pointer-events:none;white-space:nowrap;" +
        "font:600 22px/1 system-ui,sans-serif;letter-spacing:2.5px;text-transform:uppercase;padding:9px 16px 8px;border-radius:999px;" +
        (inkLight ? "color:#eef3ff;background:rgba(8,12,22,.55);" : "color:#101522;background:rgba(255,255,255,.7);") +
        `border:1.5px solid rgba(${c[0]},${c[1]},${c[2]},.85);box-shadow:0 0 18px rgba(${c[0]},${c[1]},${c[2]},.35);opacity:0;will-change:transform,opacity`;
      const canvasEl = document.getElementById("scene3d");
      root.insertBefore(el, canvasEl.nextSibling);
      return { r, el };
    });

    // ------------------------------------------------------------------ frame = f(t)
    const vp = new B.Viewport(0, 0, W, H), scr = new V(), tmpV = new V();
    let lastT = -1;

    function cameraAt(t) {
      const shots = S.camera;
      const val = (s) => {
        const tr = s.target ? byId.get(s.target).base : centroid;
        return [tr.x, tr.y, tr.z, s.azimuth, s.elevation, s.distance, s.fov];
      };
      // state after considering shots[0..k]; each shot eases from the state at its own start
      const evalUpTo = (k, time) => {
        if (k === 0) return val(shots[0]);
        const s = shots[k];
        if (time < s.at) return evalUpTo(k - 1, time);
        const from = evalUpTo(k - 1, s.at), to = val(s);
        const p = s.duration > 0 ? (EASE[s.ease] || EASE["power2.inOut"])(clamp((time - s.at) / s.duration, 0, 1)) : 1;
        return from.map((f, i) => lerp(f, to[i], p));
      };
      return evalUpTo(shots.length - 1, t);
    }

    function momentWeight(m, t) {
      const p = (t - m.at) / m.duration;
      return p < 0 || p > 1 ? -1 : p;
    }

    // the atlas tile for scene time t: a pure function of t, identical on every render worker
    function showFrame(r, t) {
      if (!r.pages) return;   // textures are created inside the readiness gate
      let f = Math.floor((t - r.playFrom) * r.rate * r.m.fps + 1e-6);
      if (f < 0) f = 0;
      f = r.loop ? f % r.m.frames : Math.min(f, r.m.frames - 1);
      let k = 0, local = f;
      while (k < r.m.pages.length - 1 && local >= r.m.pages[k].frames) { local -= r.m.pages[k].frames; k++; }
      const pg = r.m.pages[k], tex = r.pages[k];
      if (r.pm.emissiveTexture !== tex) r.pm.emissiveTexture = tex;
      tex.uOffset = (local % r.m.cols) * r.m.tile[0] / pg.width;
      tex.vOffset = 1 - (Math.floor(local / r.m.cols) + 1) * r.m.tile[1] / pg.height;
    }

    function renderAt(t) {
      t = clamp(t, 0, DURATION);
      if (t === lastT) return;
      lastT = t;

      // camera
      const c = cameraAt(t);
      camera.target.set(c[0], c[1], c[2]);
      camera.alpha = c[3] * Math.PI / 180;
      camera.beta = (90 - c[4]) * Math.PI / 180;
      camera.radius = c[5];
      camera.fov = c[6] * Math.PI / 180;
      camera.getViewMatrix(true);
      // Projection consumers (including shockwaves) must see this frame's camera.
      scene.updateTransformMatrix(true);

      // screens: ease in, then show the atlas tile for scene time t
      for (const r of screens) {
        const a = clamp((t - r.sc.appear_at) / 0.6, 0, 1);
        r.mesh.scaling.setAll(r.sc.entrance === "none" ? (t >= r.sc.appear_at ? 1 : 0) : Math.max(0.0001, a * a * (3 - 2 * a)));
        const bob = r.sc.frame === "floating" ? Math.sin(t * 0.8) * 0.05 : 0;
        r.mesh.position.set(r.base.x, r.base.y + bob, r.base.z);
        showFrame(r, t);
      }
      for (const r of videoSurfaces) if (!r.sc) showFrame(r, t);   // video nodes

      // nodes: pop-in with overshoot, gentle float, slow spin
      for (const r of nodes) {
        const a = backOut(clamp((t - r.n.appear_at) / 0.7, 0, 1));
        const s = r.n.entrance === "none" ? (t >= r.n.appear_at ? 1 : 0) : Math.max(0.0001, a);
        const idle = r.n.idle_motion !== "none";
        const bob = !idle || r.n.shape === "platform" ? 0 : Math.sin(t * 0.9 + r.phase) * 0.06 * r.n.size;
        r.mesh.position.set(r.base.x, r.base.y + bob, r.base.z);
        r.mesh.scaling.setAll(s);
        if (r.facing) {   // turn about the vertical axis so the clip side always faces the camera
          const dx = camera.position.x - r.mesh.position.x, dz = camera.position.z - r.mesh.position.z;
          r.mesh.rotation.set(0, Math.atan2(-dx, -dz), 0);
        } else if (r.n.spin != null) {   // explicit spin about the vertical axis, any shape
          r.mesh.rotation.set(0, t * r.n.spin * Math.PI / 180, 0);   // starts face-on: spin 0 = still, facing the default camera
        } else if (idle && (r.n.shape === "cube" || r.n.shape === "icosahedron" || r.n.shape === "torus")) {
          r.mesh.rotation.set(0.35 * Math.sin(t * 0.4 + r.phase), t * 0.35 + r.phase, 0.2 * Math.cos(t * 0.3 + r.phase));
        } else if (!idle) {
          r.mesh.rotation.set(0, 0, 0);
        }
        if (r.ring) {
          r.ring.position.copyFrom(r.mesh.position);
          r.ring.scaling.setAll(r.n.entrance === "none" ? s : Math.max(0.0001, smooth(r.n.appear_at + 0.2, r.n.appear_at + 0.9, t)));
          if (idle) r.ring.rotation.set(Math.PI / 2 + 0.35 * Math.sin(t * 0.7 + r.phase), t * 0.55 + r.phase, 0.25 * Math.cos(t * 0.5));
          else r.ring.rotation.set(Math.PI / 2, 0, 0);
        }
      }

      // links draw on along their arc
      for (const L of links) {
        const p = L.l.draw_duration > 0 ? EASE["power2.inOut"](clamp((t - L.l.appear_at) / L.l.draw_duration, 0, 1)) : (t >= L.l.appear_at ? 1 : 0);
        const f = p * (LINK_N - 1), k = Math.floor(f), w = f - k;
        const tip = k >= LINK_N - 1 ? L.pts[LINK_N - 1] : V.LerpToRef(L.pts[k], L.pts[k + 1], w, tmpV);
        if (p > 0) {
          for (let i = 0; i < LINK_N; i++) L.path[i].copyFrom(i <= k ? L.pts[i] : tip);
          B.MeshBuilder.CreateTube(L.tube.name, { path: L.path, radius: 0.028, instance: L.tube });
        }
        L.tube.scaling.setAll(p > 0 ? 1 : 0);   // hidden by scale, still drawn -> compiled from t=0
      }

      // effects
      for (const fx of effects) fx.update(t, camera);

      // moments: shockwave / flash / glitch / focus pulls
      const shocks = [];
      let flash = 0, glitch = 0, focusNode = null, focusW = 0;
      for (const m of S.moments) {
        const p = momentWeight(m, t);
        if (p < 0) continue;
        if (m.kind === "shockwave" && shocks.length < 4) {
          let u = 0.5, v = 0.5;
          if (m.node) { B.Vector3.ProjectToRef(byId.get(m.node).mesh.position, M.IdentityReadOnly, scene.getTransformMatrix(), vp, scr); u = scr.x / W; v = 1 - scr.y / H; }
          shocks.push([u, v, EASE["power2.out"](p) * 0.9, m.strength * (1 - p)]);
        } else if (m.kind === "flash") flash = Math.max(flash, m.strength * Math.pow(1 - p, 2.2));
        else if (m.kind === "glitch") glitch = Math.max(glitch, m.strength * Math.sin(p * Math.PI));
        else if (m.kind === "focus_pull") { focusNode = m.node ? byId.get(m.node) : null; focusW = Math.sin(p * Math.PI); }
      }
      moments.set(shocks, flash, glitch, Math.floor(t * 30));
      if (pipe.depthOfFieldEnabled) {
        const camPos = camera.position;
        const baseFocus = V.Distance(camPos, camera.target);
        const pullFocus = focusNode ? V.Distance(camPos, focusNode.mesh.position) : baseFocus;
        pipe.depthOfField.focusDistance = lerp(baseFocus, pullFocus, focusW) * 1000;
        pipe.depthOfField.fStop = lerp(dofBase || 22, 1.4, focusW);
      }

      // labels follow their nodes
      const tm = scene.getTransformMatrix();
      for (const L of labels) {
        const r = L.r;
        tmpV.set(r.mesh.position.x, r.mesh.position.y + r.n.size * 0.95 + 0.45, r.mesh.position.z);
        B.Vector3.ProjectToRef(tmpV, M.IdentityReadOnly, tm, vp, scr);
        const lw = L.el.offsetWidth, lh = L.el.offsetHeight, lx = scr.x - lw / 2, ly = scr.y - lh;
        const edge = Math.min(lx, W - (lx + lw), ly, H - scr.y);
        const entrance = r.n.entrance === "none" ? (t >= r.n.appear_at ? 1 : 0) : smooth(r.n.appear_at + 0.3, r.n.appear_at + 0.9, t);
        const vis = scr.z > 0 && scr.z < 1 ? entrance * Math.min(1, Math.max(0, edge / 40)) : 0;
        L.el.style.opacity = vis.toFixed(3);
        L.el.style.transform = `translate(${lx.toFixed(1)}px,${ly.toFixed(1)}px)`;
      }

      scene.render(true, true);
    }

    // HyperFrames' engine-agnostic seek hook (the same event its Three.js/TypeGPU adapters use).
    window.addEventListener("hf-seek", (e) => { renderAt(Number(e.detail && e.detail.time) || 0); });
    window.__unfoldScene3DRenderAt = renderAt;   // for previews/tests; capture relies on hf-seek

    // Readiness gate: include render targets (DOF depth, glow, shadow map, environment); warm-render
    // until every effect reports ready, then reset so HyperFrames' own t=0 seek produces frame 0.
    scene.whenReadyAsync(true).then(async () => {
      // every atlas page must be decoded and uploaded, not only the page bound at t=0;
      // a failed page rejects here, so the readiness gate never opens without its frames
      for (const r of videoSurfaces) {
        const imgs = await Promise.all(r.images);
        // drawn onto canvas-backed textures (as the floor grid is): no loader, no network access
        r.pages = imgs.map((img, k) => {
          const pg = r.m.pages[k];
          if (img.naturalWidth !== pg.width || img.naturalHeight !== pg.height)
            throw new Error("scene3d screen frames have unexpected size: " + pg.file);
          const tex = new B.DynamicTexture("svt_" + r.id + "_" + k, { width: pg.width, height: pg.height },
            scene, false, B.Texture.BILINEAR_SAMPLINGMODE);
          tex.getContext().drawImage(img, 0, 0);
          tex.update(true);
          tex.wrapU = tex.wrapV = B.Texture.CLAMP_ADDRESSMODE;
          tex.uScale = r.m.tile[0] / pg.width; tex.vScale = r.m.tile[1] / pg.height;
          return tex;
        });
        r.pm.emissiveTexture = r.pages[0];
      }
      await scene.whenReadyAsync(true);
      const probes = [0, 0.25, 0.5, 0.75, 1].map((f) => f * DURATION);
      for (let pass = 0; pass < 20; pass++) {
        for (const tp of probes) { lastT = -1; renderAt(tp); }
        await new Promise((r) => setTimeout(r, 30));
        if (pass >= 1 && scene.isReady(true)) break;
      }
      // warm renders advanced the flock simulation; checkpoints make that harmless (state is a
      // function of t), and renderAt(0) below rewinds to the t=0 checkpoint.
      lastT = -1; renderAt(0);
      await scene.whenReadyAsync(true);
      lastT = -1;
      document.documentElement.dataset.scene3dReady = "1";
      resolveReady();
    }).catch(fail);
  }

  // ------------------------------------------------------------------ environment
  function buildEnvironment(B, scene, env, withPanels, N) {
    const faces = [];
    const dir = (f, u, v) => {       // Babylon/GL cube face order +X,-X,+Y,-Y,+Z,-Z
      switch (f) {
        case 0: return [1, -v, -u]; case 1: return [-1, -v, u];
        case 2: return [u, 1, v];   case 3: return [u, -1, -v];
        case 4: return [u, -v, 1];  default: return [-u, -v, -1];
      }
    };
    const panels = (withPanels ? env.panels : []).map(([x, y, z, size, power, col]) => { const l = Math.hypot(x, y, z); return { d: [x / l, y / l, z / l], cosR: Math.cos(size), power, col }; });
    const radiance = (d, out) => {
      const y = d[1];
      let r, g, b;
      if (y >= 0) { const k = Math.pow(1 - y, 4); r = lerp(env.top[0], env.horizon[0], k); g = lerp(env.top[1], env.horizon[1], k); b = lerp(env.top[2], env.horizon[2], k); }
      else { const k = Math.pow(1 + y, 6); r = lerp(env.bottom[0], env.horizon[0], k); g = lerp(env.bottom[1], env.horizon[1], k); b = lerp(env.bottom[2], env.horizon[2], k); }
      for (const p of panels) {
        const c = d[0] * p.d[0] + d[1] * p.d[1] + d[2] * p.d[2];
        const e = smooth(p.cosR - 0.02, p.cosR + 0.01, c) * p.power;
        r += p.col[0] * e; g += p.col[1] * e; b += p.col[2] * e;
      }
      out[0] = r; out[1] = g; out[2] = b;
    };
    const px = [0, 0, 0];
    for (let f = 0; f < 6; f++) {
      const data = new Float32Array(N * N * 4);
      for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
        const u = ((i + 0.5) / N) * 2 - 1, v = ((j + 0.5) / N) * 2 - 1;
        const d = dir(f, u, v), l = Math.hypot(d[0], d[1], d[2]);
        radiance([d[0] / l, d[1] / l, d[2] / l], px);
        const o = (j * N + i) * 4; data[o] = px[0]; data[o + 1] = px[1]; data[o + 2] = px[2]; data[o + 3] = 1;
      }
      faces.push(data);
    }
    const tex = new B.RawCubeTexture(scene, faces, N, B.Constants.TEXTUREFORMAT_RGBA, B.Constants.TEXTURETYPE_FLOAT, true, false, B.Constants.TEXTURE_TRILINEAR_SAMPLINGMODE);
    tex.gammaSpace = false;
    // Irradiance computed synchronously from the same data: no async readPixels, identical per worker.
    try {
      tex.sphericalPolynomial = B.CubeMapToSphericalPolynomialTools.ConvertCubeMapToSphericalPolynomial({
        size: N, right: faces[0], left: faces[1], up: faces[2], down: faces[3], front: faces[4], back: faces[5],
        format: B.Constants.TEXTUREFORMAT_RGBA, type: B.Constants.TEXTURETYPE_FLOAT, gammaSpace: false,
      });
    } catch (err) { /* fall back to Babylon's own computation */ }
    return tex;
  }

  // ------------------------------------------------------------------ geometry & materials
  function makeShape(B, scene, shape, size, name) {
    const MB = B.MeshBuilder;
    switch (shape) {
      case "cube": return roundedCube(B, scene, name, size);
      case "capsule": return MB.CreateCapsule(name, { radius: size * 0.45, height: size * 1.6, tessellation: 48, subdivisions: 8, capSubdivisions: 12 }, scene);
      case "torus": return MB.CreateTorus(name, { diameter: size * 1.2, thickness: size * 0.38, tessellation: 96 }, scene);
      case "cylinder": return MB.CreateCylinder(name, { diameter: size * 1.1, height: size * 1.3, tessellation: 96 }, scene);
      case "icosahedron": return MB.CreateIcoSphere(name, { radius: size * 0.7, subdivisions: 1, flat: true }, scene);
      case "platform": return MB.CreateCylinder(name, { diameter: size * 2.4, height: size * 0.18, tessellation: 128 }, scene);
      default: return MB.CreateSphere(name, { diameter: size * 1.2, segments: 64 }, scene);
    }
  }
  // Per-face UVs for the rounded cube: each face shows the whole texture, upright and unmirrored as
  // seen from outside (same convention as a screen plane: u left->right, v bottom->top).
  function faceUVs(B, mesh) {
    const pos = mesh.getVerticesData(B.VertexBuffer.PositionKind), uv = [];
    let h = 0;
    for (let i = 0; i < pos.length; i++) h = Math.max(h, Math.abs(pos[i]));
    for (let i = 0; i < pos.length; i += 3) {
      const x = pos[i] / h, y = pos[i + 1] / h, z = pos[i + 2] / h;
      const ax = Math.abs(x), ay = Math.abs(y), az = Math.abs(z);
      let u, v;
      if (ax >= ay && ax >= az) { u = x > 0 ? z : -z; v = y; }        // right (+x) / left (-x)
      else if (ay >= az) { u = x; v = y > 0 ? z : -z; }                // top / bottom
      else { u = z > 0 ? -x : x; v = y; }                               // back (+z) / front (-z)
      uv.push((u + 1) / 2, (v + 1) / 2);
    }
    mesh.setVerticesData(B.VertexBuffer.UVKind, uv, true);
  }
  const VIDEO_UV_FLIP = { sphere: [false, true], cylinder: [true, false], capsule: [true, true] };  // [u, v]
  // media.surface "auto" by shape (mirrors unfold.models.MEDIA_SURFACES)
  const MEDIA_AUTO = { cube: "every_face", platform: "one_face", sphere: "wrap", capsule: "wrap", cylinder: "wrap" };
  function flipUVs(B, mesh, flipU, flipV) {
    const uv = mesh.getVerticesData(B.VertexBuffer.UVKind);
    for (let i = 0; i < uv.length; i += 2) {
      if (flipU) uv[i] = 1 - uv[i];
      if (flipV) uv[i + 1] = 1 - uv[i + 1];
    }
    mesh.setVerticesData(B.VertexBuffer.UVKind, uv, true);
  }
  // superellipsoid: a smooth rounded cube, not a hard low-poly box
  function roundedCube(B, scene, name, size) {
    const mesh = B.MeshBuilder.CreateSphere(name, { diameter: 2, segments: 48, updatable: true }, scene);
    const pos = mesh.getVerticesData(B.VertexBuffer.PositionKind), n = 8, half = size * 0.55;
    for (let i = 0; i < pos.length; i += 3) {
      const x = pos[i], y = pos[i + 1], z = pos[i + 2], l = Math.hypot(x, y, z) || 1;
      const dx = x / l, dy = y / l, dz = z / l;
      const k = Math.pow(Math.pow(Math.abs(dx), n) + Math.pow(Math.abs(dy), n) + Math.pow(Math.abs(dz), n), -1 / n);
      pos[i] = dx * k * half; pos[i + 1] = dy * k * half; pos[i + 2] = dz * k * half;
    }
    const normals = [];
    B.VertexData.ComputeNormals(pos, mesh.getIndices(), normals);
    mesh.updateVerticesData(B.VertexBuffer.PositionKind, pos);
    mesh.updateVerticesData(B.VertexBuffer.NormalKind, normals);
    mesh.refreshBoundingInfo();
    return mesh;
  }
  function makeMaterial(B, scene, preset, col, name) {
    const C3 = B.Color3, m = new B.PBRMaterial(name, scene);
    const c = new C3(col[0], col[1], col[2]);
    m.maxSimultaneousLights = 4;
    m.emissiveColor = c.scale(0.06);
    switch (preset) {
      case "chrome": m.albedoColor = new C3(0.96, 0.96, 0.97); m.metallic = 1; m.roughness = 0.07; break;
      case "gold": m.albedoColor = new C3(1.0, 0.76, 0.33); m.metallic = 1; m.roughness = 0.2; break;
      case "glass":
        m.albedoColor = c.scale(0.25); m.metallic = 0; m.roughness = 0.03;
        m.subSurface.isRefractionEnabled = true; m.subSurface.refractionIntensity = 0.9;
        m.subSurface.indexOfRefraction = 1.45; m.subSurface.tintColor = new C3(0.6 + col[0] * 0.4, 0.6 + col[1] * 0.4, 0.6 + col[2] * 0.4);
        m.clearCoat.isEnabled = true; m.clearCoat.intensity = 1; m.emissiveColor = c.scale(0.12); break;
      case "matte": m.albedoColor = c.scale(0.75); m.metallic = 0; m.roughness = 0.88; break;
      case "neon": m.albedoColor = c.scale(0.12); m.metallic = 0; m.roughness = 0.35; m.emissiveColor = new C3(c.r * c.r, c.g * c.g, c.b * c.b).scale(0.7); break;
      case "holo":
        m.albedoColor = c.scale(0.9); m.metallic = 0.55; m.roughness = 0.16; m.emissiveColor = c.scale(0.14);
        m.clearCoat.isEnabled = true; m.clearCoat.intensity = 0.8;
        m.iridescence.isEnabled = true; m.iridescence.intensity = 1; m.iridescence.minimumThickness = 300; m.iridescence.maximumThickness = 700; break;
      case "obsidian": m.albedoColor = new C3(0.02, 0.022, 0.028); m.metallic = 0.1; m.roughness = 0.1; m.clearCoat.isEnabled = true; m.clearCoat.intensity = 1; break;
      default: // ceramic
        m.albedoColor = c.scale(0.82); m.metallic = 0; m.roughness = 0.32; m.clearCoat.isEnabled = true; m.clearCoat.intensity = 0.9; m.clearCoat.roughness = 0.05;
    }
    return m;
  }
  function arcPoints(B, a, b, lift, n) {
    const pts = [];
    const mid = a.add(b).scale(0.5); mid.y += lift;
    for (let i = 0; i < n; i++) {
      const u = i / (n - 1), k = 1 - u;
      pts.push(new B.Vector3(k * k * a.x + 2 * k * u * mid.x + u * u * b.x, k * k * a.y + 2 * k * u * mid.y + u * u * b.y, k * k * a.z + 2 * k * u * mid.z + u * u * b.z));
    }
    return pts;
  }

  // ------------------------------------------------------------------ soft sprite batches
  let dotTexCache = null;
  function dotTexture(B, scene) {
    if (dotTexCache) return dotTexCache;
    const t = new B.DynamicTexture("dot", { width: 64, height: 64 }, scene, true);
    const c = t.getContext(), g = c.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.18, "rgba(255,255,255,0.75)");
    g.addColorStop(0.45, "rgba(255,255,255,0.18)"); g.addColorStop(1, "rgba(255,255,255,0)");
    c.clearRect(0, 0, 64, 64); c.fillStyle = g; c.fillRect(0, 0, 64, 64); t.hasAlpha = true; t.update();
    dotTexCache = t; return t;
  }
  // Camera-facing additive sprites as thin instances: one draw call per batch, no depth write, no fog
  // (fog would ADD fog colour to additive particles), excluded from the glow layer (no double glow).
  function spriteBatch(B, scene, name, count, size, intensity, lightInk) {
    const mesh = B.MeshBuilder.CreatePlane(name, { size }, scene);
    const mat = new B.StandardMaterial(name + "m", scene);
    const tex = dotTexture(B, scene);
    mat.disableLighting = true; mat.backFaceCulling = false; mat.emissiveTexture = tex; mat.opacityTexture = tex;
    mat.emissiveColor = new B.Color3(intensity, intensity, intensity);
    mat.alphaMode = lightInk ? B.Engine.ALPHA_COMBINE : B.Engine.ALPHA_ADD; mat.disableDepthWrite = true; mat.fogEnabled = false;
    mesh.material = mat;
    const matrices = new Float32Array(count * 16), colors = new Float32Array(count * 4);
    for (let i = 0; i < count; i++) matrices[i * 16 + 15] = 1;
    mesh.thinInstanceSetBuffer("matrix", matrices, 16, false);
    mesh.thinInstanceSetBuffer("color", colors, 4, false);
    mesh.alwaysSelectAsActiveMesh = true;
    const inv = B.Matrix.Identity(), rot = B.Quaternion.Identity(), sc = new B.Vector3(), mtx = B.Matrix.Identity();
    return {
      mesh,
      orient(camera) { camera.getViewMatrix().invertToRef(inv); inv.decompose(undefined, rot, undefined); },
      set(i, p, s, col) {
        sc.set(s, s, s); B.Matrix.ComposeToRef(sc, rot, p, mtx); mtx.copyToArray(matrices, i * 16);
        const o = i * 4; colors[o] = col[0]; colors[o + 1] = col[1]; colors[o + 2] = col[2]; colors[o + 3] = 1;
      },
      hide(i) {
        const o = i * 16;
        // Clear rotated basis, translation and colour too: same state as a fresh hidden instance.
        matrices.fill(0, o, o + 15); matrices[o + 15] = 1;
        colors.fill(0, i * 4, i * 4 + 4);
      },
      flush() { mesh.thinInstanceBufferUpdated("matrix"); mesh.thinInstanceBufferUpdated("color"); },
    };
  }

  // ------------------------------------------------------------------ effects
  function makeEffect(B, scene, e, byId, rngFor, S) {
    const V = B.Vector3;
    const rng = rngFor(e.id);
    const col = ROLE[e.role] || ROLE.neutral;
    const count = Math.min(e.count, CAPS[e.preset] || 1000);
    const src = byId.get(e.node);
    const size = { stream: 0.2, flock: 0.18, orbit: 0.14, burst: 0.22, field: 0.1 }[e.preset] || 0.16;
    const energy = { stream: 0.42, flock: 0.3, orbit: 0.34, burst: 0.5, field: 0.4 }[e.preset] || 0.4;
    const batch = spriteBatch(B, scene, "fx_" + e.id, count, size, energy * e.intensity, S.__lightInk);
    const P = new V(), seeds = new Float32Array(count * 6);
    for (let i = 0; i < seeds.length; i++) seeds[i] = rng();
    const life = (t) => smooth(e.start, e.start + 0.4, t) * (1 - smooth(e.end - 0.4, e.end, t));
    const shade = (k, out) => { out[0] = col[0] * k; out[1] = col[1] * k; out[2] = col[2] * k; return out; };
    const c3 = [0, 0, 0];

    if (e.preset === "stream") {
      const dst = byId.get(e.to_node);
      const link = S.links.find((l) => (l.from_node === e.node && l.to_node === e.to_node) || (l.from_node === e.to_node && l.to_node === e.node));
      const pts = arcPoints(B, src.base, dst.base, link ? link.lift : 1.2 + V.Distance(src.base, dst.base) * 0.12, 96);
      let len = 0; for (let i = 1; i < pts.length; i++) len += V.Distance(pts[i - 1], pts[i]);
      const T = len / (4 * e.speed);
      const dir = dst.base.subtract(src.base).normalize();
      const side = V.Cross(dir, V.Up()).normalize(), upv = V.Cross(side, dir).normalize();
      const at = (u) => { const f = u * (pts.length - 1), k = Math.min(pts.length - 2, Math.floor(f)); return V.LerpToRef(pts[k], pts[k + 1], f - k, P); };
      return { meshes: [batch.mesh], update(t, camera) {
        batch.orient(camera);
        for (let i = 0; i < count; i++) {
          const s = seeds.subarray(i * 6, i * 6 + 6), sj = 0.75 + s[1] * 0.5;
          const depart = e.start + s[0] * T / sj;               // first departure
          if (t < depart) { batch.hide(i); continue; }
          const laps = (t - depart) * sj / T, u = frac(laps);
          if (depart + Math.floor(laps) * T / sj > e.end) { batch.hide(i); continue; }   // stopped emitting: drain
          const env = Math.pow(Math.sin(Math.PI * u), 0.6);
          const a = (s[2] - 0.5) * 2, b = (s[3] - 0.5) * 2;
          const wob = 0.35 * e.spread * Math.sin(u * 9 + s[4] * 20 + t * 1.7);
          at(u);
          P.addInPlace(side.scale((a * 1.1 * e.spread + wob) * env)).addInPlace(upv.scale((b * 0.8 * e.spread + wob * 0.6) * env));
          const bright = 0.55 + s[5] * 0.7;
          batch.set(i, P, (0.55 + 0.6 * env) * (0.8 + s[4] * 0.5), shade(bright * (0.35 + 0.65 * env), c3));
        }
        batch.flush();
      } };
    }

    if (e.preset === "orbit") {
      return { meshes: [batch.mesh], update(t, camera) {
        batch.orient(camera);
        const L = life(t);
        for (let i = 0; i < count; i++) {
          if (L <= 0) { batch.hide(i); continue; }
          const s = seeds.subarray(i * 6, i * 6 + 6);
          const r = src.n.size * (1.1 + e.spread * 2.2 * s[0]) + 0.2;
          const w = e.speed * (0.5 + s[1] * 0.9) / Math.max(0.6, r) * 1.6;
          const ang = s[2] * 6.2832 + (t - e.start) * w;
          const tilt = (s[3] - 0.5) * 1.1, yaw = s[4] * 6.2832;
          const x = Math.cos(ang) * r, z = Math.sin(ang) * r, y = z * Math.sin(tilt); const z2 = z * Math.cos(tilt);
          P.set(src.base.x + x * Math.cos(yaw) - z2 * Math.sin(yaw), src.base.y + y, src.base.z + x * Math.sin(yaw) + z2 * Math.cos(yaw));
          batch.set(i, P, L * (0.6 + s[5] * 0.8), shade((0.5 + s[5] * 0.6) * L, c3));
        }
        batch.flush();
      } };
    }

    if (e.preset === "burst") {
      const k = 2.4, g = -3.2;
      return { meshes: [batch.mesh], update(t, camera) {
        batch.orient(camera);
        const tau = t - e.start;
        for (let i = 0; i < count; i++) {
          const s = seeds.subarray(i * 6, i * 6 + 6), lifeT = (0.5 + s[0] * 1.2) / Math.sqrt(e.speed);
          if (tau < 0 || tau > lifeT || t > e.end) { batch.hide(i); continue; }
          const uz = s[1] * 2 - 1, ph = s[2] * 6.2832, rr = Math.sqrt(1 - uz * uz);
          const v0 = e.speed * (3 + s[3] * 6) * (0.4 + e.spread);
          const d = (1 - Math.exp(-k * tau)) / k, gy = g * (tau / k - d / k);
          P.set(src.base.x + rr * Math.cos(ph) * v0 * d, src.base.y + uz * v0 * d + gy, src.base.z + rr * Math.sin(ph) * v0 * d);
          const fade = 1 - tau / lifeT;
          batch.set(i, P, (0.5 + s[4]) * (0.4 + 0.6 * fade), shade((0.6 + 0.9 * fade) * fade, c3));
        }
        batch.flush();
      } };
    }

    if (e.preset === "field") {
      const R = src.n.size * (3 + e.spread * 9) + 2;
      return { meshes: [batch.mesh], update(t, camera) {
        batch.orient(camera);
        const L = life(t);
        for (let i = 0; i < count; i++) {
          if (L <= 0) { batch.hide(i); continue; }
          const s = seeds.subarray(i * 6, i * 6 + 6);
          const bx = (s[0] * 2 - 1) * R, bz = (s[1] * 2 - 1) * R;
          const y = frac(s[2] + t * 0.03 * e.speed) * R * 0.6 - R * 0.1;
          const sw = 0.6 * e.speed;
          P.set(src.base.x + bx + Math.sin(t * sw * 0.7 + s[3] * 6.28 + y * 0.3) * 0.8,
                src.base.y + y,
                src.base.z + bz + Math.cos(t * sw * 0.6 + s[4] * 6.28 + bx * 0.2) * 0.8);
          const tw = 0.5 + 0.5 * Math.sin(t * (1 + s[5] * 2) + s[3] * 20);
          const edge = 1 - smooth(R * 0.6, R, Math.hypot(bx, bz));
          batch.set(i, P, 0.6 + s[5] * 0.9, shade(0.35 * tw * L * edge, c3));
        }
        batch.flush();
      } };
    }

    // flock: the one stateful preset. Fixed 1/60 s steps from e.start, checkpoints every 0.5 s.
    const DT = 1 / 60, EVERY = 30, n = count;
    const init = () => {
      const r = mulberry32((hashString(e.id) ^ S.seed ^ 0x9e3779b9) >>> 0);
      const st = { step: 0, p: new Float32Array(n * 3), v: new Float32Array(n * 3) };
      for (let i = 0; i < n; i++) {
        const a = r() * 6.2832, rad = src.n.size * (1.2 + r() * 1.6), yy = (r() - 0.5) * 1.4;
        st.p[i * 3] = src.base.x + Math.cos(a) * rad; st.p[i * 3 + 1] = src.base.y + yy; st.p[i * 3 + 2] = src.base.z + Math.sin(a) * rad;
        st.v[i * 3] = -Math.sin(a) * 1.5; st.v[i * 3 + 2] = Math.cos(a) * 1.5;
      }
      return st;
    };
    const clone = (st) => ({ step: st.step, p: st.p.slice(), v: st.v.slice() });
    const orbitR = src.n.size * 2.0 + 0.4, maxV = 2.6 * e.speed;
    const stepFlock = (st) => {
      const p = st.p, v = st.v, R2 = 0.55 * 0.55, NB = 1.1 * 1.1;
      for (let i = 0; i < n; i++) {
        const k = i * 3; let sx = 0, sy = 0, sz = 0, ax = 0, ay = 0, az = 0, cx = 0, cy = 0, cz = 0, cnt = 0;
        for (let j = 0; j < n; j++) {
          if (j === i) continue; const q = j * 3;
          const dx = p[q] - p[k], dy = p[q + 1] - p[k + 1], dz = p[q + 2] - p[k + 2], d2 = dx * dx + dy * dy + dz * dz;
          if (d2 < NB) { cnt++; ax += v[q]; ay += v[q + 1]; az += v[q + 2]; cx += dx; cy += dy; cz += dz;
            if (d2 < R2) { const w = 1 / (d2 + 1e-3); sx -= dx * w; sy -= dy * w; sz -= dz * w; } }
        }
        let fx = 0, fy = 0, fz = 0;
        if (cnt) { fx += (ax / cnt - v[k]) * 1.2 + (cx / cnt) * 0.9 + sx * 0.05; fy += (ay / cnt - v[k + 1]) * 1.2 + (cy / cnt) * 0.9 + sy * 0.05; fz += (az / cnt - v[k + 2]) * 1.2 + (cz / cnt) * 0.9 + sz * 0.05; }
        const ox = p[k] - src.base.x, oy = p[k + 1] - src.base.y, oz = p[k + 2] - src.base.z, r = Math.hypot(ox, oy, oz) + 1e-4;
        const pull = (r - orbitR) * -2.4;
        fx += (ox / r) * pull - oz * 0.8; fy += (oy / r) * pull * 1.4; fz += (oz / r) * pull + ox * 0.8;
        v[k] += fx * DT; v[k + 1] += fy * DT; v[k + 2] += fz * DT;
        const sp = Math.hypot(v[k], v[k + 1], v[k + 2]), lim = Math.min(1, maxV / (sp + 1e-4));
        v[k] *= lim; v[k + 1] *= lim; v[k + 2] *= lim;
      }
      for (let i = 0; i < n * 3; i++) p[i] += v[i] * DT;
      st.step++;
    };
    const checkpoints = new Map([[0, clone(init())]]);
    let st = clone(checkpoints.get(0));
    const simTo = (t) => {
      const target = Math.max(0, Math.round((Math.min(t, e.end) - e.start) / DT));
      if (target < st.step) { let best = 0; for (const k of checkpoints.keys()) if (k <= target && k > best) best = k; st = clone(checkpoints.get(best)); }
      while (st.step < target) { stepFlock(st); if (st.step % EVERY === 0 && !checkpoints.has(st.step)) checkpoints.set(st.step, clone(st)); }
    };
    return { meshes: [batch.mesh], update(t, camera) {
      batch.orient(camera);
      const L = life(t);
      if (L <= 0) { for (let i = 0; i < n; i++) batch.hide(i); batch.flush(); return; }
      simTo(t);
      for (let i = 0; i < n; i++) {
        P.set(st.p[i * 3], st.p[i * 3 + 1], st.p[i * 3 + 2]);
        const s = seeds[i * 6];
        batch.set(i, P, L * (0.7 + s * 0.6), shade((0.55 + s * 0.5) * L, c3));
      }
      batch.flush();
    } };
  }

  // ------------------------------------------------------------------ full-screen moments
  function buildMoments(B, camera, list) {
    const noop = { set() {} };
    if (!list.length) return noop;
    B.Effect.ShadersStore.unfoldMomentsFragmentShader = `
      precision highp float;
      varying vec2 vUV;
      uniform sampler2D textureSampler;
      uniform vec4 shock0; uniform vec4 shock1; uniform vec4 shock2; uniform vec4 shock3;
      uniform float flash; uniform float glitch; uniform float frame; uniform float aspect;
      vec2 wave(vec2 uv, vec4 s, inout float ring) {
        if (s.w <= 0.0) return vec2(0.0);
        vec2 d = uv - s.xy; d.x *= aspect;
        float dist = length(d); float x = (dist - s.z) / 0.055;
        float f = exp(-x * x) * s.w;
        ring += f;
        vec2 n = d / max(dist, 1e-4); n.x /= aspect;
        return n * f * 0.028;
      }
      float hash(float n) { return fract(sin(n) * 43758.5453); }
      void main(void) {
        float ring = 0.0;
        vec2 off = wave(vUV, shock0, ring) + wave(vUV, shock1, ring) + wave(vUV, shock2, ring) + wave(vUV, shock3, ring);
        vec2 uv = vUV - off;
        if (glitch > 0.0) {
          float band = floor(uv.y * 28.0);
          float r = hash(band * 12.9898 + frame * 78.233);
          if (r < glitch * 0.45) uv.x += (hash(band + frame) - 0.5) * 0.09 * glitch;
        }
        vec2 cav = off * 0.15 + vec2(glitch * 0.006, 0.0);
        vec4 base = texture2D(textureSampler, uv);
        vec3 col = vec3(texture2D(textureSampler, uv + cav).r, base.g, texture2D(textureSampler, uv - cav).b);
        col += vec3(0.55, 0.75, 1.0) * ring * 0.22;
        col = mix(col, vec3(1.0), clamp(flash, 0.0, 1.0) * 0.8);
        gl_FragColor = vec4(col, max(base.a, clamp(flash + ring * 0.3, 0.0, 1.0)));
      }`;
    const pp = new B.PostProcess("unfoldMoments", "unfoldMoments", ["shock0", "shock1", "shock2", "shock3", "flash", "glitch", "frame", "aspect"], null, 1.0, camera, B.Texture.BILINEAR_SAMPLINGMODE, camera.getEngine(), false);
    let shocks = [], flash = 0, glitch = 0, frame = 0;
    pp.onApply = (effect) => {
      for (let i = 0; i < 4; i++) { const s = shocks[i] || [0, 0, 0, 0]; effect.setFloat4("shock" + i, s[0], s[1], s[2], s[3]); }
      effect.setFloat("flash", flash); effect.setFloat("glitch", glitch); effect.setFloat("frame", frame);
      effect.setFloat("aspect", pp.width / Math.max(1, pp.height));
    };
    return { set(s, f, g, fr) { shocks = s; flash = f; glitch = g; frame = fr; } };
  }
})();
