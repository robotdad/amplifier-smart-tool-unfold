# scene3d authoring guide

The 3D layer renders full-frame *beneath* every 2D element. It carries space, flow and atmosphere; 2D text carries every word and number the viewer must read exactly.

## 1. Decide first

Use `scene3d` only if you can say "yes" to at least one of these:
- **Systems as objects:** components the viewer should see as things in one space (gateway, agent, tool, store).
- **Flow or path:** something travels between parts, or a part is busy.
- **Containment or topology:** what sits inside, around or on top of what.
- **Atmosphere:** a full-frame mood a flat background cannot give.

Removal test: flatten the idea to a 2D diagram; if only decoration is lost, stay 2D.

Before authoring, check:
1. State the audience's intended experience and what 3D contributes: depth, occlusion,
   material, spatial connection or a motivated reveal. A film/product/editorial brief
   does not call for a service graph unless its direction actually says so.
2. Keep every precise quantity in 2D text. Depth, `size`, particle `count` and `speed` are distorted by perspective, so they show *relative* amounts at best.
3. Use only the objects and effects the purpose needs; one object or no effects may
   be enough. Do not add satellites, sparks, links, rings or labels to fill a template.
4. Choose one subject per shot.

## 2. Optional system-diagram encoding

The table and throughput/queue interpretations below apply **only to relevant
system explanations**. They are not universal art direction. For other briefs,
choose shape/material for the subject, use `role:"neutral"` if no category applies,
and choose optional `color:"#RRGGBB"` independently. Explain each added effect's
purpose or leave the effects list empty.

| Channel | Meaning | Rule |
|---|---|---|
| `role` (legacy hue) | category | In a system diagram, keep categories consistent: request blue, response green, agent violet, tool orange, data cyan, error red, cache yellow, neutral grey. A node color override does not change its role or recolor links/effects. |
| `shape` | kind of part | Keep one shape per kind. Common choices: `cube` for services/gateways, `icosahedron` for agents, `cylinder` for stores/tools, `platform` for a stage or tier that other nodes sit on. |
| `material` | focus vs context | See §4. |
| `position` | topology | Put the flow left→right along x, keep z for secondary rows, and use y only for stacking. Never map a quantity to depth. |
| `size` | importance | Use 1.0 by default, 1.3–1.6 for the subject and 0.6–0.8 for minor parts. It is never a value. |
| effect preset | behaviour | See below. |
| `count`, `speed` | more or less | Relative only. When the amount matters, give the number in a 2D legend such as "≈ 2k req/s". |

Every effect must MEAN something:
- `stream` (A→B): throughput. Particles travel about 4×`speed` units/s on an automatic arc, so a stream takes roughly `path length ÷ (4×speed)` seconds to fill. Start it that much before the beat you need it for. When `end` is reached, particles in flight finish their trip (drain). For a link that the stream rides, set link `lift ≈ 1.2 + 0.12 × distance`.
- `flock`: self-organising behaviour (agents coordinating, a swarm deciding). It circles about `2×size+0.4` from its node.
- `orbit`: a resident set or queue held at a node. Its radius grows with `spread`.
- `burst`: a discrete event. All particles fire at `start` and live 0.5–1.7 s, so set `end ≈ start + 2`.
- `field`: ambient pressure or diffusion. Its radius is about `size×(3+9×spread)+2`, which is huge, so keep `spread` 0–0.2 and `intensity` ≤ 0.8.

Starting counts:

| Preset | Count |
|---|---|
| stream | 300–900 |
| flock | 120–300 |
| orbit | 150–500 |
| burst | 150–400 |
| field | 400–1200 |

Keep the total at ≤ 3000; the hard cap is 8000. Particles are additive glow sprites, so dense overlap turns white. Watch for it at low `spread` or high `intensity`.

## 3. Camera and composition

The supported camera is an orbit camera: `azimuth` orbits the target, `elevation`
looks down, `distance` dollies, `fov` zooms. This describes the available controls,
not a required turntable treatment. A still camera is valid. At `azimuth -90` you
get the front view, where +x is screen-right and +z recedes.
The ranges below are starting suggestions for readable system diagrams, not
mandatory framing, rhythm or shot counts for film/product/editorial work.

