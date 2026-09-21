const $ = (id) => document.getElementById(id),
  E = (tag, value, cls) => {
    const e = document.createElement(tag);
    if (value !== undefined) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  };
let state,
  projectId,
  revisionId,
  compareId,
  comparison = false,
  packId,
  packVersionId,
  draftTimer,
  viewTimer,
  draftSequence = Date.now(),
  draftGeneration = 0,
  draftDirty = false,
  selectionGeneration = 0,
  viewVersion = 0,
  pollTimer,
  destroyed = false,
  submissionId = null,
  submissionPayload = null,
  submissionPending = false,
  commentPayload = null,
  commentPending = false,
  draftConflict = null,
  loadedDraft = null,
  loadedDraftSignature = null,
  playerSignature = null,
  deliverySignature = null,
  librarySignature = null,
  statusSignature = null,
  playbackIntent = { revision_id: null, at: 0, playing: false, version: 0 },
  suppressDraftConflictNotice = false;
const acknowledgedIntents = new Set();
// The native dashboard owns presentation and interaction.  The optional MCP App
// supplies this narrow adapter before this script runs; the loopback dashboard
// continues to use same-origin HTTP unchanged.
const transport = window.unfoldTransport;
const requestId = () =>
  crypto.randomUUID?.().replaceAll("-", "") ||
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
const remembered = (key) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const remember = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Sandboxed MCP Apps have no origin storage. The retained library still owns work.
  }
};
const notice = (m) => ($("notice").textContent = m);
const guarded =
  (fn) =>
  async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      notice(e.message);
    }
  };
async function api(path, data) {
  if (transport?.api) return transport.api(path, data);
  const r = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
  );
  const v = await r.json();
  if (!r.ok) {
    const error = Error(
      typeof v.error === "string"
        ? v.error
        : v.error?.message || JSON.stringify(v),
    );
    error.definitive = true;
    throw error;
  }
  return v;
}
const call = (capability, args) => api("/call", { capability, arguments: args });
function source(path) {
  return transport?.mediaUrl ? transport.mediaUrl(path) : Promise.resolve(path);
}
function releaseElementMedia(element) {
  const url = element?.dataset?.unfoldMediaUrl;
  if (url && transport?.releaseMedia) transport.releaseMedia(url);
  if (element?.dataset) delete element.dataset.unfoldMediaUrl;
}
function releaseMediaWithin(root) {
  root?.querySelectorAll?.("[data-unfold-media-url]").forEach(releaseElementMedia);
}
function setSource(element, path, generation = selectionGeneration) {
  releaseElementMedia(element);
  const requested = path;
  Promise.resolve(source(path))
    .then((url) => {
      if (
        !destroyed &&
        generation === selectionGeneration &&
        element.isConnected &&
        element.dataset.unfoldSource === requested
      ) {
        element.src = url;
        element.dataset.unfoldMediaUrl = url;
        transport?.retainMedia?.(url);
      } else if (transport?.releaseMedia) transport.releaseMedia(url);
    })
    .catch((error) => {
      if (
        !destroyed &&
        generation === selectionGeneration &&
        element.isConnected &&
        element.dataset.unfoldSource === requested
      ) {
        element.removeAttribute("src");
        notice(error.message);
      }
    });
  element.dataset.unfoldSource = requested;
}
const current = () => state?.revisions.find((r) => r.id === revisionId);
const project = () => state?.projects.find((p) => p.id === projectId);
const revisions = () =>
  state?.revisions.filter((r) => r.project_id === projectId) || [];
const draftSnapshot = () => ({
  revision_id: revisionId,
  text: $("feedback").value,
  at: Number($("at").value),
  end: $("end").value === "" ? null : Number($("end").value),
  generation: draftGeneration,
  sequence: ++draftSequence,
});
function sameDraft(snapshot, saved) {
  return (
    saved?.revision_id === snapshot.revision_id &&
    saved?.text === snapshot.text &&
    Number(saved?.at) === Number(snapshot.at) &&
    (saved?.end ?? null) === (snapshot.end ?? null) &&
    Number(saved?.sequence) === Number(snapshot.sequence)
  );
}
function selectedArtifact() {
  return current()?.resolved_artifacts?.[0]?.id || null;
}
function publishContext() {
  transport?.publishContext?.({
    revision_id: revisionId || null,
    artifact_id: selectedArtifact(),
    playback_seconds: Number($("scrub").value),
    feedback_draft: $("feedback").value.slice(0, 2000),
    feedback_draft_truncated: $("feedback").value.length > 2000,
    draft_is_authority: false,
  });
}
function option(select, id, name) {
  const o = E("option", name);
  o.value = id;
  select.append(o);
}
function fill(select, items, value) {
  select.replaceChildren();
  items.forEach((i) => option(select, i.id, i.name));
  select.value = value || "";
}
function button(name, fn, title) {
  const b = E("button", name);
  b.onclick = guarded(fn);
  if (title) b.title = title;
  return b;
}
function show(page) {
  document
    .querySelectorAll(".screen")
    .forEach((e) => (e.hidden = e.id !== page));
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("on", b.dataset.page === page));
  $("crumb").textContent = "Project / " + page[0].toUpperCase() + page.slice(1);
  if (page === "delivery") drawDelivery();
  if (page === "library") drawLibrary();
}
document
  .querySelectorAll("nav button")
  .forEach((b) => (b.onclick = () => show(b.dataset.page)));
