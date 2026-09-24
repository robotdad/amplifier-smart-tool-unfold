"""Capability skill content; rendered by library help, never by intelligence."""

# Purpose, example arguments, result, and operation-specific recovery/constraints.
EXPORT_GUIDANCE = (
    "Exact filename, matching extension and existing parent required. Spaces/Unicode are preserved; "
    "unsafe/device names are rejected. POSIX descriptor-relative hard-link publication only; "
    "Windows exact-file export is explicitly unsupported. Atomic no-replace publication follows "
    "verified staging; filesystem hard-link/fsync failures are reported, never weakened. "
    "No render, provider, overwrite or auto-numbering. Save request_id (allocated if omitted): "
    "same spelling/input recovers the retained result before preflight, without checking or repairing "
    "today's caller copy. Relative paths/parent symlinks are anchored once. Changed input conflicts. "
    "status is completed, failed or incomplete, independently of generation. CLI prints partial "
    "JSON and exits nonzero on failed/incomplete delivery. Inspect mutation-status for uncertain "
    "publication (MUTATION_INCOMPLETE); deliberately export the same artifact to a new destination/ID. "
    "Owned .unfold-RECEIPT.tmp crash leftovers are identified by the intent's staging_path, not "
    "automatically removed. Cancellation does not undo published bytes. No confinement against an "
    "unrestricted same-user process moving held directories. Legacy export DIRECTORY remains "
    "unchanged and copies directly to the final filename with weaker interruption guarantees."
)