- **Framing math (16:9):** at the target, the visible width is `3.56 × distance × tan(fov/2)`. At fov 40 that is about 1.3×distance wide and 0.73×distance tall. Size `distance` so the subject plus its label fills the middle 60 %.
- **Establishing shot at 0:** `target` null (centroid), elevation 22–32, distance 18–28, fov 35–42.
- **Working shots:** target the node being explained. Use distance 10–16 for a neighbourhood and 6–10 for a close-up; stay above 4×that node's `size`. Use elevation 15–40. 60–85 flattens depth into a map, and below 10 hides the floor. Use fov 30–45. Go to 20–28 when you want less perspective distortion, and never go above 60.
- **Moves:** each shot eases from the camera state at its `at` over `duration`. Omitted fields take their defaults, not the previous shot's values, so restate elevation and fov.
  - Use 1.5–3 s eases with `power2.inOut`, and `power2.out` for urgency.
  - Hold each arrival for ≥ 2 s before the next move.
  - Keep the azimuth change ≤ 40° per shot and ≤ 15°/s. Azimuth interpolates linearly, so -90→270 is a full spin, which you should avoid.
  - Change target or azimuth, not both at once.
- **Cuts** (`duration 0`) throw away the spatial link between views. Use them only at a narrative break.
- **Density:** use 2–4 shots per 10 s, one idea per shot.
- The 2D layer does not follow the 3D camera. Only place 2D text beside a node while the camera is holding.

## 4. Materials and environment

| Material | Communicates | Note |
|---|---|---|
| `neon` | active, energised, the focus | Emits light and blooms. Use it for 1–2 nodes. |
| `holo` | intelligent, novel (agents) | Iridescent. The role hue shows through. |
| `ceramic` | an ordinary component (default) | The role colour reads cleanly. |
| `matte` | context, infrastructure | Muted, recedes. |
| `glass` | boundary, container, stateless | The tint is faint. |
| `chrome` / `gold` | metallic surface | Without `color`, these keep their fixed metallic hues. An explicit node color replaces their base tint, not their physical parameters. |
| `obsidian` | dark gloss | An explicit node color replaces its base tint. Rings are optional, not a requirement. |

These material associations are suggestions, not product claims or genre rules.
Choose them from the actual brief; do not automatically make every subject neon/holo.

### Independent style controls (opt-in)

- Node `entrance:"none"` means hidden before `appear_at`, full size at and after it,
  including frame zero when `appear_at:0`. `"pop"` restores the legacy 0.7s overshoot.
  Rings and labels follow the selected entrance.
- Node `idle_motion:"none"` removes bob, implicit shape tumble and automatic ring
  rotation. `"bob"` retains the legacy idle treatment (including tumble/ring motion).
  Explicit `spin` and `media.surface:"facing_camera"` still do what they request.
  For a completely stationary object use no entrance/idle motion, `spin:0`, and no
  camera-facing surface; camera moves can still change its projected appearance.
- Optional node `color:"#RRGGBB"` is an sRGB material tint converted to linear
  colour for lighting. It also tints that node's ring/label border, not its video
  pixels, connected links or effects. Materials and lighting still affect the final
  pixels; it is not a promise of exact screen RGB.
- `floor_grid:false` retains a plain floor where the environment supplies one.
  It does not add a floor to `deep_space`, override `floor:false`, or add one to
  transparent output. It removes neon-grid floor emission along with the grid.
- `post_overrides.bloom_weight` accepts 0–1; `glow_intensity` accepts 0–2.
  Zero disables that pass. Set both to zero for no bloom/glow halo passes.
  Unspecified members inherit independently from the selected preset/legacy glow.
  Other post effects, material emission, particles and label CSS shadows remain;
  this is not a universal unlit/no-post switch.
- All new fields are optional: omitted or null preserves the old treatment.
  Choose rings and effects deliberately rather than relying on their defaults.

### Transparent layer over existing 2D work

