---
smart_tool_format: 1
name: unfold
version: 0.1.0.dev0
description: >-
  Create, review, reuse and deliver motion graphics using embedded Amplifier Agent.
  Retain editable source, encoded output, frame evidence and revision history.
use_cases:
  - Explain a technical or mathematical idea through animated geometry and text
  - Revise an explanation while retaining the previous version
  - Review, rename and export saved outputs without a model
platforms:
  - macos
  - linux
requires:
  - name: Node.js, HyperFrames 0.8.33 and GSAP 3.14.2
    purpose: Render existing compositions; not needed for help or retained-state reads.
    install: https://github.com/heygen-com/hyperframes
    optional: true
  - name: FFmpeg and FFprobe
    purpose: Decode video observations and inspect encoded media.
    install: https://ffmpeg.org/download.html
    optional: true
  - name: Linux system libraries (unzip and Chromium dependencies)
    purpose: On Ubuntu 24.04 and similar distributions, HyperFrames requires a zip archiver and headless Chromium shared libraries for rendering.
    install: "apt-get install unzip libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0 libatspi2.0-0t64 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64"
    optional: true
    platforms:
      - linux
---
# Unfold

The Python library is the product. The CLI and optional loopback dashboard adapt
the same operations. Compositions are 1280×720 at 30 fps, lasting 5–60 seconds.
The authoring profile supports text, cards, paths, polygons, circles, arcs, image
assets, stroke drawing and camera motion. An embedded Amplifier Agent creates and
refines compositions; deterministic operations manage assets, packs and delivery.

Studio review includes full-width Single, synchronized Compare, retained drafts,
bounded direct refinement, cancellation and observable outcomes. Identity ZIPs carry
guidance and eligible assets. Delivery supports silent transparent ProRes 4444 MOV
and H.264 MP4 with optional reference footage and imported audio.

This is not the entire draft vision. Arbitrary HTML/CSS, custom-font rendering,
external source-edit adoption, editable project ZIP round trips, transcription,
audio generation and renderer migration are not supported. Fonts can be stored,
previewed and shared, but a required custom font remains a prerequisite to resolve;
no promise of font substitution is made. Reusable motion/recipe files are stored
as inert assets, never executed on import.

## Installation

Python 3.12+, Node.js and FFmpeg are prerequisites. From a checkout:

```sh
uv sync --extra smart
uv run unfold manifest
```

Install from Git:
`uv tool install "amplifier-smart-tool-unfold[smart] @ git+https://github.com/robotdad/amplifier-smart-tool-unfold"`.
The repository is private; the installing caller needs access. Without `[smart]`,
deterministic capabilities work but creative operations require installing the extra.

Install pinned renderer dependencies into a selected directory outside the Python
installation, once. For the default backend directory:

```sh
mkdir -p ~/.local/share/unfold-backend
unfold backend-package > ~/.local/share/unfold-backend/package.json
npm install --prefix ~/.local/share/unfold-backend
unfold doctor
```

Only run that redirection for a new dedicated backend directory; it writes a package
manifest. HyperFrames may prepare its Chromium binary on the first render. Creative
calls do not run npm or initiate authentication. Amplifier Agent v0.17.0 and provider
module revisions are pinned; first Agent preparation can fetch its runtime modules.
Production guidance is packaged. No private skills directory is required.

## Library and authority

```python
from unfold import Unfold, Brief, Grant

tool = Unfold(library="/chosen/library", backend="/chosen/backend")
grant = Grant(
    provider="gemini", model="gemini-3.7-flash", allow_context=True, allow_frames=True, vision=True
)
outcome = tool.create(
    Brief(
        title="A request through our system",
        intent="Show how the caller delegates to an agent and receives an artifact.",
        context="Actual component roles and relationships supplied by the caller.",
        identity="Navy, mint and gold; restrained movement.",
        duration=20,
    ),
    grant,
)
if outcome["status"] == "completed":
    revision = tool.inspect(outcome["revision_id"])
    artifact = tool.artifact(revision["artifacts"][0])
```

`Brief.context` is actual supplied content, not a filename the agent should discover.
The caller explicitly permits disclosure of brief, context, identity, feedback,
composition data and sampled generated frames to the chosen model. No unrelated
filesystem material is available to the embedded agent. This profile requires a
vision-capable model; a false capability declaration will not make a text-only model
see images. `Grant` limits calls, tool actions, renders, frames, bytes, response tokens
and wall time; `unfold schemas` exposes bounds. Models are chosen explicitly; no
model fallback or authentication is initiated. Set `GEMINI_API_KEY`, `OPENAI_API_KEY`
or `ANTHROPIC_API_KEY` for the selected provider. Credentials never enter scene data.

