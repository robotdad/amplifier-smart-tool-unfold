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
1. Write the intended reading in one sentence, for example "requests pile up at the agent, then one tool call fires."
2. Keep every precise quantity in 2D text. Depth, `size`, particle `count` and `speed` are distorted by perspective, so they show *relative* amounts at best.
3. Budget 3–9 nodes, ≤ 5 effects, ≤ 6 labels; viewers track only a handful of moving things.
4. Choose one subject per shot.

## 2. Encoding: one meaning per channel

| Channel | Meaning | Rule |
|---|---|---|
| `role` (hue) | category | The same role always means the same kind of thing: request blue, response green, agent violet, tool orange, data cyan, error red, cache yellow, neutral grey. Reserve `error` for failure. |
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

Use a turntable camera: `azimuth` orbits the target, `elevation` looks down, `distance` dollies, `fov` zooms. At `azimuth -90` you get the front view, where +x is screen-right and +z recedes. Increasing azimuth swings the camera toward +x.

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
| `chrome` / `gold` | a single hero or prize object | These ignore role hue, which survives only through the `ring` and label border. Keep `ring: true`, and avoid all-chrome scenes. |
| `obsidian` | sink, storage, black box, "off" | Dark gloss. Keep `ring: true`. |

Contrast focus against context: neon/holo on the subject, matte/ceramic elsewhere.

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
- The clip is decoded at 15 frames per second and 512 px wide. Fine text inside the clip will
  not survive; show it large or not at all.

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

Sample at 0.5 s, each shot arrival, each moment peak and near the end, then check:
1. **Glance test:** is the one-sentence intended reading visible without explanation?
2. **First frame:** is it non-empty? At least one node should have `appear_at` 0, or a 2D title should already be showing.
3. **Focus:** is the subject sharp, framed centrally, and not clipped at the frame edges?
4. **Labels:** does each sit on its node, fully inside the frame, without overlapping another label or a card?
5. **Hue identity:** do the roles stay distinguishable where streams cross or glow overlaps? Is there a white wash anywhere?
6. **Legibility:** does the 2D text hold contrast over the 3D behind it?
7. **Motion between samples:** does a stream read as a dotted ribbon (not specks, not a white tube)? Do appearances and moves happen in reading order?
8. **Honesty:** is every quantity the viewer needs stated in 2D text?

Patch the field that controls a failed check: clipping → `distance`/`fov`; label clash → `position`; wash → `count`/`intensity`/`spread`.

## 9. Worked examples

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