`scene3d.background:"transparent"` leaves the original `Scene.background` and all
2D fields untouched. The canvas clears to alpha zero instead of painting the 3D
environment, so the existing CSS background shows through. The sky, stars and
floor are omitted, even with `floor:true`; floor is not a shadow-catching transparent
plane. The environment texture, lights and reflections still shade the objects.
The 3D layer stays beneath 2D elements: it does not occlude titles or graphics.

Omitted/null background or `"environment"` preserves legacy behavior. A transparent
whole-scene `Scene.background` still suppresses the 3D backdrop even when environment
is selected; selecting the layer option does not change the whole-scene value.
Post effects and material alpha remain active and may extend visual coverage beyond
the geometry; explicitly disable unwanted bloom/glow and omit unnecessary moments.
Check uncovered pixels against the actual 2D baseline, and edges against alpha
compositing—not just equal source fields. Opaque MP4 still flattens the finished
composition; use an appropriate alpha delivery for a transparent final output.
Normal rendering of this explicit layer mode reads software WebGL RGBA into bounded
owner-encoded PNGs, then presents ordinary images and typed projected labels through
an awaited seek. This avoids a visible canvas changing the underlying 2D paint groups
and keeps the existing 2D capture policy. Direct author-HTML previews still have a
live canvas and are not that final preservation surface. Staging is limited to
512 mebipixels total, about 8.6s at 1080p/30fps, with explicit refusal rather than
trimming/downscaling; the stages share one render deadline. Lossless uncovered-pixel
checks do not promise unchanged encoded pixels across a deliberately changed scene.
Exact decoded repeatability of the same revision/runtime is a separate obligation.
This control does not fix video-wrap cap artifacts or turn its unlit video surface
into a shaded product material.

| Environment | Look | Label ink / fit |
|---|---|---|
| `studio_dark` | neutral default | Light ink. |
| `deep_space` | stars, no floor | Good for abstract networks. |
| `dusk` | warm horizon | Good for human or narrative stories. |
| `lab_white` | bright, clean | Dark ink. Additive particles almost vanish here, so use few effects. |
| `neon_grid` | magenta grid | Competes with the agent and data hues, so give it high contrast and few roles. |

## 5. Labels and 2D text

- A node `label` is a ~22 px uppercase pill above its node, drawn over the 3D and never hidden by geometry. Nothing declutters it: prevent overlap by spacing.
- Keep labels to ≤ 14 characters, one or two words, one per important node, and ≤ 6 on screen.
- Keep labelled nodes at least `distance/6` units apart in screen-x at the held shot, which is about 250 px.
- Label pills paint *above* 2D elements too, so keep cards and text out of label positions.
- **Titles and explanations are 2D text elements.**
  - Place titles in the top band (y < 180) and explanations in the bottom band (y > 880), or in whatever screen region the 3D leaves empty.
  - Use `font_size` ≥ 32 over 3D.
  - Over busy areas, put the text in a `card` with a dark fill.
  - Keep text away from dense particle regions and the bloom around neon nodes.

## 5a. Video screens

A `screens` entry plays a library video on a panel inside the 3D scene: picture-in-picture,
a retained Unfold render shown inside a larger explanation, or a clip framed as evidence.

- Use one when the clip itself is the point (show the output, then explain it). A screen is
  not decoration; if nothing in the narration refers to what is playing, cut it.
- The video must be an `asset_id` from the selected identity. Never invent or describe a clip
  you were not given.
- Size for legibility: at the default camera distance a `width` of 4 to 6 reads as a monitor;
  frame the screen with a camera shot that targets its id when the audience must read it.
- Keep at most two screens visible at once; each one is a large, bright rectangle that competes
  with every node.
- `play_from` controls when playback starts (default: when the screen appears); `rate` speeds
  it up or slows it down; `loop: false` holds the last frame.
- Screen `entrance:"none"` removes the 0.6s scale-in; at `appear_at:0` the full-size
  video is present on frame zero. `"scale"` or omission retains the old entrance.
  This does not change the source clock. A `frame:"floating"` still bobs; use bezel
  or no frame for a stationary screen.
- The clip is decoded at 15 frames per second and 512 px wide. Fine text inside the clip will
  not survive; show it large or not at all.