The embedded agent authors validated scene data. Code emits executable HyperFrames
source; arbitrary model JavaScript, remote assets and custom CSS are not accepted.
The agent can render, sample encoded frames, repair and submit within one shared
allowance. A model completion sentence alone is not a result. Sampled-frame model
review is labeled and is not continuous playback inspection or human approval.

State defaults to `~/.local/share/unfold`; select `library` explicitly to change it.
The backend defaults to `~/.local/share/unfold-backend`. Both paths belong to the
process running Unfold. Output references identify Unfold-managed bytes; export
creates a new caller-owned copy and refuses overwrite. Large input files are passed
by path to `import_asset`, not embedded in JSON. The default is a managed copy;
`mode="reference"` keeps an external dependency. Neither mode moves or deletes the
original. Use `remove` for managed deletion, never delete returned paths as an API.

## Capabilities

All results are JSON-compatible dictionaries/lists; validated creative inputs are
`Brief` and `Grant` objects or matching dictionaries. Errors use `UnfoldError` with
code, message and remedy. CLI stdout is JSON; diagnostics use stderr, and errors or
failed/cancelled operations exit nonzero. Closed stdin never triggers a prompt.
Use library return values for chaining. `--help` prints this skill, `-h` prints a
synopsis. Every subcommand’s `--help` is a capability skill with arguments, examples,
results and recovery guidance; `-h` is its short flag reference.

Global CLI options `--library PATH` and `--backend PATH` precede the subcommand.

- `create(brief, grant, request_id=None)` / `create --brief brief.json --grant grant.json`:
  model-backed. Returns a retained operation; completion identifies the revision.
- `revise(revision_id, feedback, grant, request_id=None)` /
  `revise REVISION --feedback TEXT --grant grant.json`: model-backed. Requires the
  current intact base. Creates a new revision; preserves the earlier one.
- `projects()` / `projects`: list retained projects and revision IDs.
- `inspect(id)` / `inspect ID`: records, resolved artifacts, source integrity,
  feedback, grant and checks as applicable. Reading never starts model work.
- `artifact(id)`: resolve a saved output's current name, path, ownership and integrity.
- `observe(after=0)` / `observe --after CURSOR`: ordered durable changes with cursors.
- `rename(id, name)` / `rename ID NAME`: rename project, pack, asset or saved output without
  rerendering. Labels may repeat; IDs are unique. Downloads use the current name.
- `export(artifact_id, directory)` / `export ARTIFACT DIRECTORY`: verified media copy,
  no overwrite. Previously exported copies are not renamed or deleted.
- `render(revision_id)` / `render REVISION`: deterministic re-render of intact source;
  returns a new saved output without model initialization. No automatic repair.
- `feedback(revision_id, text)` / `feedback REVISION TEXT`: save a pending, targeted
  comment. This operation never launches work; `submit_refinement` is the separate
  authority-consuming operation.
- `address_feedback(feedback_id, revision_id)` / `address-feedback FEEDBACK REVISION`:
  link a submitted comment to the revision carrying its exact text and original base.
  Records the outcome without claiming human approval or triggering model work.
- `cancel(operation_id)` / `cancel OPERATION`: request cancellation; status remains
  `cancelling` until the active caller stops the worker and records the outcome.
- `doctor()` / `doctor`: report prerequisite versions and credential presence only.
- `dashboard(port=0)` / `dashboard [--port PORT]`: start a loopback, cookie-protected
  review service. Returned object has `url`, `close()` and context-manager support.
  CLI prints the private viewer URL, waits, and stops on Ctrl-C. No browser opens
  implicitly. Dashboard plays/scrubs, compares, retains drafts, applies authorized feedback,
  manages packs and exports. Composition source is never executed in the dashboard.
- `unfold.help.manifest()`, `schemas()`, `skill()`, `backend_package()` correspond
  to CLI `manifest`, `schemas`, `--help`, `backend-package` and need no provider.

Creative operations are synchronous but their operation record and ordered progress
are readable from another process. Supply a 32-character lowercase hexadecimal
`request_id` (CLI `--request-id`) for acknowledged retry protection: exact retries
return the prior state, including running/failed states, without spending again.
Different input with the same ID fails. After caller/process loss a record may remain
running; this means uncertain interruption, not permission to retry automatically.
Direct synchronous calls need a live supervisor for cancellation. Dashboard jobs
reconcile completed or interrupted supervisors on `review_state`; they never restart
spending automatically. A new creative attempt requires a new request identity.

Retained source is native to this backend profile. Unexpected edits/missing files
are reported; they do not silently inherit checks. Do not treat SQLite tables or
directory naming as a supported caller protocol. Use public IDs and file references.
The full vision and contracts remain draft; packaging conformance is separate from
creative quality, motion correctness and review acceptance.

## Studio feedback

