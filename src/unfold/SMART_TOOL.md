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
  - windows
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
    purpose: On Ubuntu 24.04, HyperFrames requires a zip archiver and headless Chromium shared libraries for rendering.
    install: src/unfold/SMART_TOOL.md#linux-renderer-prerequisites
    optional: true
---
# Unfold

The Python library is the product. The CLI and optional loopback dashboard adapt
the same operations. Compositions are 1280×720 at 30 fps, lasting one frame (1/30 second) through 60 seconds.
The authoring profile supports text, cards, paths, polygons, circles, arcs, image
assets, stroke drawing, equal-point-count path morphing and camera motion. An embedded Amplifier Agent creates and
refines compositions; deterministic operations manage assets, packs and delivery.

Requested durations are rounded to the nearest whole 30-fps frame, with exact
half-frame ties rounded up, and stored as frame count / 30. A 2-second bumper is
60 frames; 2.5 seconds is 75; 2.52 seconds becomes 76 frames (2.533333… seconds).
Values below 1/30 second or above 60 seconds are rejected before rounding. The
general-purpose default remains 20 seconds. New briefs, scenes, review bounds,
video/overlay artifacts and handoffs share this canonical duration. Encoded
`frame_count` verifies that span; container duration displays can round to their
clock precision. Existing retained source keeps its authored timing on re-render.
Sampling chooses the frame containing each requested time; returned `time` is the
frame timestamp and `requested_time` preserves the request. Times must precede
the end; the final frame starts at duration minus 1/30 second.

Custom typography uses imported static TrueType (`.ttf`) and OpenType (`.otf`)
faces up to 32 MiB each. Family, numeric weight and style are read from the file.
Collections, web fonts, variable fonts and SVG fonts are rejected; supply a static
TTF/OTF face instead. Text/card elements select `font_asset_id`, optional
`font_weight` (1–1000), `font_style` (normal/italic/oblique), `letter_spacing`
(pixels, default 0) and `line_height` (multiplier, default 1.22). A selected file
must provide the exact weight/style and every non-whitespace character. No synthetic
bold/italic or silent font substitution is used. Omitting weight/style uses that
file's face; compositions without custom typography retain the system sans-serif.

Import each required face with `import-asset --role font`, then include its asset ID
in an identity's `asset_ids`. Assign roles inside pack guidance, for example
`{"typography":{"display":{"font_asset_id":"FONT_ASSET_ID"},"heading":{"font_asset_id":"BOLD_ITALIC_ASSET_ID","font_weight":700,"font_style":"italic"}}}`.
Roles guide the authoring agent; they are not automatic styles applied to every text
node. Font bytes stay local; the permitted agent receives metadata and sampled frames.
Rendered scenes retain and hash their used font dependencies and validate them with
the renderer's browser. Capture waits for font loading. Text stays editable and can
use the existing element animations. Retained scenes re-render without original
font paths; selected identity versions still require their pinned assets for new
model-backed work.

Identity ZIP imports remap role asset IDs to the new library. Identity exports and
delivery handoffs include only fonts declared redistributable and retain attribution;
unknown/restricted faces are listed as omissions. An identity with omitted faces has
unresolved prerequisites and cannot be used until repaired in a new version. Handoffs
contain rendered media and eligible font dependencies, not a full editable project.
Newly authored scenes retain the then-current rights declarations; handoffs also honor
any stricter current asset declaration. Declarations are supplied
by the caller, not a license verification service.

Studio review includes full-width Single, synchronized Compare, retained drafts,
bounded direct refinement, cancellation and observable outcomes. Identity ZIPs carry
guidance and eligible assets. Delivery supports silent transparent ProRes 4444 MOV
and H.264 MP4 with optional reference footage and imported audio.

Closed polygon paths can animate individual corner angles on a circular track,
with attached corner markers. This supports irregular shapes resolving into
regular polygons without corners leaving the track.

This is not the entire draft vision. Arbitrary HTML/CSS, external source-edit adoption,
editable project ZIP round trips, transcription, audio generation and renderer
migration are not supported. Custom typography requires supplied static font faces;
web/variable fonts and automatic font substitution are not supported. Reusable
motion/recipe files are stored as inert assets, never executed on import.

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

### Linux renderer prerequisites

On Ubuntu 24.04, install the unzip utility and Chromium shared libraries before
rendering. Package names differ on other Linux distributions:

```sh
sudo apt-get install unzip libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0 libatspi2.0-0t64 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

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

`max_response_tokens` is a per-response ceiling. OpenAI calls use non-streaming
requests with no automatic truncation continuation or raised-token recovery.
`PROVIDER_INCOMPLETE` ends the operation without committing a revision. Simplify
its brief or explicitly authorize a new operation; retrying the same request ID
returns the retained failure. `RESOURCE_LIMIT` can also mean an internal provider
request was blocked before transmission.

`model_call` events count entries through Unfold's model gate. OpenAI additionally
records `provider_attempt` events before each request, including its token ceiling;
at most one attempt is permitted per gate call. Revision usage includes
`provider_attempts` for OpenAI; it is null for other providers, whose internal
attempts are not measured by this counter. These are attempt counts, not billed
token usage. Events remain available when an operation fails.

Scene validation errors are repairable within the remaining grant. The first
three rejected author/patch payloads per operation are retained in local
`operations/OPERATION/rejected-CALL.json` diagnostics, each capped at 64 KiB, with
original byte count/hash and a truncation flag. Files are created with owner-only
permissions on POSIX; Windows uses the library directory's ACL. They may contain
supplied creative material. Public events contain a diagnostic reference and
field-level errors, not the rejected payload. Diagnostics are not exported or
sent back to providers automatically and remain until that operation's local
workspace is removed. Validation does not guarantee creative quality.

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
Worker status checks do not send signals. Cleanup stops the owned worker tree;
recovery checks the exact worker command and request path before stopping it.
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
