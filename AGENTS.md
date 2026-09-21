# Working on Unfold

Read [the vision](docs/VISION.md) and [the contracts](contracts/README.md) before
planning or changing product behavior. The contracts remain drafts. The packaged
SMART_TOOL.md documents the implemented slice; do not infer full product support
or acceptance from either the vision or packaging conformance alone.

## Architecture

- The library is the product. Every externally useful domain capability belongs
  there; the CLI and dashboard adapt arguments, presentation and I/O.
- Use Amplifier Agent for model-backed execution behind the library boundary.
  Imports, help and deterministic operations must not initialize intelligence.
- Implement mechanical work in code: asset management, revision records, packaging,
  frame extraction, rendering existing compositions, validation and observations.
- HyperFrames is the first production backend. Keep its commands and internal
  machinery out of the public caller contract. Do not build a renderer framework,
  universal animation format or migration system in anticipation of another backend.
- Internal production tools need not be public commands. The intelligence must
  nevertheless have scoped ways to author, inspect, render, repair and submit work.
- Sound generation is outside the initial scope. Support imported audio, timing
  and relevant asset handoffs without silently invoking generation services.

## Documents and changes

- Keep vision and contracts DRAFT until the owner explicitly locks them. Explain
  discrepancies between promises and implementation rather than quietly weakening
  the documents. Propose changes to locked promises separately.
- Preserve the broad creative scope. Acceptance examples are obligations to prove,
  not an exhaustive catalog of requests or a fixed sequence of specialists.
- Do not freeze command names, schemas or storage layout merely to make planning
  look complete. Establish those through concrete caller and installation examples.
- Keep tracked files focused on deliverables and durable contributor guidance.
  Put research, deliberations, trial media, scratch scripts and local review records
  in gitignored `.work/` or caller-owned storage. Never depend on private trial files.
- Keep document indexes to links; requirements belong in their governing documents.
  Write the user-facing README when working behavior can be documented. Keep
  intermediate plans and phased delivery notes in `.work/`, not the vision.
- Keep credentials, local viewer secrets, generated stores and private footage out
  of Git. Preserve upstream attribution and licenses when reusing code or assets.
- Keep runtime state outside the installation tree; use documented per-user
  defaults or explicitly selected library/output destinations.
- Package production guidance, prompts and required resources for installed use.
  Update library-owned help and manifest documentation with capability changes.

## Verification

- Define expected behavior independently before running acceptance scenarios.
  Check library behavior and adapter semantics, not just happy-path CLI output.
- Inspect actual rendered media. File existence, a model's completion message,
  schema checks and sampled stills establish different things; report their limits.
- Exercise identity ZIP exchange and continuation on a fresh store without original
  absolute paths, caches, provider sessions or the creator's conversation.
- Test deterministic paths with credentials absent and no agent initialization.
  Live provider trials require applicable bounded authority; fixtures do not prove
  creative quality or real model performance.
- Run the upstream Smart Tools conformance kit when a distribution exists. Report
  its packaging checks separately from Unfold's product acceptance scenarios.
- Keep patches scoped and preserve unrelated work. Commit or push when requested.

## Develop from this checkout

Use Python 3.12+ and uv. Start with `uv sync`; use `uv sync --extra smart` only
when exercising model-backed work. Run the CLI as `uv run unfold` from the checkout.
`uv run unfold --help` owns installation and renderer setup instructions; do not
copy them into a second contributor reference. Keep test stores and generated
media in `.work/` or temporary directories.

```sh
uv run ruff check src tests
uv run pytest -q
```

For renderer-backed tests, prepare a dedicated backend as described by the usage
skill and supply its absolute path:

```sh
UNFOLD_TEST_BACKEND=/absolute/path/to/backend uv run pytest -q
uv build
```

Without that backend, renderer-dependent tests may skip; report skips explicitly.
Run the upstream [conformance kit](https://github.com/microsoft/amplifier-smart-tools)
against the extracted distribution with the built wheel installed and its `unfold`
executable on PATH. Do not point it at a working tree containing other installed
copies in `.work/`: those are not distribution contents. The kit needs no provider.
Conformance does not establish creative quality or replace product tests.

## Documentation ownership

- README.md is for people deciding whether and how to use Unfold. Lead with the
  experience, an example request, prerequisites and honest current limits.
- This file is for contributors. Keep architecture rules, checkout commands and
  verification requirements here rather than in the README.
- `src/unfold/SMART_TOOL.md` owns the top-level installed usage skill.
  `src/unfold/capability_help.py` owns capability examples, results and recovery;
  `help.py` renders those skills with current library signatures. The CLI supplies
  its argument reference and must not reimplement domain guidance.
- Every `--help` entry point returns a usage skill; `-h` stays a short argument
  reference. Update capability help with behavior changes, and keep examples
  consistent with the public library. Help must work without credentials, model
  initialization, or creation of a library directory.

## Optional MCP adapter verification

```sh
uv sync --extra mcp
npm ci --prefix mcp-app
npm run build --prefix mcp-app
uv run ruff check src tests
uv run pytest -q
# Optional real-browser check (Chromium installation is explicit):
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q tests/test_mcp_browser.py
# Deterministic retained playback fixture, never a creative-quality claim:
.venv/bin/python tests/mcp_fixtures.py /temporary/retained-library
```

The adapter tests exercise the official SDK, actual stdio reopen, typed grants,
model permission gates, exact retry/cancellation recovery, shared drafts/views,
scoped byte integrity, and symlink/path replacement boundaries without paid model
calls. The independent browser fixture uses the official AppBridge, verifies real
decoded MP4 playback/seek, user/agent shared state, quiet polling, durable reopen,
and a visible unsupported-host error. Native renderer tests additionally require
`UNFOLD_TEST_BACKEND` as documented in `AGENTS.md`. Regenerate the bundled resource
and bundled dependency license notice after changing `mcp-app/`; Node is a build
dependency only. `npm ci` reproduces the SDK bundle pinned in package-lock.json.
Media navigation regressions must cover failed loads after a valid preview: old
video/image sources and download links must not remain under the new revision.
The MCP App is built from the native `dashboard.html`, `dashboard.css`,
`theme.js` and `dashboard.js`; do not reintroduce an independent portable page or
controller. Verify the generated resource has the native controls at matched inner
viewports, and keep the MCP transport limited to opaque checked asset/artifact/
prepared-download bytes rather than browser-visible paths or loopback URLs.
MCP upload staging must use descriptor-relative no-follow opens, regular-file identity
checks and a durable serialized record update. Do not reintroduce path strings into
App-visible records or a whole-file rehash on every bounded resource chunk.
Pack preview/import must consume the checked server-owned snapshot, not reopen an
inspected staging pathname. Preserve finite expiry/count/byte cleanup for staging,
preview snapshots and prepared transfers, while keeping records needed to recover an
explicitly incomplete effect.

## Provider budget regressions

`tests/test_provider_budget.py` uses scripted HTTP responses with the real OpenAI
provider and SDK, plus real offline worker/supervisor execution. The provider budget
CI job installs the exact revision from `unfold.agent.PROVIDERS` and runs these
checks without credentials or paid calls. Locally the optional provider tests may
skip if Amplifier Core or the OpenAI provider is absent; report those skips and
install the pinned provider to validate a change at that boundary. Preserve the
non-streaming request seam and recheck it when updating the provider pin. Tool
schemas expose constraints, but deterministic scene validation remains authoritative.