```python
tool.authorize_review(project_id, grant, refinements=2)  # 1–10 bounded operations
# These remain deterministic and never spend:
tool.save_draft(revision_id, "Move the callout; preserve pacing", at=3, end=5, sequence=1)
state = tool.review_state()
# Returns an acknowledged durable job; work survives closing the dashboard.
job = tool.submit_refinement(
    revision_id,
    "Move the callout; preserve pacing",
    request_id="0123456789abcdef0123456789abcdef",
    at=3,
    end=5,
)
tool.cancel_refinement(job["id"])
```

Draft sequence numbers are monotonically increasing per revision; older saves do
not overwrite newer ones. Apply consumes one allowance at acceptance, even if the
provider later fails. Exact retries return the same job without spending. Failed
or interrupted jobs require explicit new authorization when the allowance is used
up. Feedback on an old base is refused without moving or deleting the draft.
`review_state` exposes jobs, receipts, revisions, drafts, authority and events.
The dashboard cannot grant itself more authority. It inherits the launch process's
selected provider credential. Grant settings are snapshots, not secrets.

## Assets and packs

```python
logo = tool.import_asset("/chosen/logo.png", role="image", rights="redistributable")
pack = tool.save_pack(
    "Team",
    {"required": "Mint arrows; accurate labels", "adaptable": "Pacing follows the explanation"},
    asset_ids=[logo["id"]],
)
version = pack["current_version"]
# Pass identity_version=version in Brief, or deliberately adopt it:
# tool.adopt_identity(revision_id, version, grant, request_id=...)
exported = tool.export_pack(version, "/chosen/team.zip")
checked = tool.inspect_pack(exported["path"])
receipt = tool.import_pack(exported["path"], expected_sha256=checked["sha256"])
```

- `assets()`, `asset(asset_id)`, `packs()` and `inspect(version_id)` discover retained
  records, hashes, resolved paths and integrity. PNG/JPEG images from the selected
  identity can be used by the agent; renderer copies are decoded/re-encoded PNGs.
- `save_pack(name, guidance, asset_ids=[], pack_id=None, prerequisites=[])` creates
  a pack or a new immutable version. `guidance` is an object (up to 20 KB), typically
  `required`, `adaptable`, `examples`. Declared unresolved prerequisites block
  creative use; resolving them requires an explicit new version. Guidance is passed
  to the model; arbitrary prose requirements are not mechanically proven.
- `duplicate_pack(pack_id, name)` creates a local variation with origin observations.
  `adopt_identity(revision_id, version_id, grant, request_id=None)` creates a new
  model-backed revision, leaving the earlier revision pinned. Dashboard Adopt uses
  an existing review allowance through `submit_refinement(identity_version=...)`.
- `update_asset(asset_id, rights=None, attribution=None)` records declarations.
  Rights are `unknown` (default), `redistributable`, or `restricted`. ZIP export
  includes only explicitly redistributable assets; everything else is listed as
  an omission. Unfold does not verify legal permission. No account, sender or user
  profile is collected or automatically included.
- `dependencies(identity)` lists retained dependents. `remove(identity)` supports
  assets, packs and generated outputs, refuses retained asset/pack dependencies,
  preserves external originals and reports missing files. Removing an output leaves
  its composition source available. Names are labels; IDs, bytes and references
  survive renaming, including names that happen to be identical.
- ZIP inspection validates a mandatory manifest, supported format, hashes, declared
  contents and safe entries before exposing a contents view. Limits: 256 entries,
  256 MiB uncompressed, 1 MiB manifest, 257 MiB archive on disk. Individual asset
  intake is limited to 256 MiB. No extraction executes scripts or installs fonts.
- Import remaps to local identities and records origin identities. Repeated identical
  imports return the previous receipt; conflicting version claims fail.
  `import_pack(..., conflict="copy")` explicitly imports a separate variation.
  Original absolute paths, credentials and library caches are excluded from ZIPs.

## Footage, audio and output

`Brief(reference_id=asset_id, reference_start=seconds, cues=[...])` permits the
agent to inspect up to three footage samples relative to the composition, within
the frame/disclosure allowance. Sampling gaps remain unobserved; no tracking or
source authentication is claimed. Keep cue statements explicit. Existing footage
is referenced as input, never treated as proof that illustrative graphics happened.

```python
reference = tool.import_asset("/chosen/demo.mp4", role="video", mode="reference")
audio = tool.import_asset("/chosen/narration.wav", role="audio")
delivery = tool.configure_delivery(
    revision_id,
    reference_id=reference["id"],
    reference_start=2,
    audio=[{"asset_id": audio["id"], "start": 1, "offset": 0, "duration": 4, "gain": 1}],
    cues=[{"at": 1, "text": "Narration starts"}],
)
overlay = tool.render_delivery(delivery["id"], mode="overlay")
video = tool.render_delivery(delivery["id"], mode="video")
tool.export_handoff(overlay["id"], "/chosen/handoff.zip")
frames = tool.sample_output(video["id"], [1, 3, 5])
```

