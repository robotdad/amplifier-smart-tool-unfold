"""Validated static font faces, identity roles and retained font dependencies."""

import io
import json
import re
from pathlib import Path

from fontTools.ttLib import TTFont

from .models import UnfoldError
from .store import digest

MAX_FONT = 32 * 1024 * 1024


def inspect_font(source, suffix=None, text=None):
    """Read actual face metadata; never infer a typeface from its filename."""
    try:
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        suffix = suffix or Path(source).suffix.lower()
        if suffix not in {".ttf", ".otf"} or len(raw) > MAX_FONT:
            raise ValueError("Use a static .ttf or .otf font up to 32 MiB.")
        with TTFont(io.BytesIO(raw), lazy=False) as font:
            if font.flavor or "fvar" in font or "SVG " in font:
                raise ValueError("Web, variable and SVG fonts are not supported; supply a static face.")
            if not ({"glyf", "CFF "} & set(font.keys())):
                raise ValueError("Font has no supported outlines.")
            font.ensureDecompiled()
            family = font["name"].getBestFamilyName()
            weight = font["OS/2"].usWeightClass
            selection = font["OS/2"].fsSelection
            style = "oblique" if selection & 512 else "italic" if selection & 1 else "normal"
            cmap = font.getBestCmap()
            if not family or not cmap or not 1 <= weight <= 1000:
                raise ValueError("Font needs a family, Unicode characters and a valid weight.")
            if text is not None:
                missing = sorted({ord(c) for c in text if not c.isspace() and ord(c) not in cmap})
                if missing:
                    raise UnfoldError("MISSING_GLYPH", "Font lacks required characters: " +
                                      ", ".join(f"U+{c:04X}" for c in missing[:12]))
            return {"family": family, "weight": weight, "style": style,
                    "format": "opentype" if font.sfntVersion == "OTTO" else "truetype"}
    except UnfoldError:
        raise
    except Exception as exc:
        raise UnfoldError("INVALID_FONT", f"Unsupported or invalid font: {exc}") from None


def validate_face(metadata, weight=None, style=None):
    if ((weight is not None and weight != metadata["weight"]) or
            (style is not None and style != metadata["style"])):
        raise UnfoldError("UNAVAILABLE_FONT_FACE", f"{metadata['family']} supplies weight "
                          f"{metadata['weight']}, style {metadata['style']}; import the requested face.")


def validate_typography(guidance, assets, omitted=()):
    """Roles are explicit face selections, remapped with asset IDs during ZIP import."""
    roles = guidance.get("typography", {})
    if isinstance(roles, str):
        return  # Earlier packs allowed freeform typography guidance.
    if not isinstance(roles, dict) or len(roles) > 20:
        raise UnfoldError("INVALID_TYPOGRAPHY", "Typography must map up to 20 roles to font selections.")
    by_id = {a["id"]: a for a in assets}
    for role, selection in roles.items():
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", role) or not isinstance(selection, dict)
                or set(selection) - {"font_asset_id", "font_weight", "font_style"}):
            raise UnfoldError("INVALID_TYPOGRAPHY", "Use named roles with font_asset_id and optional font_weight/font_style.")
        identity = selection.get("font_asset_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise UnfoldError("INVALID_TYPOGRAPHY", "Each role needs a font_asset_id.")
        if identity in omitted:
            continue
        asset = by_id.get(identity)
        if not asset or asset["role"] != "font":
            raise UnfoldError("MISSING_FONT", f"Typography role {role} needs a font in the pack's assets.")
        validate_face(asset["font"], selection.get("font_weight"), selection.get("font_style"))


def retained_fonts(directory):
    path = Path(directory) / "fonts.json"
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text())
        for identity, record in records.items():
            if (not re.fullmatch(r"[a-f0-9]{32}", identity) or record["suffix"] not in {".ttf", ".otf"}):
                raise UnfoldError("INVALID_FONT", "Invalid retained font identity or format.")
            font_path = Path(directory) / "fonts" / (identity + record["suffix"])
            if not font_path.is_file() or digest(font_path) != record["sha256"]:
                raise UnfoldError("MISSING_FONT", f"Retained font {record['font']['family']} is missing or changed.")
        return records
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise UnfoldError("INVALID_FONT", "Retained font manifest is invalid or unreadable.") from None