function toggle(name) {
  const open = $(name + "Drawer").hidden;
  $(name + "Drawer").hidden = !open;
  $(name + "Toggle").classList.toggle("on", open);
  $(name + "Toggle").setAttribute("aria-expanded", String(open));
}
$("feedbackToggle").onclick = () => toggle("feedback");
$("historyToggle").onclick = () => toggle("history");
function revisionName(r) {
  return (
    "Revision " +
    (project()?.revisions.indexOf(r.id) + 1) +
    " · " +
    r.id.slice(0, 8)
  );
}
function signature(value) {
  return JSON.stringify(value);
}
function playerStateSignature() {
  return signature({
    projectId,
    revisionId,
    compareId,
    comparison,
    selected: (comparison ? [compareId, revisionId] : [revisionId]).map((id) => {
      const revision = state?.revisions.find((item) => item.id === id);
      const artifact = revision?.resolved_artifacts?.[0];
      return [id, artifact?.id, artifact?.sha256, artifact?.integrity];
    }),
  });
}
function deliveryStateSignature() {
  return signature(
    (state?.outputs || [])
      .filter((output) => output.revision_id === revisionId)
      .map((output) => [
        output.id,
        output.name,
        output.sha256,
        output.integrity,
        output.delivery_id,
      ]),
  );
}
function setPlaybackIntent(view) {
  const at = Number(view?.at);
  playbackIntent = {
    revision_id: view?.revision_id || revisionId,
    at: Number.isFinite(at) ? at : 0,
    playing: Boolean(view?.playing),
    version: Number(view?.version || viewVersion || 0),
  };
  if (playbackIntent.revision_id === revisionId) {
    $("scrub").value = playbackIntent.at;
    $("time").textContent = playbackIntent.at.toFixed(2) + "s";
    syncPlayerPlayback();
  }
}
function syncVideoPlayback(video, generation = selectionGeneration) {
  if (
    generation !== selectionGeneration ||
    !video.isConnected ||
    playbackIntent.revision_id !== revisionId ||
    !Number.isFinite(video.duration)
  )
    return;
  const at = Math.min(playbackIntent.at, video.duration);
  if (Math.abs(video.currentTime - at) > 0.04) video.currentTime = at;
  if (playbackIntent.playing && video.paused) video.play().catch(() => {});
  if (!playbackIntent.playing && !video.paused) video.pause();
}
function syncPlayerPlayback() {
  document
    .querySelectorAll("#players video")
    .forEach((video) => syncVideoPlayback(video));
}
function drawPlayers() {
  const generation = selectionGeneration;
  releaseMediaWithin($("players"));
  $("players").replaceChildren();
  $("players").classList.toggle("compare", comparison);
  $("single").classList.toggle("on", !comparison);
  $("compare").classList.toggle("on", comparison);
  (comparison ? [compareId, revisionId] : [revisionId])
    .filter(Boolean)
    .forEach((id, index) => {
      const r = state.revisions.find((r) => r.id === id);
      if (!r) return;
      const p = E("div", undefined, "pane"),
        s = E("select");
      s.setAttribute(
        "aria-label",
        comparison && index === 0 ? "Compare revision" : "Viewed revision",
      );
      revisions().forEach((r) => option(s, r.id, revisionName(r)));
      s.value = id;
      s.onchange = guarded(async () => {
        if (comparison && index === 0) {
          compareId = s.value;
          drawPlayers();
        } else await chooseRevision(s.value);
      });
      p.append(s);
      const a = r.resolved_artifacts?.[0];
      if (a && a.integrity === "intact") {
        const v = E("video");
        v.controls = true;
        v.preload = "metadata";
        v.muted = comparison && index === 0;
        v.onplay = () => {
          if (comparison)
            document.querySelectorAll("#players video").forEach((other) => {
              if (other !== v && other.paused) {
                other.currentTime = Math.min(v.currentTime, other.duration);
                other.play().catch((e) => notice(e.message));
              }
            });
        };
        v.onpause = () => {
          if (comparison)
            document.querySelectorAll("#players video").forEach((other) => {
              if (other !== v && !other.paused) other.pause();
            });
        };
        v.onseeked = () => {
          if (comparison)
            document.querySelectorAll("#players video").forEach((other) => {
              const time = Math.min(v.currentTime, other.duration);
              if (
                other !== v &&
                Number.isFinite(time) &&
                Math.abs(other.currentTime - time) > 0.08
              )
                other.currentTime = time;
            });
        };
        setSource(v, "/media/" + a.id, generation);
        v.onloadedmetadata = () => {
          syncVideoPlayback(v, generation);
        };
        v.ontimeupdate = () => {
          if (!comparison || index === 1) {
            if (playbackIntent.revision_id === revisionId && !v.seeking)
              playbackIntent = {
                ...playbackIntent,
                at: v.currentTime,
                playing: !v.paused,
              };
            if (comparison && !v.paused)
              document.querySelectorAll("#players video").forEach((other) => {
                if (
                  other !== v &&
                  !other.seeking &&
                  Math.abs(other.currentTime - v.currentTime) > 0.08
                )
                  other.currentTime = Math.min(v.currentTime, other.duration);
              });
            $("scrub").value = v.currentTime;
            $("time").textContent = v.currentTime.toFixed(2) + "s";
          }
        };
        v.onerror = () =>
          notice(
            "This output could not be played. Inspect its integrity in History.",
          );
        p.append(v);
      } else p.append(E("div", "Output missing or changed.", "empty"));
      p.append(
        E("div", revisionName(r) + " · " + (a?.name || "No output"), "caption"),
      );
      $("players").append(p);
    });
  const duration = current()?.brief.duration || 60;
  for (const id of ["scrub", "at", "end"]) $(id).max = duration;
  $("selection").textContent = current()
    ? revisionName(current())
    : "No revision";
  $("feedbackTarget").textContent = current()
    ? revisionName(current()) + " · composition seconds"
    : "";
  loadDraft();
  drawStatus();
  publishContext();
  playerSignature = playerStateSignature();
}
async function chooseRevision(id) {
  const target = state.revisions.find((revision) => revision.id === id);
  if (!target) throw Error("That retained revision is no longer available.");
  await flushCurrentDraft();
  selectionGeneration++;
  revisionId = target.id;
  projectId = target.project_id;
  remember("unfold.revision", target.id);
  loadedDraft = null;
  loadedDraftSignature = null;
  compareId = project()?.revisions.find((candidate) => candidate !== revisionId) || revisionId;
  $("title").textContent = project()?.name || "No projects yet";
  drawPlayers();
  drawDelivery();
  await saveReviewView();
}
async function flushCurrentDraft() {
  while (revisionId) {
    const before = draftSnapshot();
    const acknowledged = await saveDraft(before);
    if (!acknowledged)
      throw Error(
        "A shared draft won this save. Your local text remains visible; edit it to save a new draft before changing review.",
      );
    // Keep taking exact old-target snapshots until no input arrived during any
    // awaited save. Navigation never turns that input into a new-target draft.
    if (
      before.generation === draftGeneration &&
      before.text === $("feedback").value &&
      before.at === Number($("at").value) &&
      before.end === ($("end").value === "" ? null : Number($("end").value))
    )
      return;
  }
}
function loadDraft() {
  const d = state.drafts.find((d) => d.revision_id === revisionId);
  const conflict = (state.draft_conflicts || [])
    .filter((item) => item.status === "open" && item.revision_id === revisionId)
    .at(-1);
  if (conflict && (!draftConflict || draftConflict.id !== conflict.id)) {
    draftConflict = conflict;
    loadedDraft = revisionId;
    loadedDraftSignature = "conflict:" + conflict.id;
    $("feedback").value = conflict.local.text;
    $("at").value = conflict.local.at;
    $("end").value = conflict.local.end ?? "";
    draftSequence = Math.max(
      Date.now(),
      (conflict.local.sequence || 0) + 1,
      (conflict.remote.sequence || 0) + 1,
    );
    draftGeneration++;
    draftDirty = true;
    $("draftStatus").textContent =
      "Shared draft conflict · local changes remain unsaved. Edit to save a new draft.";
    return;
  }
  const nextSignature = signature([
    revisionId,
    d?.text || "",
    d?.at || 0,
    d?.end ?? null,
    d?.sequence || 0,
    d?.submitted_job || null,
  ]);
  if (loadedDraft === revisionId && loadedDraftSignature === nextSignature) return;
  if (loadedDraft === revisionId && draftDirty) {
    // A caller's newer draft is visible as a real conflict, never silently
    // overwritten beneath current typing.
    if (!suppressDraftConflictNotice)
      notice("A shared draft changed while you have unsaved input.");
    return;
  }
  loadedDraft = revisionId;
  loadedDraftSignature = nextSignature;
  $("feedback").value = d?.text || "";
  $("at").value = d?.at || 0;
  $("end").value = d?.end ?? "";
  $("draftStatus").textContent = d?.submitted_job
    ? "Submitted · " + d.submitted_job.slice(0, 8)
    : "Unsubmitted draft";
  draftSequence = Math.max(Date.now(), (d?.sequence || 0) + 1);
  draftGeneration++;
  draftDirty = false;
  draftConflict = null;
}
async function saveDraft(body = draftSnapshot()) {
  clearTimeout(draftTimer);
  if (!body.revision_id) return;
  if (body.revision_id === revisionId && body.generation === draftGeneration)
    $("draftStatus").textContent = "Saving…";
  const saved = await api("/draft", {
    revision_id: body.revision_id,
    text: body.text,
    at: body.at,
    end: body.end,
    sequence: body.sequence,
    resolve_conflict_id: draftConflict?.id || null,
  });
  state.drafts = state.drafts
    .filter((d) => d.revision_id !== body.revision_id)
    .concat(saved);
  const acknowledged = sameDraft(body, saved);
  if (
    revisionId === body.revision_id &&
    body.generation === draftGeneration &&
    $("feedback").value === body.text &&
    Number($("at").value) === Number(body.at) &&
    ($("end").value === "" ? null : Number($("end").value)) === (body.end ?? null)
  ) {
    if (acknowledged) {
      $("draftStatus").textContent = saved.submitted_job
        ? "Submitted · " + saved.submitted_job.slice(0, 8)
        : "Draft retained · not submitted";
      draftDirty = false;
      if (draftConflict) {
        state.draft_conflicts = (state.draft_conflicts || []).filter(
          (item) => item.id !== draftConflict.id,
        );
        draftConflict = null;
      }
    } else {
      const conflict = {
        id: draftConflict?.id || requestId(),
        revision_id: body.revision_id,
        local: {
          revision_id: body.revision_id,
          text: body.text,
          at: body.at,
          end: body.end,
          sequence: body.sequence,
        },
        remote: saved,
        status: "open",
      };
      await call("record-draft-conflict", {
        revision_id: body.revision_id,
        local: conflict.local,
        remote: saved,
        conflict_id: conflict.id,
      });
      draftConflict = conflict;
      state.draft_conflicts = (state.draft_conflicts || [])
        .filter((item) => item.id !== conflict.id)
        .concat(conflict);
      draftDirty = true;
      $("draftStatus").textContent =
        "Shared draft conflict · local changes remain unsaved. Edit to save a new draft.";
    }
  }
  return acknowledged;
}
async function saveReviewView(flush = false) {
  if (!revisionId || (destroyed && !flush)) return;
  const payload = {
    revision_id: revisionId,
    artifact_id: selectedArtifact(),
    at: Number($("scrub").value),
    playing: [...$("players").querySelectorAll("video")].some((video) => !video.paused),
    expected_version: viewVersion,
    theme: document.documentElement.dataset.theme || "system",
  };
  try {
    const saved = await api("/view", payload);
    viewVersion = saved.version;
    publishContext();
  } catch (error) {
    if (error.definitive) {
      refresh().catch(() => {});
      return;
    }
    throw error;
  }
}
["feedback", "at", "end"].forEach(
  (id) =>
    ($(id).oninput = () => {
      if (!submissionPayload) {
        submissionId = null;
        submissionPayload = null;
      }
      if (!commentPayload) commentPayload = null;
      draftGeneration++;
      draftDirty = true;
      $("draftStatus").textContent = "Unsaved changes";
      clearTimeout(draftTimer);
      // A retained Apply has immutable feedback already. Later typing is a
      // distinct unsaved draft, not an implicit save that can race its exact
      // receipt retry and replace the user-visible outcome.
      if (!submissionPayload) draftTimer = setTimeout(guarded(saveDraft), 350);
    }),
);
$("apply").onclick = guarded(async () => {
  const retrying = Boolean(submissionPayload);
  const action =
    submissionPayload ||
    {
      revision_id: revisionId,
      text: $("feedback").value,
      at: Number($("at").value),
      end: $("end").value === "" ? null : Number($("end").value),
      request_id: submissionId || requestId(),
    };
  submissionId = action.request_id;
  submissionPayload = action;
  const snapshot = {
    ...action,
    generation: draftGeneration,
    sequence: ++draftSequence,
  };
  try {
    if (!retrying) {
      await saveDraft(snapshot);
      await call("retain-refinement-intent", action);
    }
  } catch (error) {
    if (error.definitive) {
      submissionId = null;
      submissionPayload = null;
    }
    throw error;
  }
  submissionPending = true;
  let result;
  try {
    // Never reread mutable presenter state after an await: this exact action
    // survives navigation, remount recovery, and a lost response.
    result = await api("/refine", action);
    await call("acknowledge-refinement-intent", { request_id: action.request_id });
    acknowledgedIntents.add(action.request_id);
    submissionId = null;
    submissionPayload = null;
  } catch (error) {
    if (error.definitive) {
      submissionId = null;
      submissionPayload = null;
    }
    throw error;
  } finally {
    submissionPending = false;
  }
  $("draftStatus").textContent = "Submitted · " + result.id.slice(0, 8);
  notice(
    "Feedback accepted · " +
      result.id.slice(0, 8) +
      ". Your viewed revision stays selected.",
  );
  suppressDraftConflictNotice = true;
  try {
    await refresh();
  } finally {
    suppressDraftConflictNotice = false;
  }
});
$("note").onclick = guarded(async () => {
  const snapshot = draftSnapshot();
  await saveDraft(snapshot);
  const action =
    commentPayload ||
    {
      revision_id: snapshot.revision_id,
      text: snapshot.text,
      request_id: requestId(),
    };
  commentPayload = action;
  await call("retain-feedback-intent", action);
  let n;
  commentPending = true;
  try {
    n = await api("/feedback", action);
    await call("acknowledge-feedback-intent", { request_id: action.request_id });
    acknowledgedIntents.add(action.request_id);
    commentPayload = null;
  } catch (error) {
    if (error.definitive) commentPayload = null;
    throw error;
  } finally {
    commentPending = false;
  }
  notice("Comment retained · " + n.id.slice(0, 8) + ". No refinement started.");
  await refresh();
});
$("single").onclick = () => {
  comparison = false;
  drawPlayers();
};
$("compare").onclick = () => {
  comparison = true;
  if (!compareId || compareId === revisionId) {
    const index = revisions().findIndex((r) => r.id === revisionId);
    compareId = revisions()[Math.max(0, index - 1)]?.id || revisionId;
  }
  drawPlayers();
};
$("latest").onclick = guarded(() => chooseRevision(project().current_revision));
$("project").onchange = guarded(async () => {
  const target = state.projects.find((project) => project.id === $("project").value);
  if (!target?.current_revision) throw Error("This project has no retained revision.");
  await chooseRevision(target.current_revision);
  $("scrub").value = 0;
});
$("play").onclick = guarded(async () => {
  setPlaybackIntent({
    revision_id: revisionId,
    at: Number($("scrub").value),
    playing: true,
    version: viewVersion,
  });
  const videos = [...$("players").querySelectorAll("video")];
  for (const v of videos) {
    v.currentTime = Math.min(Number($("scrub").value), v.duration);
    await v.play();
  }
});
$("pause").onclick = () => {
  setPlaybackIntent({
    revision_id: revisionId,
    at: Number($("scrub").value),
    playing: false,
    version: viewVersion,
  });
  document.querySelectorAll("#players video").forEach((v) => v.pause());
};
$("scrub").oninput = () => {
  setPlaybackIntent({
    revision_id: revisionId,
    at: Number($("scrub").value),
    playing: false,
    version: viewVersion,
  });
  document.querySelectorAll("#players video").forEach((v) => {
    v.pause();
    v.currentTime = Number($("scrub").value);
  });
  $("time").textContent = Number($("scrub").value).toFixed(2) + "s";
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => guarded(saveReviewView)(), 200);
};
function drawStatus() {
  const evidenceOpen = $("history").querySelector("details")?.open || false;
  const a = state.authorities.find((a) => a.project_id === projectId);
  $("allowance").textContent = a
    ? `${a.remaining} authorized refinements remaining · ${a.grant.provider} / ${a.grant.model}`
    : "Caller authorization is needed before Apply.";
  $("apply").disabled = (!a?.remaining && !submissionId) || !current();
  $("latest").hidden =
    !project()?.current_revision || project().current_revision === revisionId;
  $("jobs").replaceChildren();
  state.jobs
    .filter((j) => j.project_id === projectId)
    .reverse()
    .forEach((j) => {
      const e = E("div", undefined, "event");
      e.append(E("b", j.status), E("p", j.text), E("small", j.id.slice(0, 8)));
      const err = j.error || j.result?.error;
      if (err)
        e.append(
          E(
            "p",
            typeof err === "string" ? err : err.message + " " + err.remedy,
          ),
        );
      if (["queued", "running", "cancelling"].includes(j.status))
        e.append(
          button("Cancel", async () => {
            await api("/cancel", { job_id: j.id, request_id: requestId() });
            await refresh();
          }),
        );
      if (j.result?.revision_id)
        e.append(button("Open", () => chooseRevision(j.result.revision_id)));
      $("jobs").append(e);
    });
  $("history").replaceChildren();
  const detail = E("details");
  detail.open = evidenceOpen;
  detail.append(
    E("summary", "Revision evidence"),
    E("pre", JSON.stringify(current(), null, 2)),
  );
  $("history").append(detail);
  state.events
    .filter(
      (e) =>
        e.subject === projectId ||
        revisions().some(
          (r) => r.id === e.subject || r.operation_id === e.subject,
        ) ||
        e.data?.project_id === projectId ||
        state.jobs.some(
          (j) =>
            j.project_id === projectId &&
            (j.id === e.subject || j.operation_id === e.subject),
        ),
    )
    .slice(-30)
    .reverse()
    .forEach((e) => {
      const d = E("div", undefined, "event");
      d.append(
        E("b", e.kind.replaceAll("_", " ")),
        E("small", " · " + new Date(e.time * 1000).toLocaleTimeString()),
        E("pre", JSON.stringify(e.data, null, 2)),
      );
      $("history").append(d);
    });
  statusSignature = signature({
    projectId,
    revisionId,
    authority: state.authorities.find((a) => a.project_id === projectId),
    jobs: state.jobs.filter((job) => job.project_id === projectId),
    events: state.events.filter(
      (event) => event.subject === projectId || event.data?.project_id === projectId,
    ),
  });
}
function modal(title) {
  $("dialogBody").replaceChildren();
  const head = E("div", undefined, "row spread");
  head.append(
    E("h2", title),
    button("Close", () => $("dialog").close()),
  );
  $("dialogBody").append(head);
  $("dialog").showModal();
  return $("dialogBody");
}
function rename(id, name) {
  const body = modal("Rename"),
    input = E("input");
  input.value = name;
  input.setAttribute("aria-label", "Name");
  body.append(
    input,
    button("Save", async () => {
      await api("/rename", { id, name: input.value, request_id: requestId() });
      $("dialog").close();
      await refresh();
      drawLibrary();
    }),
  );
}
$("renameProject").onclick = () =>
  project() && rename(projectId, project().name);