`configure_delivery` pins hashes and a revision. Times use composition seconds;
`reference_start + composition_time` maps to the recording. Footage scales to fit
1280×720 with letterboxing. Source trimming and audio placement are explicit; no
motion tracking, cropping or region transforms are currently exposed. Up to eight
imported audio tracks can be trimmed, placed and mixed with gain 0–2. Video audio
is 48 kHz stereo AAC with limiting; reference recording audio is excluded. This
profile neither transcribes nor asks a model to listen to imported audio.

`render_delivery(..., mode="overlay")` removes the canvas background, retaining
colored shapes/images, and renders silent ProRes 4444 MOV with real alpha. The
full-frame layer starts at composition time zero. MOV playback in the browser is
not assumed. `mode="video"` composites the same layer over the selected footage
(or composition background) and includes selected audio. Both require intact source
and dependency hashes; neither uses intelligence. The output profile is explicit,
not inferred from a file extension.

`export_handoff` creates a ZIP with the chosen output, separate supplied audio and
a timing manifest with hashes. An overlay handoff never contains reference footage
or recording audio. Combined Video deliberately includes footage in its encoded
output. `sample_output` decodes 1–12 PNG frames without a model. Samples prove neither
continuous temporal quality nor audible quality. Outputs remain independently
traceable to source revision, profile, audio policy and dependency hashes.

The theme icon cycles System → Light → Dark. System is the default and follows
live operating-system appearance changes. An explicit override persists in the
browser without changing composition colors or triggering model work.

Dashboard Save invokes the browser's native save picker when available. Browsers
without that API use their normal download flow and configured destination; Unfold
does not simulate an OS chooser or invent a second destination dialog.

## JSON capability adapter

Every operation above is a public `Unfold` method. The CLI also provides
`unfold call CAPABILITY --args arguments.json` (`--args -` reads stdin).
Capability names use hyphens: `import-asset`, `save-pack`, `review-state`,
`authorize-review`, `submit-refinement`, `configure-delivery`, `render-delivery`,
`export-handoff`, and so forth. Arguments are the corresponding method's named
parameters in a JSON object. `unfold schemas` lists exact signatures, and
`unfold manifest` classifies deterministic versus model-backed capabilities.


Read `unfold call --help` for the complete JSON capability index and
`unfold call CAPABILITY --help` before invoking one. Direct command skills:

- `unfold manifest --help` — Discover available capabilities.
- `unfold schemas --help` — Prepare validated requests.
- `unfold backend-package --help` — Obtain pinned renderer dependencies.
- `unfold doctor --help` — Check local prerequisites.
- `unfold projects --help` — Find work to reopen.
- `unfold inspect --help` — Inspect retained work and integrity.
- `unfold observe --help` — Discover changes since a caller's last visit.
- `unfold rename --help` — Rename a project, pack, asset or output.
- `unfold export --help` — Copy saved media to a caller-owned directory.
- `unfold render --help` — Render retained source again without intelligence.
- `unfold feedback --help` — Retain a targeted comment without spending.
- `unfold address-feedback --help` — Link submitted feedback to its result.
- `unfold cancel --help` — Request cancellation of a synchronous creative operation.
- `unfold create --help` — Animate a new explanation.
- `unfold revise --help` — Apply a change while retaining the earlier composition.
- `unfold dashboard --help` — Open a local review workspace.
- `unfold call --help` — Invoke the JSON capability adapter.

## Optional standard MCP adapter

Install `[mcp]` and configure `unfold-mcp --library /absolute/retained-library` as a
stdio server. `unfold-mcp --help` describes the transport; no MCP dependency is
needed for the ordinary library/CLI. `ui://unfold/review` is a portable MCP App
using the same typed `unfold_*` actions as the model. Media uses bounded standard
resources, never credential-bearing browser URLs. The review needs serverTools,
serverResources and updateModelContext support from its host.

Default server mode denies model-backed creation/refinement. For those, install
`[smart,mcp]`, prepare the renderer, and explicitly enable `--allow-models` with
provider credentials supplied in the server environment. Grants remain required
per operation. Use submit-creation for owned asynchronous creation and
authorize-review plus submit-refinement for bounded follow-up. Exact request_id
retries never relaunch; closing the view never cancels accepted work. Poll
review-state and inspect cancellation/interruption rather than replaying work.

One shared view position is retained per library; drafts are revision-bound
context and grant no authority. The first portable view covers project/revision
review, retained media playback/download, notes and interval refinements, bounded
creation and job cancellation. It does not replace every standalone dashboard or
delivery control. Preview assembly is limited to 32 MiB; use export for larger
media. No MCP sampling/Tasks or browser credential configuration is provided.
