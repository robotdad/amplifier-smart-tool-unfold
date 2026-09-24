"""Public capability registry used by thin JSON CLI and dashboard adapters."""

# An explicit allowlist, never arbitrary getattr from untrusted UI input.
CAPABILITIES = {
    "export-file": "export_file",
    "submit-creation": "submit_creation",
    "cancel-job": "cancel_job",
    "save-review-view": "save_review_view",
    "media-info": "media_info",
    "read-artifact-chunk": "read_artifact_chunk",
    "sample-output": "sample_output",
    "update-asset": "update_asset",
    "authorize-review": "authorize_review",
    "review-state": "review_state",
    "mutation-status": "mutation_status",
    "retain-mutation-intent": "retain_mutation_intent",
    "acknowledge-mutation-intent": "acknowledge_mutation_intent",
    "save-draft": "save_draft",
    "record-draft-conflict": "record_draft_conflict",
    "retain-feedback-intent": "retain_feedback_intent",
    "acknowledge-feedback-intent": "acknowledge_feedback_intent",
    "submit-refinement": "submit_refinement",
    "retain-refinement-intent": "retain_refinement_intent",
    "acknowledge-refinement-intent": "acknowledge_refinement_intent",
    "cancel-refinement": "cancel_refinement",
    "assets": "assets",
    "asset": "asset",
    "import-asset": "import_asset",
    "packs": "packs",
    "save-pack": "save_pack",
    "duplicate-pack": "duplicate_pack",
    "dependencies": "dependencies",
    "remove": "remove",
    "export-pack": "export_pack",
    "inspect-pack": "inspect_pack",
    "import-pack": "import_pack",
    "configure-delivery": "configure_delivery",
    "render-delivery": "render_delivery",
    "export-handoff": "export_handoff",
    "adopt-identity": "adopt_identity",
}


def invoke(library, capability, arguments):
    from .models import UnfoldError

    if capability not in CAPABILITIES or not isinstance(arguments, dict):
        raise UnfoldError("INVALID_INPUT", "Unknown capability or non-object arguments.")
    return getattr(library, CAPABILITIES[capability])(**arguments)


def signature(method):
    import inspect

    value = inspect.signature(method)
    return value.replace(parameters=[p for p in value.parameters.values() if p.name != "self"])