function preview(a, url) {
  let e;
  if (a.mime?.startsWith("image/") && a.mime !== "image/svg+xml") {
    e = E("img");
    setSource(e, url);
    e.alt = a.name;
  } else if (a.mime?.startsWith("audio/")) {
    e = E("audio");
    e.controls = true;
    setSource(e, url);
  } else if (a.mime?.startsWith("video/")) {
    e = E("video");
    e.controls = true;
    e.preload = "metadata";
    setSource(e, url);
  } else {
    e = E("div", a.role === "font" ? "Aa Bb Cc 123" : a.role, "empty");
    if (a.role === "font") {
      const family = "asset" + a.id;
      source(url)
        .then((resolved) => new FontFace(family, "url(" + resolved + ")").load())
        .then((f) => {
          document.fonts.add(f);
          e.style.fontFamily = family;
          e.style.fontSize = "24px";
        })
        .catch(() => (e.textContent = "Font preview unavailable"));
    }
  }
  return e;
}
function assetCard(a) {
  const card = E("div", undefined, "asset");
  card.append(
    preview(a, "/asset/" + a.id),
    E("b", a.name),
    E("small", a.role + " · " + a.ownership),
    E("small", a.integrity),
  );
  const row = E("div", undefined, "row");
  row.append(
    button("Rename", () => rename(a.id, a.name)),
    button("Details", () => assetDetails(a)),
    button("Remove", () => remove(a.id)),
  );
  card.append(row);
  return card;
}
function assetDetails(a) {
  const b = modal("Asset");
  b.append(
    E("h3", a.name),
    E("p", a.ownership + " · " + a.integrity),
    E("label", "Redistribution"),
  );
  const rights = E("select");
  ["unknown", "redistributable", "restricted"].forEach((v) =>
    option(rights, v, v),
  );
  rights.value = a.rights;
  b.append(rights, E("label", "Attribution (optional)"));
  const attribution = E("textarea");
  attribution.value = a.attribution;
  b.append(
    attribution,
    E(
      "p",
      "These are your declarations. Unfold does not verify rights; unknown or restricted assets are omitted from identity ZIPs.",
      "muted",
    ),
    button("Save", async () => {
      await call("update-asset", {
        asset_id: a.id,
        rights: rights.value,
        attribution: attribution.value,
        request_id: requestId(),
      });
      $("dialog").close();
      await refresh();
      drawLibrary();
    }),
  );
}
async function remove(id) {
  const deps = await call("dependencies", { identity: id });
  const b = modal("Remove");
  b.append(
    E(
      "p",
      deps.length
        ? "This item is used by retained work. Removal is blocked."
        : "Remove this managed item? External originals remain untouched.",
    ),
    E("pre", deps.length ? JSON.stringify(deps, null, 2) : ""),
  );
  if (!deps.length)
    b.append(
      button("Remove", async () => {
        await call("remove", { identity: id, request_id: requestId() });
        $("dialog").close();
        await refresh();
        drawLibrary();
      }),
    );
}
function guidanceView(guidance) {
  const body = E("div");
  Object.entries(guidance).forEach(([key, value]) => {
    if (value) {
      body.append(
        E("h3", key[0].toUpperCase() + key.slice(1)),
        E("p", typeof value === "string" ? value : JSON.stringify(value)),
      );
    }
  });
  return body;
}
function drawLibrary() {
  $("packs").replaceChildren();
  state.packs.forEach((p) => {
    const b = button(p.name, () => {
      packId = p.id;
      packVersionId = null;
      drawLibrary();
    });
    b.className = "pack" + (p.id === packId ? " on" : "");
    const v = state.versions.find((v) => v.id === p.current_version);
    b.append(E("small", ` · v${v?.number} · ${v?.assets.length} assets`));
    const shell = E("div", undefined, "card");
    shell.style.marginBottom = "10px";
    shell.append(b);
    const first = v?.assets
      .map((id) => state.assets.find((a) => a.id === id))
      .find(Boolean);
    if (first) shell.append(preview(first, "/asset/" + first.id));
    $("packs").append(shell);
  });
  $("assets").replaceChildren(...state.assets.map(assetCard));
  const p = state.packs.find((p) => p.id === packId);
  if (!p) {
    $("packDetail").replaceChildren(
      E("h2", "Selected pack"),
      E("p", "Select or create a pack.", "muted"),
    );
    return;
  }
  if (!p.versions.includes(packVersionId)) packVersionId = p.current_version;
  const v = state.versions.find((v) => v.id === packVersionId),
    body = $("packDetail");
  body.replaceChildren(E("small", "Selected pack"));
  const row = E("div", undefined, "row spread"),
    actions = E("div", undefined, "row");
  actions.append(
    button("Edit", () => editPack(p, v)),
    button("Export", () => exportPack(v)),
    button("Copy", async () => {
      const result = await call("duplicate-pack", {
        pack_id: p.id,
        name: p.name + " copy",
        request_id: requestId(),
      });
      packId = result.id;
      await refresh();
      drawLibrary();
    }),
    button("Remove", () => remove(p.id)),
  );
  row.append(E("h2", p.name), actions);
  body.append(row, E("p", "Version " + v.number), guidanceView(v.guidance));
  const versionPicker = E("select");
  versionPicker.setAttribute("aria-label", "Pack version");
  p.versions.forEach((id) => {
    const version = state.versions.find((v) => v.id === id);
    option(
      versionPicker,
      id,
      "Version " +
        version.number +
        (id === p.current_version ? " · latest" : ""),
    );
  });
  versionPicker.value = v.id;
  versionPicker.onchange = () => {
    packVersionId = versionPicker.value;
    drawLibrary();
  };
  body.append(versionPicker);
  if (v.prerequisites.length)
    body.append(
      E("p", "Unresolved prerequisites"),
      E("pre", JSON.stringify(v.prerequisites, null, 2)),
    );
  const gallery = E("div", undefined, "gallery");
  v.assets.forEach((id) => {
    const a = state.assets.find((a) => a.id === id);
    if (a) gallery.append(assetCard(a));
  });
  body.append(gallery);
  const pin = current()?.identity_version;
  body.append(
    E(
      "p",
      pin
        ? "Viewed revision uses identity " + pin.slice(0, 8)
        : "Viewed revision has no selected identity.",
      "muted",
    ),
  );
  body.append(
    button(
      "Adopt",
      async () => {
        const b = modal("Adopt");
        b.append(
          E(
            "p",
            "Apply " +
              p.name +
              " v" +
              v.number +
              " to the viewed composition? This uses one authorized refinement and creates a new reviewable revision.",
          ),
          button("Apply", async () => {
            await api("/refine", {
              revision_id: revisionId,
              text: "Adopt this identity version, preserving the explanation, timing and unrelated choices.",
              identity_version: v.id,
              request_id: requestId(),
            });
            $("dialog").close();
            notice(
              "Identity adoption accepted. The viewed revision remains unchanged.",
            );
            await refresh();
          }),
        );
      },
      "Use this exact pack version in a new composition revision",
    ),
  );
  body.append(E("small", "Editing a pack leaves existing projects unchanged."));
}
function editPack(p, v) {
  const b = modal(p ? "Edit" : "Create");
  function input(id, title, value, multi = false) {
    const l = E("label", title);
    l.htmlFor = id;
    b.append(l);
    const e = E(multi ? "textarea" : "input");
    e.id = id;
    e.value = value || "";
    b.append(e);
    return e;
  }
  const name = input("packName", "Name", p?.name),
    required = input("required", "Required", v?.guidance.required, true),
    adapt = input("adaptable", "Adaptable", v?.guidance.adaptable, true),
    examples = input("examples", "Examples", v?.guidance.examples, true);
  b.append(E("h3", "Assets"));
  const choices = [];
  state.assets.forEach((a) => {
    const l = E("label", undefined, "check"),
      c = E("input");
    c.type = "checkbox";
    c.checked = v?.assets.includes(a.id) || false;
    choices.push([c, a.id]);
    l.append(c, E("span", a.name + " · " + a.role));
    b.append(l);
  });
  b.append(
    button("Save", async () => {
      const result = await call("save-pack", {
        name: name.value,
        guidance: {
          required: required.value,
          adaptable: adapt.value,
          examples: examples.value,
        },
        asset_ids: choices.filter(([c]) => c.checked).map(([, id]) => id),
        pack_id: p?.id || null,
        prerequisites: v?.prerequisites || [],
        request_id: requestId(),
      });
      packId = result.id;
      packVersionId = result.current_version;
      $("dialog").close();
      await refresh();
      drawLibrary();
    }),
  );
}
$("createPack").onclick = () => editPack();
async function chooseFile(accept) {
  const f = $("file");
  f.accept = accept;
  f.value = "";
  return new Promise((resolve) => {
    f.onchange = () => resolve(f.files[0]);
    f.oncancel = () => resolve(null);
    f.click();
  });
}
async function upload(file, params) {
  if (transport?.upload) return transport.upload(file, params);
  const r = await fetch(
    "/upload?" + new URLSearchParams({ name: file.name, ...params }),
    { method: "POST", body: file },
  );
  const v = await r.json();
  if (!r.ok) throw Error(v.error);
  return v;
}
$("addAsset").onclick = guarded(async () => {
  const b = modal("Add");
  b.append(
    E("p", "A managed copy will be retained; your original stays in place."),
  );
  const role = E("select");
  ["image", "video", "audio", "font", "example", "motion", "recipe"].forEach(
    (r) => option(role, r, r),
  );
  b.append(
    role,
    button("Select", async () => {
      const f = await chooseFile("");
      if (!f) return;
      await upload(f, { kind: "asset", role: role.value });
      $("dialog").close();
      await refresh();
      drawLibrary();
    }),
  );
});
$("importPack").onclick = guarded(async () => {
  const file = await chooseFile(".zip");
  if (!file) return;
  notice("Inspecting pack…");
  const result = await upload(file, { kind: "pack" });
  notice("");
  const b = modal("Import"),
    m = result.manifest,
    actions = E("div", undefined, "row");
  actions.append(
    button("Cancel", () => $("dialog").close()),
    button("Import", async () => {
      const r = await api("/import", { upload_id: result.upload_id });
      packId = r.pack_id;
      $("dialog").close();
      await refresh();
      drawLibrary();
    }),
  );
  b.append(actions, E("h3", m.name), guidanceView(m.guidance));
  const gallery = E("div", undefined, "gallery");
  m.assets.forEach((a, i) => {
    const c = E("div", undefined, "asset");
    c.append(
      preview(a, "/pack-preview/" + result.upload_id + "/" + i),
      E("b", a.name),
    );
    gallery.append(c);
  });
  b.append(gallery);
  if (m.omissions.length || m.prerequisites.length)
    b.append(
      E("h3", "Prerequisites and omissions"),
      E("pre", JSON.stringify([...m.prerequisites, ...m.omissions], null, 2)),
    );
});
async function saveURL(url, name) {
  if (transport?.save) return transport.save(url, name);
  if (window.showSaveFilePicker) {
    const handle = await window.showSaveFilePicker({ suggestedName: name });
    const response = await fetch(url);
    if (!response.ok) throw Error("Export could not be read.");
    const writable = await handle.createWritable();
    await writable.write(await response.blob());
    await writable.close();
    notice("Saved.");
  } else {
    const link = E("a");
    link.href = url;
    link.download = name;
    link.click();
    notice(
      "This browser controls the save location. Enable “ask where to save” in its download settings to choose a destination.",
    );
  }
}
async function exportPack(v) {
  const b = modal("Export");
  b.append(
    E("p", "Identity pack · guidance, eligible assets and declared omissions."),
  );
  const result = await api("/prepare", {
    kind: "pack",
    id: v.id,
    request_id: requestId(),
  });
  b.append(
    E("h3", "Included"),
    E(
      "p",
      result.manifest.assets.map((a) => a.name).join(", ") || "Guidance only",
    ),
    E("h3", "Omissions"),
    E(
      "p",
      result.manifest.omissions
        .map((a) => a.name + ": " + a.reason)
        .join("; ") || "None",
    ),
    E("h3", "Prerequisites"),
    E(
      "p",
      result.manifest.prerequisites
        .map((a) =>
          typeof a === "string" ? a : a.name || a.reason || JSON.stringify(a),
        )
        .join("; ") || "None",
    ),
    button("Save", async () => {
      await saveURL(
        "/download/" + result.id,
        (state.packs.find((p) => p.id === v.pack_id)?.name || "Unfold") +
          ".zip",
      );
      $("dialog").close();
    }),
  );
}
function drawDelivery() {
  $("exportRevision").textContent = current()
    ? revisionName(current())
    : "No revision";
  for (const [id, role] of [
    ["reference", "video"],
    ["audio", "audio"],
  ]) {
    const old = $(id).value;
    fill(
      $(id),
      [
        { id: "", name: "None" },
        ...state.assets.filter(
          (a) => a.role === role && a.integrity === "intact",
        ),
      ],
      old,
    );
  }
  releaseMediaWithin($("outputs"));
  $("outputs").replaceChildren();
  state.outputs
    .filter((a) => a.revision_id === revisionId)
    .reverse()
    .forEach((a) => {
      const c = E("div", undefined, "event");
      c.append(
        E("b", a.name),
        E(
          "p",
          `${a.profile === "overlay" ? "Overlay only" : a.profile === "video" ? "Video" : "Animation"} · ${a.format || "mp4"} · ${a.duration?.toFixed(2) || "?"}s`,
        ),
      );
      if (a.integrity !== "intact")
        c.append(E("p", "Output missing or changed.", "empty"));
      else if (a.format !== "mov") {
        const v = E("video");
        v.controls = true;
        v.preload = "metadata";
        setSource(v, "/media/" + a.id);
        c.append(v);
      } else
        c.append(
          E(
            "p",
            "ProRes 4444 · alpha. Save for a compatible compositor; browser playback is not assumed.",
            "muted",
          ),
        );
      const row = E("div", undefined, "row");
      row.append(
        button("Save", () =>
          saveURL("/media/" + a.id + "?download=1", a.download_name),
        ),
        button("Rename", () => rename(a.id, a.name)),
      );
      if (a.delivery_id)
        row.append(
          button("Handoff", async () => {
            const b = modal("Export");
            b.append(
              E(
                "p",
                "Output, separate audio assets and timing manifest. Reference footage is excluded from an overlay handoff.",
              ),
            );
            const result = await api("/prepare", {
              kind: "handoff",
              id: a.id,
              request_id: requestId(),
            });
            b.append(
              button("Save", async () => {
                await saveURL("/download/" + result.id, "Unfold handoff.zip");
                $("dialog").close();
              }),
            );
          }),
        );
      c.append(row);
      $("outputs").append(c);
    });
  deliverySignature = deliveryStateSignature();
}
async function render(mode) {
  if (!revisionId) throw Error("Select a revision first.");
  $("renderVideo").disabled = $("renderOverlay").disabled = true;
  notice(
    "Rendering " +
      (mode === "overlay" ? "transparent overlay" : "video") +
      "… You can continue reviewing.",
  );
  try {
    const d = await call("configure-delivery", {
      revision_id: revisionId,
      reference_id: $("reference").value || null,
      reference_start: Number($("referenceStart").value),
      audio: $("audio").value
        ? [
            {
              asset_id: $("audio").value,
              start: Number($("audioStart").value),
              offset: Number($("audioOffset").value),
            },
          ]
        : [],
      cues: $("cueText").value
        ? [{ at: Number($("scrub").value), text: $("cueText").value }]
        : [],
      request_id: requestId(),
    });
    const a = await call("render-delivery", {
      delivery_id: d.id,
      mode,
      request_id: requestId(),
    });
    notice("Output ready · " + a.id.slice(0, 8));
    await refresh();
    drawDelivery();
  } finally {
    $("renderVideo").disabled = $("renderOverlay").disabled = false;
  }
}
$("renderVideo").onclick = guarded(() => render("video"));
$("renderOverlay").onclick = guarded(() => render("overlay"));
function recoverPendingIntents() {
  const comments = (state.feedback_intents || []).filter(
    (intent) =>
      ["pending", "completed"].includes(intent.status) &&
      !intent.acknowledged_at &&
      !acknowledgedIntents.has(intent.request_id || intent.id) &&
      intent.payload?.revision_id === revisionId,
  );
  if (!commentPayload && comments.length === 1) {
    commentPayload = {
      ...comments[0].payload,
      request_id: comments[0].request_id || comments[0].id,
    };
    commentPending = true;
    $("draftStatus").textContent = "Pending comment retained · retry Comment";
  }
  const refinements = (state.refinement_intents || []).filter(
    (intent) =>
      ["pending", "accepted"].includes(intent.status) &&
      !intent.acknowledged_at &&
      !acknowledgedIntents.has(intent.request_id) &&
      intent.payload?.revision_id === revisionId,
  );
  if (!submissionPayload && refinements.length === 1) {
    submissionId = refinements[0].request_id;
    submissionPayload = { ...refinements[0].payload, request_id: submissionId };
    $("draftStatus").textContent =
      refinements[0].status === "accepted"
        ? "Apply accepted · retry checks its retained job"
        : "Pending Apply retained · retry Apply";
  }
}
async function refresh() {
  const next = await api("/state"),
    first = !state;
  if (destroyed) return;
  state = next;
  transport?.onState?.(state);
  const shared = state.views?.find((view) =>
    state.revisions.some((revision) => revision.id === view.revision_id),
  );
  if (!projectId) {
    const retained = remembered("unfold.revision");
    revisionId = state.revisions.some((r) => r.id === shared?.revision_id)
      ? shared.revision_id
      : state.revisions.some((r) => r.id === retained)
        ? retained
        : state.projects.find((p) => p.current_revision)?.current_revision;
    projectId =
      state.revisions.find((r) => r.id === revisionId)?.project_id ||
      state.projects[0]?.id;
    compareId = project()?.revisions.at(-2) || revisionId;
    viewVersion = shared?.version || 0;
    if (shared?.theme) {
      window.unfoldTheme?.(shared.theme);
      drawTheme();
    }
    setPlaybackIntent(shared);
  } else if (shared && shared.version > viewVersion && !draftDirty) {
    const changesSelection =
      revisionId !== shared.revision_id || projectId !== shared.project_id;
    if (changesSelection) {
      selectionGeneration++;
      revisionId = shared.revision_id;
      projectId = shared.project_id;
      compareId = project()?.revisions.at(-2) || revisionId;
      loadedDraft = null;
    }
    viewVersion = shared.version;
    if (shared.theme) {
      window.unfoldTheme?.(shared.theme);
      drawTheme();
    }
    setPlaybackIntent(shared);
  }
  fill($("project"), state.projects, projectId);
  $("title").textContent = project()?.name || "No projects yet";
  if (first) {
    if (revisionId) drawPlayers();
    else
      $("players").append(
        E(
          "div",
          "Create a project through the Unfold library or CLI to begin.",
          "empty",
        ),
      );
    packId = state.packs[0]?.id;
    drawLibrary();
    drawDelivery();
  } else {
    if (playerSignature !== playerStateSignature()) drawPlayers();
    else {
      loadDraft();
      const nextStatus = signature({
        projectId,
        revisionId,
        authority: state.authorities.find((a) => a.project_id === projectId),
        jobs: state.jobs.filter((job) => job.project_id === projectId),
        events: state.events.filter(
          (event) => event.subject === projectId || event.data?.project_id === projectId,
        ),
      });
      if (statusSignature !== nextStatus) drawStatus();
    }
    if (
      !$("delivery").hidden &&
      deliverySignature !== deliveryStateSignature()
    )
      drawDelivery();
    if (!$("library").hidden) drawLibrary();
  }
  recoverPendingIntents();
}
refresh().catch((e) => notice(e.message));
pollTimer = setInterval(
  () => refresh().catch((e) => notice("Disconnected: " + e.message)),
  3000,
);
window.unfoldOnToolResult = guarded(async (result) => {
  const target = result?.kind === "revision" ? result.id : result?.revision_id;
  if (!target || !state?.revisions.some((revision) => revision.id === target)) {
    await refresh();
    return;
  }
  if (target !== revisionId) await chooseRevision(target);
});
window.unfoldInvalidateMedia = (path) => {
  document.querySelectorAll(`[data-unfold-source="${CSS.escape(path)}"]`).forEach((element) => {
    if (element instanceof HTMLMediaElement) element.pause();
    releaseElementMedia(element);
    element.removeAttribute("src");
    element.load?.();
  });
  if (path.startsWith("/media/")) {
    if (playerSignature !== playerStateSignature()) drawPlayers();
    if (!$("delivery").hidden && deliverySignature !== deliveryStateSignature())
      drawDelivery();
  }
};
window.unfoldTeardown = async () => {
  destroyed = true;
  clearTimeout(draftTimer);
  clearTimeout(viewTimer);
  clearInterval(pollTimer);
  try {
    // The host keeps the bridge open until teardown resolves. Flush typing
    // that has not reached the debounce timer without submitting model work.
    if (draftDirty) await saveDraft();
    if (revisionId) await saveReviewView(true);
  } finally {
    releaseMediaWithin(document);
  }
};

function drawTheme() {
  const mode = document.documentElement.dataset.theme || "system";
  const next = { system: "light", light: "dark", dark: "system" }[mode];
  const label =
    "Theme: " +
    mode[0].toUpperCase() +
    mode.slice(1) +
    ". Switch to " +
    next[0].toUpperCase() +
    next.slice(1) +
    ".";
  $("theme").title = label;
  $("theme").setAttribute("aria-label", label);
  const icons = {
    system:
      '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    light:
      '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
    dark: '<path d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z"/>',
  };
  // All SVG is code-owned; no asset or model markup enters this control.
  $("theme").innerHTML =
    '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
    icons[mode] +
    "</svg>";
  $("theme").onclick = () => {
    window.unfoldTheme(next, true);
    drawTheme();
    guarded(saveReviewView)();
  };
}
drawTheme();