- The required source span is limited to 60 seconds per surface, after `media_start` and
  accounting for `rate`. A loop needs the full remaining clip, not a truncated loop.
  Frames taller than 4096 px at 512 px wide are refused. Pages fit within 4096 × 4096;
  all video surfaces together are limited to 134217728 atlas pixels, including padding.
  These limits fail explicitly with `RESOURCE_LIMIT`; simplify the request rather than
  silently shorten, crop or stretch its footage. The same limits apply to video on objects.

## 5b. Video on objects

A node can show a library video on its own surface (`media`), and keep moving: pop-in, bob, `spin`.
Only these shapes take video: cube, platform, sphere, capsule, cylinder.

**How the clip sits on the object is the user's call.** If the request does not say, ask one short
question offering only the options for that shape; if you cannot ask, use `auto` and say which you
used in the `explanation`.

| `media.surface` | cube | platform | sphere / capsule / cylinder |
|---|---|---|---|
| `auto` (default) | = `every_face` | = `one_face` | = `wrap` |
| `every_face` | the same clip on all six faces | - | - |
| `one_face` | the front face only; the rest keeps the material | the top | - |
| `wrap` | - | - | wraps once around |
| `facing_camera` | turns to face the camera; clip on that face | - | turns the wrap's centre to the camera |

- Choose `facing_camera` when the audience must read the clip while the camera moves (the object
  "billboards"); it cannot be combined with `spin`.
- Choose `every_face` or `wrap` with `spin` when the clip is texture or atmosphere, not something
  to read; a wrapped clip is only half visible at a time.
- Choose `one_face` for a device or monitor-like object whose other sides should look solid.
- `spin:0` disables rotation, not legacy bob/pop. Use the independent entrance/idle
  controls for a stationary node. Nonzero spin turns it at that many degrees per second.
- For reading-critical footage prefer a `screens` entry or `facing_camera`: faces are small, and the
  clip is decoded at 512 px wide.

## 6. Post and moments

| Post | Look | Use |
|---|---|---|
| `clean` | no depth of field, light vignette | Most legible. Use it for dense or technical scenes. |
| `cinematic` (default) | DOF f/3.2 focused on the shot `target`, grain, fringe | Anything off-target blurs, so always set `target` to the subject. |
| `neon` | heavy bloom, +saturation, strong fringe | Keep particle `intensity` ≤ 1 or everything washes out. |
| `dreamy` | shallow DOF, soft bloom | Mood only. It is not for detail. |
| `noir` | fully desaturated, grain | It erases role hues. Use it only when categories do not matter, and name them in 2D text. |

Moments are emphasis: ≤ 1 per ~4 s, each tied to a narrative beat (same-beat moments count as one). They affect only the 3D layer.
- `shockwave` (node): impact or arrival, "it lands here". It is strongest at `at`, so put `at` on the beat.
- `flash`: a reveal or state reset. Keep `strength` ≤ 0.7.
- `glitch`: failure, corruption or error only.
- `focus_pull` (node): a shallow-focus pulse on the node. It peaks mid-duration, so set `at = beat − duration/2` and a duration of 1–2 s.

## 7. Budget

Renders use software GL at about 1.5 s per frame, so each scene-second costs roughly 20–40 s of render time.
- Keep 3D scenes 6–12 s long and fix several issues per iteration.
- Prefer fewer, meaningful particles; the §2 counts are enough.
- `flock` costs grow with count², so stay at ≤ 300.
- Every timing must end within the scene duration.

## 8. Self-review of sampled frames

Sample frame zero, at 0.5 s, each shot arrival, each moment peak and near the end, then check:
1. **Glance test:** is the one-sentence intended reading visible without explanation?
2. **First frame:** is required content present at full intended size? `appear_at:0`
   alone still starts the legacy entrance nearly invisible; use `entrance:"none"`
   when full-size frame-zero presence is required.
3. **Focus:** is the subject sharp and framed as intended, without unintended clipping?
4. **Labels:** does each sit on its node, fully inside the frame, without overlapping another label or a card?
5. **Hue identity:** do the roles stay distinguishable where streams cross or glow overlaps? Is there a white wash anywhere?
6. **Legibility:** does the 2D text hold contrast over the 3D behind it?
7. **Motion between samples:** does a stream read as a dotted ribbon (not specks, not a white tube)? Do appearances and moves happen in reading order?
8. **Honesty:** is every quantity the viewer needs stated in 2D text?
9. **Copy and claims:** does exact supplied copy match case and punctuation? Have you
   invented a date, slogan, feature or observed performance? Remove unsupported claims.
