# Unfold demo imagery

`unfold-made.mp4`, `unfold-made.gif`, and `unfold-made-poster.png` are a
20-second approved cut of the owner's MADE identity work, prepared on
2026-09-20. The native identity ZIP was imported through Unfold's public API
into isolated demo storage. The motion review uses a separate copy of the
original retained library. Original projects, media, and packs are unchanged.

Showrun's 25fps browser capture records the imported pack and a large playing
preview of the dark-ink title overlay. The next seven seconds composite the existing dark-ink ProRes 4444 title and corner-mark
overlays over the approved Possibly demo. This uses their actual alpha channel,
not a solid-background preview or chroma key. The old demo footer is cropped
out before fitting the footage without stretching. The final six seconds play
the approved MADE dark team bumper. Original overlay and demo sources remain
unchanged. The poster is taken from the composited demo.

This edit demonstrates retained identity guidance/assets and motion review.
It does not show a newly submitted agent request, new generation, or automatic
restoration of editable projects from the identity ZIP. The pack contains inert
scene reference data; its restricted Georgia font is omitted from ZIP exchange.
No font file or identity ZIP is published with this demo.

Vid compiles trims and footer captions. FFmpeg fits the sources into 1440×900
and applies three half-second dissolves and a closing crossfade back to the opening.
The complete six-second bumper plays before the closing fade. The silent MP4 and Outtake-rendered
960px GIF run at 25fps. Capture takes, edit plans, receipts and original-library
copies remain in ignored working storage. Browser checks cover responsive
layout, playback, looping, shared pause, reduced motion and no-JavaScript controls.

The website presents this as a secondary team-identity capability below the
main explanation/motion introduction. Captions emphasize creating a team identity
and exporting a pack so teammates can import, reuse and remix it. The pack shot
was rerecorded after fixing image previews that overflowed their cards and
Library polling that recreated video elements every three seconds. The new
asset preview opens a larger muted, looping view; closing it pauses playback.
A browser regression checks continued playback across polling and changed-asset
updates. Unsupported browser formats show an explicit unavailable message.

Both demo cuts were visually approved by the owner before publication.

## Main motion demo

`unfold-demo.mp4`, `unfold-demo.gif` and `unfold-demo-poster.png` are the
20-second main-page demo. The opening uses seconds 8–16 of the owner-approved
neural-network explanation. The second shot is a new Showrun browser recording
of the actual Unfold dashboard playing the retained From spark to system
revision. An eased zoom follows that recorded player into its Make ideas move
ending; no replacement interface or simulated agent conversation is shown.
Captions explain the calling-agent workflow, rather than claiming the video
records a new request or refinement submission.

The neural layout revision rendered through Unfold but its final provider-review
step failed. The retained rendered draft was independently inspected and approved
by the owner; it is not represented as a successfully committed Unfold revision.
The original and revised source renders and failure receipts remain in ignored
working storage. The promo uses a copy of its original library, preserving the
original review state and media.

Vid supplies trim and caption plans; FFmpeg applies the dissolve, eased zoom and
closing loop fade. Delivery is silent 1440×900 at 25fps, with a 960px GIF rendered
through Outtake. The identity demo remains the separately approved secondary
capability, unchanged by this edit.

## scene3d demo

`scene3d-pr-demo.mp4`, `scene3d-pr-demo.gif` and `scene3d-pr-demo-poster.png` are a 16-second
explanation of the scene3d layer, made with the layer itself. The composition is a hand-authored
`Scene` (2D captions plus a `scene3d` pipeline of nodes, links, particle streams, a flock
simulation, camera shots and full-screen moments) that was validated by `unfold.models`, compiled
by `Backend.author` and rendered by `Backend.render` from the feature branch, at native
1920×1080 and 30fps with two render workers on software WebGL. No model authored or reviewed it,
and it does not show a model-authored 3D request; that path is a follow-up.

The same source was rendered a second time with one worker. Decoded frames were compared with
FFmpeg `framemd5`: 480 of 480 frames are identical. The MP4 is the unmodified
two-worker render. The 960px GIF is derived from it with an FFmpeg palette pass (12fps,
96 colours, ordered dither); the poster is frame 465 (the closing pull-back). The scene script, both renders and the
comparison report remain in ignored working storage.

The cut was visually approved by the owner on 2026-09-27 before publication.

## scene3d video screens demo

`scene3d-video-screens-demo.mp4`, `scene3d-video-screens-demo.gif` and
`scene3d-video-screens-demo-poster.png` are a 16-second explanation of scene3d video screens, made
with the feature itself. The composition is a hand-authored `Scene` whose screen plays the approved
`scene3d-pr-demo.mp4` (seconds 6 to 12.6) as a library video asset. It was validated by
`unfold.models`, compiled by `Backend.author` (which decoded the clip into frame atlases at 15 fps,
512 px wide) and rendered by `Backend.render` from the feature branch at native 1920×1080 and 30fps
with two render workers on software WebGL. No model authored or reviewed it.

The same source was rendered a second time with one worker; FFmpeg `framemd5` found 480 of 480
decoded frames identical. The MP4 is the unmodified two-worker render. The 960px GIF is derived from
it with an FFmpeg palette pass (12fps, 96 colours, ordered dither); the poster is frame 345, with the
demo playing on the screen. The scene script, both renders and the comparison report remain in
ignored working storage.

Added to the pull request at the owner's request on 2026-09-27.

## scene3d video on moving objects demo

`scene3d-video-objects-demo.mp4`, `scene3d-video-objects-demo.gif` and
`scene3d-video-objects-demo-poster.png` are an 8-second demonstration of node `media`: the approved
`scene3d-pr-demo.mp4` (from its 6-second mark, looping) plays as a video texture on three moving objects
while the camera orbits: every face of a spinning cube (`surface: every_face`), a cube that keeps turning
to face the camera (`surface: facing_camera`), and wrapped around a spinning sphere (`surface: wrap`). The
composition is a hand-authored `Scene` validated by `unfold.models`, compiled by `Backend.author` (which
decoded the clip into frame atlases at 15 fps, 512 px wide) and rendered by `Backend.render` from the
feature branch at native 1920×1080 and 30fps with two render workers on software WebGL. No model authored
or reviewed it.

The same source was rendered a second time with one worker; FFmpeg `framemd5` found 240 of 240 decoded
frames identical. The MP4 is the unmodified two-worker render. The 960px GIF is derived from it with an
FFmpeg palette pass (12fps, 96 colours, ordered dither); the poster is frame 150. The scene script, both
renders and the comparison report remain in ignored working storage.

Added to the pull request at the owner's request on 2026-09-27.
