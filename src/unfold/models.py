"""Validated public inputs and the bounded HyperFrames authoring vocabulary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnfoldError(Exception):
    def __init__(self, code, message, remedy="Inspect the operation and correct the input."):
        self.code, self.message, self.remedy = code, message, remedy
        super().__init__(message)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "remedy": self.remedy}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Brief(Strict):
    title: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=12000)
    context: str = Field(default="", max_length=30000)
    identity: str = Field(default="", max_length=20000)
    identity_version: str | None = None
    reference_id: str | None = None
    reference_start: float = Field(default=0, ge=0)
    cues: list[str] = Field(default_factory=list, max_length=40)
    duration: float = Field(default=20, ge=5, le=60)


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


class Element(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    kind: Literal["card", "text", "line", "dot", "path", "circle", "arc", "image"]
    asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    x: float = Field(ge=0, le=1280)
    y: float = Field(ge=0, le=720)
    width: float = Field(gt=0, le=1280)
    height: float = Field(gt=0, le=720)
    text: str = Field(default="", max_length=250)
    label: str = Field(default="", max_length=60)
    fill: str = Field(default="#142334", pattern=r"^#[0-9a-fA-F]{6}$")
    color: str = Field(default="#edf4f5", pattern=r"^#[0-9a-fA-F]{6}$")
    border: str = Field(default="#345368", pattern=r"^#[0-9a-fA-F]{6}$")
    font_size: int = Field(default=28, ge=16, le=80)
    opacity: float = Field(default=0, ge=0, le=1)
    radius: int = Field(default=14, ge=0, le=100)
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=80)
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
        if self.glow_tip and self.kind != "arc":
            raise ValueError("A synchronized glowing tip currently requires an arc.")
        if self.x + self.width > 1280 or self.y + self.height > 720:
            raise ValueError("Element must fit the 1280 × 720 canvas.")
        if self.kind == "path":
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
    at: float = Field(ge=0, le=60)
    duration: float = Field(default=0.5, ge=0, le=10)
    opacity: float | None = Field(default=None, ge=0, le=1)
    x: float | None = Field(default=None, ge=-1280, le=1280)
    y: float | None = Field(default=None, ge=-720, le=720)
    scale: float | None = Field(default=None, ge=0.1, le=3)
    rotation: float | None = Field(default=None, ge=-720, le=720)
    draw: float | None = Field(default=None, ge=0, le=1)
    points: list[tuple[float, float]] | None = Field(default=None, max_length=80)
    ease: Literal["none", "power2.inOut", "power2.out", "power3.out"] = "power2.inOut"


class CameraMove(Strict):
    """World point to place at the screen center, with uniform magnification."""

    at: float = Field(ge=0, le=60)
    duration: float = Field(default=1, ge=0, le=10)
    center_x: float = Field(ge=0, le=1280)
    center_y: float = Field(ge=0, le=720)
    zoom: float = Field(ge=0.25, le=8)
    ease: Literal["none", "power2.inOut", "power2.out"] = "power2.inOut"


class Scene(Strict):
    """Internal backend-specific authoring input, not a universal interchange format."""

    title: str = Field(min_length=1, max_length=120)
    duration: float = Field(ge=5, le=60)
    background: str = Field(default="#08131f", pattern=r"^(#[0-9a-fA-F]{6}|transparent)$")
    elements: list[Element] = Field(min_length=1, max_length=70)
    tweens: list[Tween] = Field(min_length=1, max_length=200)
    camera: list[CameraMove] = Field(default_factory=list, max_length=30)
    stroke_animation: Literal["css", "svg"] = "css"
    explanation: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def references(self):
        if any(e.glow_tip for e in self.elements) and self.stroke_animation != "svg":
            raise ValueError("Glowing tips require SVG stroke animation.")
        ids = {element.id for element in self.elements}
        if ids & {"root", "world"}:
            raise ValueError("The element IDs root and world are reserved by the backend.")
        if len(ids) != len(self.elements):
            raise ValueError("Element IDs must be unique.")
        points_tweens_by_target = {}
        for tween in self.tweens:
            if tween.target not in ids or tween.at + tween.duration > self.duration:
                raise ValueError("Tween target or timing is invalid.")
            if tween.draw is not None and next(
                e for e in self.elements if e.id == tween.target
            ).kind not in {"path", "circle", "arc"}:
                raise ValueError("Drawing progress applies only to paths and circles.")
            if tween.points is not None:
                element = next(e for e in self.elements if e.id == tween.target)
                if element.kind != "path":
                    raise ValueError("A points tween can only target a path.")
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
        end = 0
        for move in self.camera:
            if move.at < end or move.at + move.duration > self.duration:
                raise ValueError(
                    "Camera moves must be chronological, non-overlapping and fit duration."
                )
            end = move.at + move.duration
        return self
