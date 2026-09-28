"""Validated public inputs and the bounded HyperFrames authoring vocabulary."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_serializer, model_validator

from .timing import FPS, MIN_DURATION, canonical_duration, encoded_frames


class UnfoldError(Exception):
    def __init__(self, code, message, remedy="Inspect the operation and correct the input."):
        self.code, self.message, self.remedy = code, message, remedy
        super().__init__(message)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "remedy": self.remedy}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def normalize_duration(seconds, info):
    # Re-rendering saved source must not silently rewrite its original HTML timing.
    if info.context and info.context.get("retained_source"):
        return seconds
    return canonical_duration(seconds)


Duration = Annotated[
    float,
    Field(ge=MIN_DURATION, le=60, description=(
        "Seconds, from one 30-fps frame (1/30 s) through 60 s. Rounded to the nearest "
        "whole frame, with half-frame ties rounded up; retained as frames/30."
    )),
    AfterValidator(normalize_duration),
]


class OutputSettings(Strict):
    """Native canvas pixels; not an encoder upscale or a global preference."""

    resolution: Literal["720p", "1080p"] = "1080p"

    @property
    def dimensions(self):
        return (1920, 1080) if self.resolution == "1080p" else (1280, 720)


def retained_brief(data):
    """Missing settings on saved work mean the original 720p profile."""
    return Brief.model_validate({"output": {"resolution": "720p"}, **data})


def retry_brief_payload(brief, previous, *, derived=False, canonical=False):
    """Keep legacy hashes exact, without treating explicit new settings as old input."""
    payload = Brief.model_validate(brief.model_dump()).model_dump() if canonical else brief.model_dump()
    if "output" not in previous and (
        "output" not in brief.model_fields_set
        or (derived and brief.output.resolution == "720p")
    ):
        payload.pop("output")
    return payload


class Brief(Strict):
    title: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=12000)
    context: str = Field(default="", max_length=30000)
    identity: str = Field(default="", max_length=20000)
    identity_version: str | None = None
    reference_id: str | None = None
    reference_start: float = Field(default=0, ge=0)
    cues: list[str] = Field(default_factory=list, max_length=40)
    duration: Duration = 20
    output: OutputSettings = Field(default_factory=OutputSettings)


class Grant(Strict):
    provider: Literal["gemini", "openai", "anthropic"]
    model: str = Field(min_length=1, max_length=150)
    allow_context: bool = False
    allow_frames: bool = False
    vision: bool = False
    max_model_calls: int = Field(default=12, ge=1, le=24)
    max_tool_calls: int = Field(default=30, ge=1, le=60)
    max_renders: int = Field(default=3, ge=1, le=5)
    max_frames: int = Field(default=16, ge=1, le=40)
    max_seconds: int = Field(default=900, ge=30, le=1800)
    max_text_bytes: int = Field(default=5000000, ge=1000, le=5000000)
    max_image_bytes: int = Field(default=16000000, ge=1000, le=40000000)
    max_response_tokens: int = Field(default=12000, ge=1000, le=20000)


Angle = Annotated[float, Field(ge=-3600, le=3600)]


class Orbit(Strict):
    """Local circular track with independently animated vertex angles."""

    center: tuple[float, float]
    radius: float = Field(gt=0, le=640)
    angles: list[Angle] = Field(min_length=3, max_length=80)
    marker_radius: float = Field(default=0, ge=0, le=20)


ELEMENT_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"


class Element(Strict):
    id: str = Field(pattern=ELEMENT_ID_PATTERN)
    kind: Literal["card", "text", "line", "dot", "path", "circle", "arc", "image"]
    asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    x: float = Field(ge=0, le=1920)
    y: float = Field(ge=0, le=1080)
    width: float = Field(gt=0, le=1920)
    height: float = Field(gt=0, le=1080)
    text: str = Field(default="", max_length=250)
    label: str = Field(default="", max_length=60)
    fill: str = Field(default="#142334", pattern=r"^#[0-9a-fA-F]{6}$")
    color: str = Field(default="#edf4f5", pattern=r"^#[0-9a-fA-F]{6}$")
    border: str = Field(default="#345368", pattern=r"^#[0-9a-fA-F]{6}$")
    font_size: int = Field(default=28, ge=16, le=80)
    font_asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    font_weight: int | None = Field(default=None, ge=1, le=1000)
    font_style: Literal["normal", "italic", "oblique"] | None = None
    letter_spacing: float = Field(default=0, ge=-10, le=40)
    line_height: float = Field(default=1.22, ge=0.5, le=4)
    opacity: float = Field(default=0, ge=0, le=1)
    radius: int = Field(default=14, ge=0, le=100)
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=80)
    orbit: Orbit | None = None
    closed: bool = False
    arrow_end: bool = False
    stroke_width: float = Field(default=4, ge=0.5, le=20)
    fill_opacity: float = Field(default=0, ge=0, le=1)
    draw: float = Field(default=1, ge=0, le=1)
    start_angle: float = Field(default=0, ge=-360, le=360)
    sweep_angle: float = Field(default=90, gt=0, lt=360)
    glow_tip: bool = False

    @model_validator(mode="after")
    def fits(self):
        if self.font_asset_id and self.kind not in {"text", "card"}:
            raise ValueError("Custom fonts apply to text and card elements.")
        if self.glow_tip and self.kind != "arc":
            raise ValueError("A synchronized glowing tip currently requires an arc.")
        if self.orbit:
            if self.kind != "path" or not self.closed or self.points or self.arrow_end:
                raise ValueError("Orbit requires a closed path without points or arrowheads.")
            cx, cy = self.orbit.center
            extent = self.orbit.radius + max(self.orbit.marker_radius, self.stroke_width / 2)
            if not (extent <= cx <= self.width - extent and
                    extent <= cy <= self.height - extent):
                raise ValueError("Orbit and markers must fit inside the element bounds.")
        elif self.kind == "path":
            if len(self.points) < (3 if self.closed else 2):
                raise ValueError("Paths need two points; closed polygons need three.")
            if any(not (0 <= x <= self.width and 0 <= y <= self.height) for x, y in self.points):
                raise ValueError("Path points use local coordinates inside the element's bounds.")
            if self.arrow_end and (self.closed or self.points[-1] == self.points[-2]):
                raise ValueError("Arrowheads require an open path with a nonzero final segment.")
        elif self.points or self.closed or self.arrow_end:
            raise ValueError("Points, closed and arrow_end apply only to paths.")
        return self


class Tween(Strict):
    target: str
    orbit_angles: list[Angle] | None = Field(default=None, min_length=3, max_length=80)
    marker_opacity: float | None = Field(default=None, ge=0, le=1)
    at: float = Field(ge=0, le=60)
    duration: float = Field(default=0.5, ge=0, le=10)
    opacity: float | None = Field(default=None, ge=0, le=1)
    x: float | None = Field(default=None, ge=-1920, le=1920)
    y: float | None = Field(default=None, ge=-1080, le=1080)
    scale: float | None = Field(default=None, ge=0.1, le=3)
    rotation: float | None = Field(default=None, ge=-720, le=720)
    draw: float | None = Field(default=None, ge=0, le=1)
    points: list[tuple[float, float]] | None = Field(default=None, max_length=80)
    ease: Literal["none", "power2.inOut", "power2.out", "power3.out"] = "power2.inOut"


class CameraMove(Strict):
    """World point to place at the screen center, with uniform magnification."""

    at: float = Field(ge=0, le=60)
    duration: float = Field(default=1, ge=0, le=10)
    center_x: float = Field(ge=0, le=1920)
    center_y: float = Field(ge=0, le=1080)
    zoom: float = Field(ge=0.25, le=8)
    ease: Literal["none", "power2.inOut", "power2.out"] = "power2.inOut"


Role3D = Literal["request", "response", "agent", "tool", "data", "error", "cache", "neutral"]
Material3D = Literal["chrome", "gold", "glass", "ceramic", "matte", "neon", "holo", "obsidian"]
Shape3D = Literal["sphere", "cube", "capsule", "torus", "cylinder", "icosahedron", "platform"]
Ease3D = Literal["none", "power2.inOut", "power2.out", "power3.out"]

# stream = throughput A->B, flock = self-organising behaviour, orbit = resident set/queue,
# burst = discrete event, field = ambient pressure/diffusion.
EFFECT3D_COUNT_CAPS = {"stream": 2500, "flock": 400, "orbit": 1500, "burst": 600, "field": 3000}


def _bounded_node_position(value):
    x, y, z = value
    if not (-20 <= x <= 20 and 0 <= y <= 12 and -20 <= z <= 20):
        raise ValueError("Node position requires x,z in [-20,20] and y in [0,12].")
    return value


NodePosition = Annotated[tuple[float, float, float], AfterValidator(_bounded_node_position)]


# How a video sits on each shape. Box shapes show it on faces; round shapes wrap it around; torus and
# icosahedron are not offered (their surfaces scramble a picture). "auto" resolves per shape.
MEDIA_SURFACES = {
    "cube": {"auto": "every_face", "every_face": 1, "one_face": 1, "facing_camera": 1},
    "platform": {"auto": "one_face", "one_face": 1},
    "sphere": {"auto": "wrap", "wrap": 1, "facing_camera": 1},
    "capsule": {"auto": "wrap", "wrap": 1, "facing_camera": 1},
    "cylinder": {"auto": "wrap", "wrap": 1, "facing_camera": 1},
}
MEDIA_SHAPES = tuple(MEDIA_SURFACES)


class Media3D(Strict):
    """A library video shown on a node's surface. Code decodes it into frame atlases at author
    time; the runtime shows the frame for scene time t while the node moves."""

    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")  # a video asset from the selected identity
    media_start: float = Field(default=0, ge=0, le=600)  # seconds into the clip
    play_from: float | None = Field(default=None, ge=0, le=60)  # scene time playback starts; None = appear_at
    rate: float = Field(default=1.0, ge=0.25, le=4)
    loop: bool = False  # False holds the last frame
    # every_face: each face of a box shows the clip; one_face: only the front face (top, for a platform);
    # wrap: wraps once around a round shape; facing_camera: the node keeps turning to face the camera and
    # the clip is on the side it shows the camera. auto picks every_face / one_face / wrap by shape.
    surface: Literal["auto", "every_face", "one_face", "wrap", "facing_camera"] = "auto"


class Node3D(Strict):
    id: str = Field(pattern=ELEMENT_ID_PATTERN)
    shape: Shape3D = "sphere"
    material: Material3D = "ceramic"
    role: Role3D = "neutral"
    position: NodePosition
    size: float = Field(default=1.0, ge=0.2, le=6)
    appear_at: float = Field(default=0, ge=0, le=60)
    ring: bool = True
    # Rendered by the runtime as DOM text (textContent), never HTML.
    label: str | None = Field(default=None, max_length=40)
    media: Media3D | None = None  # plays a library video on the node's surface
    spin: float | None = Field(default=None, ge=-360, le=360)  # degrees/second about the vertical axis
    # None (including an absent field) preserves the legacy treatment.
    entrance: Literal["pop", "none"] | None = None
    idle_motion: Literal["bob", "none"] | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")  # sRGB material tint

    @model_validator(mode="after")
    def media_fits_shape(self):
        if self.media is None:
            return self
        if self.shape not in MEDIA_SURFACES:
            raise ValueError(f"media is supported on {', '.join(MEDIA_SHAPES)} nodes, not {self.shape}.")
        allowed = [k for k in MEDIA_SURFACES[self.shape] if k != "auto"]
        if self.media.surface != "auto" and self.media.surface not in allowed:
            raise ValueError(f"media surface {self.media.surface!r} is not available on a {self.shape}; "
                             f"use auto or one of: {', '.join(allowed)}.")
        if self.media.surface == "facing_camera" and self.spin is not None:
            raise ValueError("spin cannot be combined with media surface 'facing_camera'.")
        return self


class Link3D(Strict):
    id: str = Field(pattern=ELEMENT_ID_PATTERN)
    from_node: str
    to_node: str
    role: Role3D = "neutral"
    lift: float = Field(default=1.5, ge=0, le=6)
    appear_at: float = Field(default=0, ge=0, le=60)
    draw_duration: float = Field(default=0.8, ge=0, le=10)

    @model_validator(mode="after")
    def distinct_endpoints(self):
        if self.from_node == self.to_node:
            raise ValueError("A link's from_node and to_node must differ.")
        return self


class Effect3D(Strict):
    id: str = Field(pattern=ELEMENT_ID_PATTERN)
    preset: Literal["stream", "flock", "orbit", "burst", "field"]
    node: str
    to_node: str | None = None
    role: Role3D = "neutral"
    start: float = Field(default=0, ge=0, le=60)
    end: float = Field(..., gt=0, le=60)
    count: int = Field(..., ge=1)
    intensity: float = Field(default=1.0, ge=0.2, le=2)
    speed: float = Field(default=1.0, ge=0.25, le=3)
    spread: float = Field(default=0.35, ge=0, le=1)

    @model_validator(mode="after")
    def fits(self):
        if self.preset == "stream":
            if self.to_node is None or self.to_node == self.node:
                raise ValueError("A stream effect requires a to_node different from node.")
        elif self.to_node is not None:
            raise ValueError(f"to_node is not permitted for the {self.preset} preset.")
        if self.end <= self.start:
            raise ValueError("Effect end must be greater than start.")
        cap = EFFECT3D_COUNT_CAPS[self.preset]
        if self.count > cap:
            raise ValueError(f"{self.preset} effects allow at most {cap} particles.")
        return self


class Shot3D(Strict):
    """Camera keyframe; the runtime interpolates between keyframes."""

    at: float = Field(..., ge=0, le=60)
    duration: float = Field(default=0, ge=0, le=20)  # ease-in from previous keyframe; 0 = cut
    target: str | None = None  # node id; None = centroid of all nodes
    azimuth: float = Field(default=-90, ge=-720, le=720)
    elevation: float = Field(default=25, ge=3, le=85)
    distance: float = Field(default=16, ge=2, le=60)
    fov: float = Field(default=40, ge=15, le=90)
    ease: Ease3D = "power2.inOut"


class Moment3D(Strict):
    """A full-screen effect pulse."""

    at: float = Field(..., ge=0, le=60)
    duration: float = Field(default=0.8, gt=0, le=5)
    kind: Literal["shockwave", "flash", "glitch", "focus_pull"]
    node: str | None = None  # shockwave/focus_pull origin; None = screen centre
    strength: float = Field(default=1.0, ge=0.1, le=2)


class Screen3D(Strict):
    """A panel that plays a library video. Code decodes the clip into frame atlases at author
    time; the runtime shows the frame for scene time t, so playback is seek-exact."""

    id: str = Field(pattern=ELEMENT_ID_PATTERN)
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")  # a video asset from the selected identity
    position: NodePosition
    width: float = Field(default=4.0, ge=1, le=12)  # world units; height follows the clip aspect
    yaw: float = Field(default=0, ge=-180, le=180)  # degrees about the vertical axis
    tilt: float = Field(default=0, ge=-45, le=45)  # degrees back (+) or forward (-)
    appear_at: float = Field(default=0, ge=0, le=60)
    play_from: float | None = Field(default=None, ge=0, le=60)  # scene time playback starts; None = appear_at
    media_start: float = Field(default=0, ge=0, le=600)  # seconds into the clip
    rate: float = Field(default=1.0, ge=0.25, le=4)
    loop: bool = False  # False holds the last frame
    frame: Literal["bezel", "floating", "none"] = "bezel"
    role: Role3D = "neutral"
    label: str | None = Field(default=None, max_length=40)
    entrance: Literal["scale", "none"] | None = None  # None preserves the legacy scale-in


class PostOverrides3D(Strict):
    """Selective overrides, not a new preset. Zero disables the corresponding pass."""

    bloom_weight: float | None = Field(default=None, ge=0, le=1)
    glow_intensity: float | None = Field(default=None, ge=0, le=2)


class Scene3D(Strict):
    # Independent of Scene.background (the underlying CSS/2D plate).
    # None preserves legacy behavior, including transparent whole-scene output.
    background: Literal["environment", "transparent"] | None = Field(
        default=None,
        description="Transparent omits sky, stars and floor while retaining object lighting/reflections; "
                    "Scene.background and 2D elements remain unchanged. Null keeps legacy behavior.",
    )
    environment: Literal[
        "studio_dark", "deep_space", "dusk", "lab_white", "neon_grid"
    ] = "studio_dark"
    post: Literal["clean", "cinematic", "neon", "dreamy", "noir"] = "cinematic"
    seed: int = Field(default=1, ge=0, le=2**31 - 1)
    floor: bool = True
    floor_grid: bool | None = None  # False keeps the environment's floor but removes its grid
    post_overrides: PostOverrides3D | None = None
    nodes: list[Node3D] = Field(..., min_length=1, max_length=24)
    links: list[Link3D] = Field(default_factory=list, max_length=32)
    effects: list[Effect3D] = Field(default_factory=list, max_length=12)
    camera: list[Shot3D] = Field(..., min_length=1, max_length=20)
    moments: list[Moment3D] = Field(default_factory=list, max_length=16)
    screens: list[Screen3D] = Field(default_factory=list, max_length=4)

    @model_serializer(mode="wrap")
    def preserve_background_presence(self, handler):
        # This additive nullable field did not exist in earlier retained sources.
        # Preserve its absence through nested Scene dumps and author's numeric
        # roundtrip, without changing any other default/null serialization.
        data = handler(self)
        if self.background is None and "background" not in self.model_fields_set:
            data.pop("background", None)
        return data

    @model_validator(mode="after")
    def references(self):
        node_ids = {n.id for n in self.nodes}
        link_ids = {link.id for link in self.links}
        effect_ids = {e.id for e in self.effects}
        screen_ids = {s.id for s in self.screens}
        all_ids = node_ids | link_ids | effect_ids | screen_ids
        if len(all_ids) != len(self.nodes) + len(self.links) + len(self.effects) + len(self.screens):
            raise ValueError("scene3d IDs must be unique across nodes, links, effects and screens.")
        targetable = node_ids | screen_ids
        if all_ids & {"root", "world", "scene3d"}:
            raise ValueError("The IDs root, world and scene3d are reserved by the backend.")
        for link in self.links:
            if link.from_node not in node_ids or link.to_node not in node_ids:
                raise ValueError("Link must reference existing nodes.")
        for effect in self.effects:
            if effect.node not in node_ids or (
                effect.to_node is not None and effect.to_node not in node_ids
            ):
                raise ValueError("Effect must reference existing nodes.")
        for shot in self.camera:
            if shot.target is not None and shot.target not in targetable:
                raise ValueError("Camera shot target must reference an existing node or screen.")
        for moment in self.moments:
            if moment.node is not None and moment.node not in targetable:
                raise ValueError("Moment must reference an existing node or screen.")
        if sum(e.count for e in self.effects) > 8000:
            raise ValueError("Total scene3d particle count must not exceed 8000.")
        if self.camera[0].at != 0:
            raise ValueError("The first camera shot must be at time 0.")
        for earlier, later in zip(self.camera, self.camera[1:]):
            if later.at <= earlier.at:
                raise ValueError("Camera shots must be strictly increasing in time.")
        return self


class Scene(Strict):
    """Internal backend-specific authoring input, not a universal interchange format."""

    title: str = Field(min_length=1, max_length=120)
    duration: Duration
    # Legacy scene documents have no output field. Never reinterpret their pixels.
    output: OutputSettings = Field(default_factory=lambda: OutputSettings(resolution="720p"))
    background: str = Field(default="#08131f", pattern=r"^(#[0-9a-fA-F]{6}|transparent)$")
    # A scene3d layer can stand alone: elements/tweens are required only without one.
    elements: list[Element] = Field(default_factory=list, max_length=70)
    tweens: list[Tween] = Field(default_factory=list, max_length=200)
    camera: list[CameraMove] = Field(default_factory=list, max_length=30)
    stroke_animation: Literal["css", "svg"] = "css"
    explanation: str = Field(min_length=1, max_length=3000)
    scene3d: Scene3D | None = None

    @model_validator(mode="after")
    def references(self):
        width, height = self.output.dimensions
        if any(e.x + e.width > width or e.y + e.height > height for e in self.elements):
            raise ValueError(f"Element must fit the {width} × {height} canvas.")
        if any(abs(t.x or 0) > width or abs(t.y or 0) > height for t in self.tweens):
            raise ValueError("Tween offsets must fit the canvas dimensions.")
        if any(m.center_x > width or m.center_y > height for m in self.camera):
            raise ValueError("Camera center must fit the canvas dimensions.")
        if any(e.glow_tip for e in self.elements) and self.stroke_animation != "svg":
            raise ValueError("Glowing tips require SVG stroke animation.")
        ids = {element.id for element in self.elements}
        if ids & {"root", "world"}:
            raise ValueError("The element IDs root and world are reserved by the backend.")
        if self.scene3d is not None and "scene3d" in ids:
            raise ValueError("The element ID scene3d is reserved when a scene3d layer is present.")
        if len(ids) != len(self.elements):
            raise ValueError("Element IDs must be unique.")
        if self.scene3d is None:
            if not self.elements:
                raise ValueError("Provide at least one element, or a scene3d layer.")
            if not self.tweens:
                raise ValueError("Provide at least one tween, or a scene3d layer.")
        else:
            scene3d_ids = (
                {n.id for n in self.scene3d.nodes}
                | {link.id for link in self.scene3d.links}
                | {e.id for e in self.scene3d.effects}
                | {screen.id for screen in self.scene3d.screens}
            )
            if scene3d_ids & ids:
                raise ValueError("scene3d IDs must not collide with 2D element IDs.")
            if any(node.appear_at > self.duration + 1e-9 for node in self.scene3d.nodes):
                raise ValueError("scene3d node appear_at must fit the scene duration.")
            if any(
                link.appear_at + link.draw_duration > self.duration + 1e-9
                for link in self.scene3d.links
            ):
                raise ValueError("scene3d link timing must fit the scene duration.")
            if any(effect.end > self.duration + 1e-9 for effect in self.scene3d.effects):
                raise ValueError("scene3d effect timing must fit the scene duration.")
            if any(shot.at > self.duration + 1e-9 for shot in self.scene3d.camera):
                raise ValueError("scene3d camera shot must fit the scene duration.")
            if any(
                moment.at + moment.duration > self.duration + 1e-9
                for moment in self.scene3d.moments
            ):
                raise ValueError("scene3d moment timing must fit the scene duration.")
            if any(
                max(screen.appear_at, screen.play_from or 0) > self.duration + 1e-9
                for screen in self.scene3d.screens
            ):
                raise ValueError("scene3d screen timing must fit the scene duration.")
            if any(
                node.media is not None and (node.media.play_from or 0) > self.duration + 1e-9
                for node in self.scene3d.nodes
            ):
                raise ValueError("scene3d node media timing must fit the scene duration.")
        last_frame = (encoded_frames(self.duration) - 1) / FPS
        points_tweens_by_target = {}
        for tween in self.tweens:
            if (tween.target not in ids or tween.at > last_frame + 1e-9 or
                    tween.at + tween.duration > self.duration + 1e-9):
                raise ValueError("Tween target or timing is invalid.")
            if tween.draw is not None and next(
                e for e in self.elements if e.id == tween.target
            ).kind not in {"path", "circle", "arc"}:
                raise ValueError("Drawing progress applies only to paths and circles.")
            if tween.points is not None:
                element = next(e for e in self.elements if e.id == tween.target)
                if element.kind != "path" or element.orbit is not None:
                    raise ValueError("A points tween can only target a path with explicit points, not an orbit.")
                if len(tween.points) != len(element.points):
                    raise ValueError(
                        "A points tween needs exactly as many points as the element."
                    )
                if any(
                    not (0 <= x <= element.width and 0 <= y <= element.height)
                    for x, y in tween.points
                ):
                    raise ValueError(
                        "Tween points use local coordinates inside the element's bounds."
                    )
                if element.arrow_end:
                    raise ValueError("A points tween cannot target a path with an arrowhead.")
                points_tweens_by_target.setdefault(tween.target, []).append(tween)
        for target_tweens in points_tweens_by_target.values():
            target_tweens.sort(key=lambda t: t.at)
            for earlier, later in zip(target_tweens, target_tweens[1:]):
                if later.at < earlier.at + earlier.duration:
                    raise ValueError(
                        "Points tweens on the same path must not overlap in time."
                    )
        orbit_ends = {}
        for tween in sorted(self.tweens, key=lambda t: t.at):
            element = next(e for e in self.elements if e.id == tween.target)
            if tween.orbit_angles is not None or tween.marker_opacity is not None:
                if element.orbit is None:
                    raise ValueError("Orbit animation requires an orbit path target.")
            if tween.orbit_angles is not None:
                if len(tween.orbit_angles) != len(element.orbit.angles):
                    raise ValueError("Orbit animation must preserve vertex count.")
                if tween.at < orbit_ends.get(tween.target, 0):
                    raise ValueError("Orbit angle tweens must not overlap.")
                orbit_ends[tween.target] = tween.at + tween.duration
        end = 0
        for move in self.camera:
            if (move.at < end or move.at > last_frame + 1e-9 or
                    move.at + move.duration > self.duration + 1e-9):
                raise ValueError(
                    "Camera moves must be chronological, non-overlapping and fit duration."
                )
            end = move.at + move.duration
        return self
