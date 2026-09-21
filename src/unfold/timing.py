"""Frame timing for the fixed 30-fps production profile."""

import math
from decimal import ROUND_HALF_UP, Decimal

FPS = 30
MIN_DURATION = 1 / FPS


def canonical_duration(seconds):
    """Nearest complete frame; exact half-frame ties round upward."""
    frames = int((Decimal(str(seconds)) * FPS).to_integral_value(rounding=ROUND_HALF_UP))
    return frames / FPS


def encoded_frames(seconds):
    """Retained off-grid sources keep the renderer's historical ceiling policy."""
    return max(1, math.ceil(seconds * FPS - 1e-8))


def sample_frame(seconds, fps=FPS):
    """Select the frame containing a requested timestamp, tolerating float noise."""
    return max(0, math.floor(seconds * fps + 1e-8))