CAPABILITY_HELP = {
    "export-file": (
        "Copy retained media to an exact caller-owned filename",
        {"artifact_id": "ARTIFACT", "destination": "/existing/exports/The convergence loop.mp4",
         "request_id": "0123456789abcdef0123456789abcdef"},
        "Separate delivery status, receipt ID, artifact/revision IDs; path and SHA-256 on success.",
        EXPORT_GUIDANCE,
    ),
    "submit-creation": (
        "Accept one owned asynchronous creation",
        {
            "brief": {"title": "A handoff", "intent": "Explain the caller and library",
                      "output": {"resolution": "1080p"}},
            "grant": {
                "provider": "openai",
                "model": "YOUR_VISION_MODEL",
                "allow_context": True,
                "allow_frames": True,
                "vision": True,
            },
            "request_id": "0123456789abcdef0123456789abcdef",
        },
        "Retained job ID and status; poll review-state for the result. Completed job.result carries primary_artifact_id and outputs. Queued/running is not output readiness; async creation never auto-exports.",
        "Requires a prepared renderer, smart dependencies and explicit bounded disclosure authority. Duration supports 1/30–60 seconds, rounded to the nearest 30-fps frame with ties up. Exact retries never relaunch; changing input under the same request_id fails. Closing a view does not cancel work. Use cancel-job and inspect its terminal state.",
    ),
    "cancel-job": (
        "Request cancellation of owned creation or refinement",
        {"job_id": "JOB"},
        "Updated job state.",
        "A cancellation request is not proof of cleanup. Poll review-state; never automatically replay interrupted jobs.",
    ),
    "save-review-view": (
        "Share a review position without starting work",
        {"revision_id": "REVISION", "at": 1, "playing": False, "expected_version": 0},
        "Retained view with a new monotonic version.",
        "One shared view per library. Optional artifact_id must belong to the revision. Stale expected_version fails; read review-state and deliberately retry. This is presentation state, never permission or creative selection.",
    ),
    "media-info": (
        "Describe intact retained media for bounded transfer",
        {"artifact_id": "ARTIFACT"},
        "MIME type, byte size, SHA-256, safe download name and chunk limit.",
        "Only retained supported media, with no symlinks or paths escaping the library. Missing or changed bytes fail. This never renders or contacts a provider.",
    ),
    "read-artifact-chunk": (
        "Read a bounded verified media chunk",
        {"artifact_id": "ARTIFACT", "offset": 0},
        "At most 192 KiB as base64, next_offset, eof and artifact metadata.",
        "Offset must be within the exact retained artifact. Each call verifies the complete file from the same descriptor used to capture its chunk. No arbitrary local paths or URLs. This integrity-first implementation rereads the whole file for each chunk.",
    ),
    "sample-output": (
        "Inspect encoded frames without a model",
        {"artifact_id": "ARTIFACT", "times": [1, 3]},
        "Artifact hash and PNG frame paths, timestamps and hashes.",
        "Choose 1–12 times before the output ends. Each selects the containing frame; time is its exact timestamp and requested_time preserves your input. Requires FFmpeg/FFprobe and intact media. Samples do not inspect audio or continuous motion.",
    ),
    "update-asset": (
        "Update an asset's sharing declarations",
        {"asset_id": "ASSET", "rights": "redistributable"},
        "Updated asset record with integrity and resolved path.",
        "Rights are unknown, redistributable or restricted; attribution is optional text. Declare only rights you have; declarations do not verify permission. Use rename to change its name.",
    ),
    "authorize-review": (
        "Give a project a bounded refinement allowance",
        {
            "project_id": "PROJECT",
            "grant": {
                "provider": "gemini",
                "model": "YOUR_VISION_MODEL",
                "allow_context": True,
                "allow_frames": True,
                "vision": True,
            },
            "refinements": 2,
            "request_id": "0123456789abcdef0123456789abcdef",
        },
        "Retained authorization receipt when request_id is supplied; otherwise the current allowance record.",
        "Requires caller authorization. Replaces the allowance with 1–10 refinements; does not add to it or spend it. Supply a request_id when retrying across a transport: exact retries return the original receipt without restoring consumed allowance; changed input or an occupied ID fails. Read review-state for the current remaining allowance. Grant disclosure and limits apply to each accepted job. Read unfold schemas for Grant fields.",
    ),
    "review-state": (
        "Resume review and inspect durable job outcomes",
        {},
        "Projects, revisions, drafts, jobs, authority, receipts and events.",
        "Reconciles interrupted workers but never restarts spending. PROVIDER_INCOMPLETE means OpenAI stopped without automatic continuation or a larger-token retry. Inspect provider_attempt and authoring_rejected events before authorizing a new attempt.",
    ),
    "mutation-status": (
        "Read one retained non-model mutation receipt",
        {"request_id": "0123456789abcdef0123456789abcdef"},
        "The accepted payload and either completed result or explicit pending outcome.",
        "A pending receipt is an uncertain interrupted mutation, not success. Inspect it before choosing a new request identity; do not repeat its effect by guessing from current state.",
    ),
    "retain-mutation-intent": (
        "Persist one exact non-model UI operation before dispatch",
        {
            "operation": "rename",
            "request_id": "0123456789abcdef0123456789abcdef",
            "payload": {"identity": "ITEM", "name": "New name"},
        },
        "A durable pending operation intent.",
        "Use the same identity only for the exact same payload. A retained intent is admission, not proof that its effect completed.",
    ),
    "acknowledge-mutation-intent": (
        "Record delivery of a completed non-model mutation receipt",
        {"request_id": "0123456789abcdef0123456789abcdef"},
        "The corresponding intent marked acknowledged.",
        "A pending or incomplete effect cannot be acknowledged. Inspect mutation-status before deciding on a new request.",
    ),
    "retain-feedback-intent": (
        "Persist an exact comment command before its transport call",
        {
            "revision_id": "REVISION",
            "text": "Keep the dot at the line tip",
            "request_id": "0123456789abcdef0123456789abcdef",
        },
        "A durable pending comment intent with its exact target and text.",
        "This records no feedback or model work. Reuse the identity only with the exact same input, then call feedback. It supports presentation recovery after a lost response.",
    ),
    "acknowledge-feedback-intent": (
        "Record delivery of a retained comment receipt",
        {"request_id": "0123456789abcdef0123456789abcdef"},
        "The same feedback intent marked delivered.",
        "Call only after feedback returns its retained receipt. If delivery is lost, retry feedback with the same identity; it returns the original note without duplicating it.",
    ),
    "save-draft": (
        "Retain feedback without launching work",
        {
            "revision_id": "REVISION",
            "text": "Keep the dot at the line tip",
            "at": 3,
            "end": 5,
            "sequence": 1,
        },
        "Saved draft, or the already newer draft.",
        "Increase sequence monotonically per revision. Times must fit that revision. Saving does not consume authority; submit-refinement is separate.",
    ),
    "record-draft-conflict": (
        "Retain a local unsaved draft separately from a newer shared draft",
        {
            "revision_id": "REVISION",
            "conflict_id": "0123456789abcdef0123456789abcdef",
            "local": {"revision_id": "REVISION", "text": "local", "at": 0, "end": None, "sequence": 2},
            "remote": {"revision_id": "REVISION", "text": "shared", "at": 0, "end": None, "sequence": 3},
        },
        "A visible unresolved local draft conflict.",
        "This never overwrites the shared draft. Edit the local text to save a deliberate newer draft, or leave it visible for later recovery.",
    ),
    "submit-refinement": (
        "Apply feedback through the embedded agent",
        {
            "revision_id": "REVISION",
            "text": "Keep the dot at the line tip",
            "request_id": "0123456789abcdef0123456789abcdef",
            "at": 3,
        },
        "Acknowledged durable review job; poll review-state for completion and result_revision.",
        "Requires current base and authorize-review allowance. Consumes once at acceptance, even on failure. Exact retries return the same job; changed input with the same request_id fails. Optional identity_version adopts a pack version. Closing the dashboard does not stop work; use cancel-refinement.",
    ),
    "retain-refinement-intent": (
        "Persist an exact Apply command before its transport call",
        {
            "revision_id": "REVISION",
            "text": "Keep the dot at the line tip",
            "request_id": "0123456789abcdef0123456789abcdef",
            "at": 3,
        },
        "A durable pending Apply intent with its exact revision, feedback and target.",
        "This spends no allowance and starts no worker. Reuse the identity only with the exact same input, then call submit-refinement. Correct a definitely invalid draft before retaining a new intent.",
    ),
    "acknowledge-refinement-intent": (
        "Record delivery of an accepted Apply receipt",
        {"request_id": "0123456789abcdef0123456789abcdef"},
        "The same Apply intent marked delivered.",
        "Call only after submit-refinement returns its retained job. If delivery is lost, retry that exact request identity; it returns the same job without re-spending allowance.",
    ),
    "cancel-refinement": (
        "Request cancellation of a dashboard refinement",
        {"job_id": "JOB"},
        "Updated job state.",
        "Cancellation is not instantaneous. Poll review-state for a terminal outcome; do not infer cancellation from the request alone.",
    ),
    "assets": (
        "List reusable assets",
        {},
        "List of asset records with resolved paths and current integrity.",
        "Use asset to refresh a single item before use. An intact result describes the bytes at the time of the read.",
    ),
    "asset": (
        "Resolve an asset and check its bytes",
        {"asset_id": "ASSET"},
        "Asset metadata, ownership, resolved path and integrity.",
        "Changed or missing originals invalidate external references. Restore the bytes or deliberately import a new asset; never delete a supplied original.",
    ),
    "import-asset": (
        "Bring local media into the library",
        {"path": "/chosen/logo.png", "role": "image", "mode": "copy", "rights": "redistributable"},
        "New asset ID, hash, ownership and resolved path.",
        "Pass a local path, not file bytes. Maximum 256 MiB. Roles: image, video, audio, font, example, motion, recipe. copy retains managed bytes; reference depends on the original location. Neither moves/deletes originals. Rights default unknown; only redistributable assets enter pack ZIPs. Fonts must be static TTF/OTF faces up to 32 MiB; family, weight and style are read from the bytes.",
    ),
    "packs": (
        "Find reusable identities",
        {},
        "Pack records with version IDs and current_version.",
        "Inspect a version ID for guidance and asset membership. Projects pin versions; updating a pack does not change existing work.",
    ),
    "save-pack": (
        "Create a pack or save a new immutable version",
        {
            "name": "Team",
            "guidance": {"required": "Mint lines", "adaptable": "Pacing"},
            "asset_ids": [],
        },
        "Pack record with current_version.",
        "Supply pack_id to version an existing pack. Guidance is an object up to 20 KB; asset_ids must resolve to intact assets. guidance.typography maps role names to {font_asset_id, optional font_weight/font_style}; each selected font must belong to asset_ids and supply that face. ZIP import remaps role IDs. Optional prerequisites declare unresolved requirements and block creative use until resolved in a new version.",
    ),
    "duplicate-pack": (
        "Make a local variation of a pack",
        {"pack_id": "PACK", "name": "Team variant"},
        "New pack with origin observations.",
        "Assets remain library references. Inspect dependencies before removing shared assets; duplicate names are allowed and IDs remain distinct.",
    ),
    "dependencies": (
        "Check what relies on an item before removing it",
        {"identity": "ASSET_OR_PACK"},
        "Retained dependency records.",
        "Use public IDs. A dependency is a reason to retain an item, not permission to remove its files directly.",
    ),
    "remove": (
        "Remove a managed asset, pack or output",
        {"identity": "ARTIFACT"},
        "Removal receipt.",
        "Deletion changes the library. Asset/pack dependents block removal; inspect dependencies first. External originals are preserved. Removing an output retains composition source. Never delete returned paths to bypass accounting.",
    ),
    "export-pack": (
        "Share an identity version as a ZIP",
        {"version_id": "VERSION", "destination": "/chosen/team.zip"},
        "Export path, hash and export details.",
        "Destination must not exist. Only redistributable assets are included; inspect omissions and prerequisites. Export does not alter the pack or source files.",
    ),
    "inspect-pack": (
        "Validate a ZIP before offering import",
        {"path": "/chosen/team.zip"},
        "Validated manifest, content summary and archive sha256.",
        "Rejects absent/invalid manifest, unsafe paths, unexpected entries and hash failures. Limits: 256 entries, 256 MiB expanded, 1 MiB manifest, 257 MiB archive. Nothing executes or installs on inspection.",
    ),
    "import-pack": (
        "Adopt a validated ZIP into this library",
        {"path": "/chosen/team.zip", "expected_sha256": "SHA256_FROM_INSPECTION"},
        "Import receipt with local IDs and origin references.",
        "Inspect first and pass expected_sha256 to detect changes. IDs are remapped. Identical imports return the prior receipt; conflicting claims fail unless conflict is explicitly copy. Imported motion/recipe files remain inert.",
    ),
    "configure-delivery": (
        "Pin footage, audio and timing to a revision",
        {
            "revision_id": "REVISION",
            "reference_id": "VIDEO_ASSET",
            "reference_start": 2,
            "audio": [
                {"asset_id": "AUDIO_ASSET", "start": 1, "offset": 0, "duration": 4, "gain": 1}
            ],
            "cues": [{"at": 1, "text": "Narration starts"}],
        },
        "Delivery ID, dependency hashes, timing and output profile.",
        "Requires FFprobe for media. All times are seconds; trims must fit both source and composition. Up to eight audio tracks, gain 0–2. Footage fits with letterboxing; recording audio is excluded. Omit reference_id/audio for graphics alone.",
    ),
    "render-delivery": (
        "Render a pinned video or transparent overlay",
        {"delivery_id": "DELIVERY", "mode": "overlay"},
        "Saved artifact record with output profile and dependency evidence.",
        "Requires pinned backend, FFmpeg/FFprobe and intact inputs. mode video produces H.264 with selected audio; overlay produces silent ProRes 4444 MOV with alpha. Changed inputs require deliberate new delivery configuration. Browser MOV playback is not assumed.",
    ),
    "export-handoff": (
        "Package delivery for another tool",
        {"artifact_id": "ARTIFACT", "destination": "/chosen/handoff.zip"},
        "ZIP path, hash and manifest.",
        "Use a render-delivery artifact and a new destination. Includes output, separate supplied audio and timing/hashes. Overlay handoffs exclude reference footage; video output already includes its composited footage.",
    ),
    "adopt-identity": (
        "Apply an identity version to an existing composition",
        {
            "revision_id": "REVISION",
            "version_id": "VERSION",
            "grant": {
                "provider": "gemini",
                "model": "YOUR_VISION_MODEL",
                "allow_context": True,
                "allow_frames": True,
                "vision": True,
            },
        },
        "Operation record; completed status identifies a new revision.",
        "Model-backed; requires smart extra, provider credentials, backend and explicit disclosure grant. Earlier revision stays pinned. Resolve pack prerequisites first. Optional request_id protects exact retries; inspect failed operations before a new attempt.",
    ),
}