10. **Purpose:** does 3D contribute the intended experience, or is this the same graph
    with different labels? Is product rotation visible on the product rather than
    just a decorative ring? These require media review, not schema compliance.

Patch the field that controls a failed check: clipping → `distance`/`fov`; label clash → `position`; wash → `count`/`intensity`/`spread`.

## 9. Worked examples — only for system-diagram briefs

These illustrate the optional encoding in §2, not defaults or templates for unrelated
work. Do not import their objects, particles, copy or shockwaves into another brief.

**(a) Request flow**, 10 s. A 2D title sits top-left; the 2D bottom band says "Gateway → agent → tool".

```json
{"environment":"studio_dark","post":"cinematic","nodes":[
 {"id":"gateway","shape":"cube","material":"ceramic","role":"request","position":[-7,1.2,0],"label":"GATEWAY"},
 {"id":"agent","shape":"icosahedron","material":"holo","role":"agent","position":[0,1.6,0],"size":1.4,"appear_at":0.4,"label":"AGENT"},
 {"id":"tool","shape":"cylinder","material":"neon","role":"tool","position":[7,1.2,1.5],"appear_at":0.8,"label":"SEARCH"}],
"links":[
 {"id":"l_gw_agent","from_node":"gateway","to_node":"agent","role":"request","lift":2.0,"appear_at":0.9},
 {"id":"l_agent_tool","from_node":"agent","to_node":"tool","role":"tool","lift":2.1,"appear_at":1.3}],
"effects":[
 {"id":"req_stream","preset":"stream","node":"gateway","to_node":"agent","role":"request","start":1.0,"end":9.5,"count":600},
 {"id":"agent_flock","preset":"flock","node":"agent","role":"agent","start":2.5,"end":10,"count":220},
 {"id":"tool_stream","preset":"stream","node":"agent","to_node":"tool","role":"tool","start":4.0,"end":9.5,"count":400,"speed":1.3}],
"camera":[
 {"at":0,"azimuth":-90,"elevation":28,"distance":24,"fov":38},
 {"at":3.0,"duration":2.5,"target":"agent","azimuth":-75,"elevation":22,"distance":14}],
"moments":[{"at":5.2,"kind":"shockwave","node":"tool","strength":0.8,"duration":0.9}]}
```

**(b) Failure beat**, 6 s. A 2D caption at 3 s reads "Tool timed out after 30 s".

```json
{"environment":"studio_dark","post":"clean","nodes":[
 {"id":"agent","shape":"icosahedron","material":"holo","role":"agent","position":[-4,1.5,0],"size":1.3,"label":"AGENT"},
 {"id":"tool","shape":"cylinder","material":"matte","role":"tool","position":[4,1.2,0],"label":"TOOL"},
 {"id":"fault","shape":"icosahedron","material":"neon","role":"error","position":[4,3.4,0],"size":0.7,"appear_at":2.8,"ring":false,"label":"TIMEOUT"}],
"links":[{"id":"call","from_node":"agent","to_node":"tool","role":"request","lift":2.2,"appear_at":0.3}],
"effects":[
 {"id":"calls","preset":"stream","node":"agent","to_node":"tool","role":"request","start":0.5,"end":2.8,"count":400},
 {"id":"fail_burst","preset":"burst","node":"tool","role":"error","start":2.8,"end":4.8,"count":350,"speed":1.4,"spread":0.5}],
"camera":[
 {"at":0,"azimuth":-90,"elevation":24,"distance":18},
 {"at":2.4,"duration":1.2,"target":"tool","azimuth":-80,"distance":10,"ease":"power2.out"}],
"moments":[{"at":2.8,"kind":"glitch","duration":0.7,"strength":0.9}]}
```

---
Guidance adapted from the amplifier-bundle-3d-developer bench (github.com/colombod/amplifier-bundle-3d-developer), MIT License.