# Purpose, complete CLI example, result, constraints/recovery.
COMMAND_HELP = {
    "manifest": (
        "Discover available capabilities",
        "unfold manifest",
        "JSON descriptor and deterministic/model-backed capability classification.",
        "Needs no provider or renderer. JSON capability names are invoked through unfold call NAME.",
    ),
    "schemas": (
        "Prepare validated requests",
        "unfold schemas",
        "Brief and Grant JSON schemas and JSON capability signatures.",
        "Needs no provider. Defaults and bounds are authoritative; unknown Brief/Grant fields are rejected.",
    ),
    "backend-package": (
        "Obtain pinned renderer dependencies",
        "unfold backend-package",
        "npm package manifest JSON.",
        "Needs no provider. Write only to a new dedicated backend directory, then install with npm. See unfold --help for setup; this command does not install anything.",
    ),
    "doctor": (
        "Check local prerequisites",
        "unfold doctor",
        "Library path, backend checks, provider credential-presence booleans and selected environment-variable names (never values).",
        "Does not authenticate or select a provider/model. Gemini uses the first nonempty GEMINI_API_KEY, then GOOGLE_API_KEY. The same precedence applies to workers. Grant.provider and Grant.model remain explicit. Never paste credentials into a brief.",
    ),
    "projects": (
        "Find work to reopen",
        "unfold projects",
        "Project records and revision IDs.",
        "Reads retained state without a model. Use inspect on an ID to resolve its details.",
    ),
    "inspect": (
        "Inspect retained work and integrity",
        "unfold inspect REVISION",
        "Record and, for revisions, resolved artifacts and source integrity.",
        "ID can identify a retained record, including an operation. Inspection is passive: a recorded running state is not a fresh liveness check. Use reconcile OPERATION after launcher loss. Changed/missing bytes invalidate checks; inspection never repairs or starts model work.",
    ),
    "observe": (
        "Discover changes since a caller's last visit",
        "unfold observe --after 0",
        "Ordered durable events with cursors.",
        "Persist the returned cursor for continuation. Reads do not launch or reconcile work; use inspect to resolve referenced IDs. A quiet log does not prove a worker stopped. Use reconcile OPERATION to settle interrupted execution without paid replay.",
    ),
    "rename": (
        "Rename a project, pack, asset or output",
        'unfold rename ARTIFACT "Agent loop"',
        "Updated record.",
        "IDs and bytes remain stable; names may repeat. Previously exported copies are unchanged. No rerender is needed.",
    ),
    "export": (
        "Copy saved media to a caller-owned directory",
        "unfold export ARTIFACT /chosen/exports",
        "Exported path and integrity information.",
        "Requires intact output and refuses overwrite. Choose another destination/name if occupied. This is a media copy; use call export-pack or export-handoff for ZIPs.",
    ),
    "export-file": (
        "Copy saved media to an exact filename without generation",
        "unfold export-file ARTIFACT '/existing/exports/The convergence loop.mp4' --request-id 0123456789abcdef0123456789abcdef",
        "Delivery status, receipt_id, artifact_id, revision_id; path and SHA-256 on success.",
        EXPORT_GUIDANCE,
    ),
    "render": (
        "Render retained source again without intelligence",
        "unfold render REVISION",
        "New saved artifact.",
        "Requires intact source and pinned renderer/FFmpeg dependencies. Preserves native 1080p or 720p source dimensions; missing settings in legacy source mean 720p. No upscale or automatic repair; no provider is required.",
    ),
    "feedback": (
        "Retain a targeted comment without spending",
        'unfold feedback REVISION "Make the handoff clearer"',
        "Pending feedback record.",
        "Does not start revision work. Use revise with an explicit grant or call submit-refinement with prior project authority to apply feedback.",
    ),
    "address-feedback": (
        "Link submitted feedback to its result",
        "unfold address-feedback FEEDBACK REVISION",
        "Updated feedback record.",
        "Result revision must carry the same feedback and original base. Does not launch work or claim human approval.",
    ),
    "cancel": (
        "Request cancellation of a synchronous creative operation",
        "unfold cancel OPERATION",
        "Operation with cancellation requested.",
        "cancelling is a request, not completed cleanup. Inspect the operation; run reconcile OPERATION to stop only birth-verified owned processes. Cleanup denial remains cancelling/cleanup_status=pending for another reconciliation. A vanished execution root means interrupted/cleanup_status=unverified, not proof its reparented descendants stopped. Unknown legacy ownership is reported rather than signalled. For dashboard jobs use call cancel-job and review-state.",
    ),
    "reconcile": (
        "Recover a direct-create or revise operation without replaying generation",
        "unfold reconcile OPERATION",
        "Retained operation: completed, failed, cancelled, interrupted, or active with a recovery explanation.",
        "No model calls or new execution. A live launcher with a live supervisor or bounded live worker remains active unless cancellation was requested. Supervisor loss checks the execution child separately, even with a live launcher. Cleanup denial stays active and recoverable; a vanished execution root yields interrupted with unverified descendant cleanup, never a cancelled guarantee. After worker exit, validate and commit its retained result once, or record interruption with uncertain external calls disclosed. Prior terminal outcomes are unchanged. PID birth mismatch never signals the replacement process; unknown ownership remains unresolved. Legacy PID-only records cannot safely prove ownership. inspect and observe remain passive. Use cancel then reconcile to stop verified work; repeated reconcile never launches or overwrites completed work.",
    ),
    "create": (
        "Animate a new explanation",
        "unfold create --brief brief.json --grant grant.json --request-id 0123456789abcdef0123456789abcdef",
        "Operation record; completed status includes project_id, revision_id, primary_artifact_id and outputs (artifact, revision, role, format, SHA-256, suggested_filename). Optional --export-to FILE / library create(export_to=...) adds a separate export result; failed copying never relabels successful generation.",
        "Model-backed. Requires smart extra, selected provider key, vision-capable model and renderer. Brief.output is {\"resolution\":\"1080p\"} (native 1920×1080, new-work default) or {\"resolution\":\"720p\"} (1280×720). Settings are retained per revision, not global. Durations support 1/30–60 seconds, rounded to the nearest 30-fps frame with ties up; the default stays 20 seconds. Brief context contains actual text, not paths to discover. Grant explicitly allows context/frame disclosure and bounds work. Exact retries with the same 32 lowercase hexadecimal request ID do not spend again; inspect uncertain outcomes before a fresh request. Optional --export-to FILE preflights before NEW generation and binds a separate copy receipt. Destination never enters the brief/provider payload. Keep the returned operation id and export.receipt_id. Changed destination conflicts; adding export to an old no-export request requires export-file instead. A pending copy after committed generation can resume deterministically; uncertain copying cannot replay. No export for uncommitted production. " + EXPORT_GUIDANCE,
    ),
    "revise": (
        "Apply a change while retaining the earlier composition",
        'unfold revise REVISION --feedback "Keep the dot at the line tip" --grant grant.json',
        "Operation record; completed status identifies the new revision.",
        "Model-backed with the same prerequisites/disclosure as create. Base must be current and intact. Optional --request-id protects exact retries. A stale base requires reviewing the latest revision and adapting feedback, not silently retargeting it.",
    ),
    "dashboard": (
        "Open a local review workspace",
        "unfold dashboard --port 0",
        "One JSON object containing a private loopback viewer URL; process remains running.",
        "Open that URL in a browser. Ctrl-C stops the service; closing the tab does not. The dashboard cannot grant spending authority: call authorize-review separately. Keep the viewer token private. Port 0 chooses an available port.",
    ),
    "call": (
        "Invoke the JSON capability adapter",
        "unfold call packs --args - <<'JSON'\n{}\nJSON",
        "The chosen capability's JSON result.",
        "Use unfold call NAME --help for its skill or -h for the adapter flag reference. --args accepts a JSON object file or - for stdin. Paths refer to the tool's machine. Closed or invalid stdin fails without prompting.",
    ),
}
