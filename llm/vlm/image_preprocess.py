"""Image preprocessing and ROI helpers for VLM workflows."""

import base64
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency in runtime envs.
    Image = None

from utils.thresholds import get_threshold


def _threshold_int(key: str, default: int) -> int:
    """Read integer threshold with safe fallback."""
    try:
        return int(get_threshold(key, default))
    except Exception:
        return int(default)


def _threshold_float(key: str, default: float) -> float:
    """Read float threshold with safe fallback."""
    try:
        return float(get_threshold(key, default))
    except Exception:
        return float(default)


def _threshold_bool(key: str, default: bool) -> bool:
    """Read bool threshold with safe fallback."""
    raw = get_threshold(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(raw, (int, float)):
        return bool(raw)
    return bool(default)


def _encode_image_base64(image_path: str) -> Optional[str]:
    """Read image and return base64 payload for multimodal requests."""
    try:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None


def _sips_binary() -> Optional[str]:
    """Return path to macOS `sips` binary when available."""
    return shutil.which("sips")


def _sips_dimensions(sips_bin: str, image_path: str) -> Optional[Tuple[int, int]]:
    """Read image width/height through `sips` metadata."""
    try:
        completed = subprocess.run(
            [sips_bin, "-g", "pixelWidth", "-g", "pixelHeight", image_path],
            check=True,
            capture_output=True,
            text=True,
        )
        width_match = re.search(r"pixelWidth:\s*(\d+)", completed.stdout)
        height_match = re.search(r"pixelHeight:\s*(\d+)", completed.stdout)
        if not width_match or not height_match:
            return None
        return int(width_match.group(1)), int(height_match.group(1))
    except Exception:
        return None


def _sips_make_variant(
    sips_bin: str,
    src_path: str,
    out_path: str,
    *,
    max_side: int,
    jpeg_quality: int,
    crop_h: Optional[int] = None,
    crop_w: Optional[int] = None,
    offset_y: int = 0,
    offset_x: int = 0,
) -> bool:
    """Create one resized/cropped JPEG variant using `sips`."""
    cmd = [sips_bin]
    if crop_h and crop_w:
        cmd += [
            "-c",
            str(int(crop_h)),
            str(int(crop_w)),
            "--cropOffset",
            str(int(offset_y)),
            str(int(offset_x)),
        ]
    cmd += [
        "-Z",
        str(int(max_side)),
        "-s",
        "format",
        "jpeg",
        "-s",
        "formatOptions",
        str(int(jpeg_quality)),
        src_path,
        "--out",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return Path(out_path).exists()
    except Exception:
        return False


def _dedupe_and_cap_payloads(
    candidate_bytes: List[bytes],
    *,
    original_bytes: bytes,
    max_bytes: int,
    max_variants: int,
    preferred_fallback_bytes: Optional[bytes] = None,
    logger=None,
) -> Tuple[List[str], List[int]]:
    """Apply hard byte caps + dedupe and return encoded payloads + accepted sizes."""
    payloads: List[str] = []
    accepted_sizes: List[int] = []
    seen_hashes = set()
    smallest_blob: Optional[bytes] = None
    for raw in candidate_bytes:
        if not raw:
            continue
        if smallest_blob is None or len(raw) < len(smallest_blob):
            smallest_blob = raw
        if len(raw) > max_bytes:
            continue
        signature = hashlib.sha1(raw).hexdigest()
        if signature in seen_hashes:
            continue
        seen_hashes.add(signature)
        payloads.append(base64.b64encode(raw).decode("ascii"))
        accepted_sizes.append(len(raw))
        if len(payloads) >= max_variants:
            break

    if not payloads:
        fallback = preferred_fallback_bytes or smallest_blob or original_bytes
        if len(fallback) > max_bytes and logger is not None:
            logger.warning(
                "llm.vlm_image.preprocess fallback_oversize bytes=%s max_bytes=%s",
                len(fallback),
                max_bytes,
            )
        payloads.append(base64.b64encode(fallback).decode("ascii"))
        accepted_sizes.append(len(fallback))
    return payloads, accepted_sizes


def _encode_image_base64_variants(
    image_path: str,
    *,
    profile: str = "default",
    threshold_bool_fn: Callable[[str, bool], bool] = _threshold_bool,
    threshold_int_fn: Callable[[str, int], int] = _threshold_int,
    threshold_float_fn: Callable[[str, float], float] = _threshold_float,
    sips_binary_fn: Callable[[], Optional[str]] = _sips_binary,
    sips_dimensions_fn: Callable[[str, str], Optional[Tuple[int, int]]] = _sips_dimensions,
    sips_make_variant_fn: Callable[..., bool] = _sips_make_variant,
    logger=None,
) -> List[str]:
    """Build compact image variants (downsample/crops) and return base64 payloads."""
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return []

    try:
        original_bytes = path.read_bytes()
    except Exception:
        return []
    if not original_bytes:
        return []

    preprocess_enabled = threshold_bool_fn("vlm_image_preprocess_enabled", True)
    max_variants = max(1, threshold_int_fn("vlm_image_max_variants", 2))
    max_side = max(480, threshold_int_fn("vlm_image_max_side_px", 1280))
    max_bytes = max(1, threshold_int_fn("vlm_image_max_bytes", 500_000))
    jpeg_quality = max(35, min(95, threshold_int_fn("vlm_image_jpeg_quality", 65)))
    profile_key = str(profile or "default").strip().lower() or "default"
    if profile_key == "diverse":
        include_top = threshold_bool_fn("vlm_image_profile_diverse_include_top_crop", True)
        include_center = threshold_bool_fn("vlm_image_profile_diverse_include_center_crop", True)
        include_bottom = threshold_bool_fn("vlm_image_profile_diverse_include_bottom_crop", True)
        diverse_max_variants = max(1, threshold_int_fn("vlm_image_profile_diverse_max_variants", 3))
        max_variants = min(max_variants, diverse_max_variants)
    else:
        profile_key = "default"
        include_top = threshold_bool_fn(
            "vlm_image_profile_default_include_top_crop",
            threshold_bool_fn("vlm_image_include_top_crop", True),
        )
        include_center = threshold_bool_fn(
            "vlm_image_profile_default_include_center_crop",
            threshold_bool_fn("vlm_image_include_center_crop", False),
        )
        include_bottom = threshold_bool_fn(
            "vlm_image_profile_default_include_bottom_crop",
            threshold_bool_fn("vlm_image_include_bottom_crop", False),
        )
    crop_height_ratio = max(
        0.25,
        min(0.95, threshold_float_fn("vlm_image_crop_height_ratio", 0.62)),
    )
    bottom_crop_height_ratio = max(
        0.25,
        min(0.95, threshold_float_fn("vlm_image_bottom_crop_height_ratio", 0.62)),
    )
    oversize_reencode_max_attempts = max(
        0,
        min(8, threshold_int_fn("vlm_image_oversize_reencode_max_attempts", 4)),
    )

    # Always keep original as terminal fallback.
    candidate_bytes: List[bytes] = []
    full_frame_bytes: Optional[bytes] = None

    if preprocess_enabled:
        sips_bin = sips_binary_fn()
        dims = sips_dimensions_fn(sips_bin, str(path)) if sips_bin else None
        if sips_bin and dims:
            width, height = dims
            with tempfile.TemporaryDirectory(prefix="vlm_img_") as tmp_dir:
                tmp_root = Path(tmp_dir)
                # First variant: bounded full-frame preview.
                full_path = tmp_root / "full.jpg"
                if sips_make_variant_fn(
                    sips_bin,
                    str(path),
                    str(full_path),
                    max_side=max_side,
                    jpeg_quality=jpeg_quality,
                ):
                    try:
                        full_frame_bytes = full_path.read_bytes()
                    except Exception:
                        pass

                # Prefer keeping an in-budget full-frame candidate before crops.
                if full_frame_bytes and len(full_frame_bytes) > max_bytes and oversize_reencode_max_attempts > 0:
                    tune_side = max_side
                    tune_quality = jpeg_quality
                    for attempt_idx in range(oversize_reencode_max_attempts):
                        next_side = max(480, int(round(float(tune_side) * 0.85)))
                        next_quality = max(35, tune_quality - 10)
                        if next_side == tune_side and next_quality == tune_quality:
                            break
                        tune_side = next_side
                        tune_quality = next_quality
                        tuned_path = tmp_root / f"full_reencode_{attempt_idx + 1}.jpg"
                        if not sips_make_variant_fn(
                            sips_bin,
                            str(path),
                            str(tuned_path),
                            max_side=tune_side,
                            jpeg_quality=tune_quality,
                        ):
                            continue
                        try:
                            tuned_bytes = tuned_path.read_bytes()
                        except Exception:
                            continue
                        if not tuned_bytes:
                            continue
                        full_frame_bytes = tuned_bytes
                        if len(full_frame_bytes) <= max_bytes:
                            break

                if full_frame_bytes:
                    candidate_bytes.append(full_frame_bytes)

                crop_h = max(220, min(height, int(round(height * crop_height_ratio))))
                crop_w = width
                if include_top and len(candidate_bytes) < max_variants:
                    top_path = tmp_root / "top.jpg"
                    if sips_make_variant_fn(
                        sips_bin,
                        str(path),
                        str(top_path),
                        max_side=max_side,
                        jpeg_quality=jpeg_quality,
                        crop_h=crop_h,
                        crop_w=crop_w,
                        offset_y=0,
                        offset_x=0,
                    ):
                        try:
                            candidate_bytes.append(top_path.read_bytes())
                        except Exception:
                            pass

                if include_center and len(candidate_bytes) < max_variants:
                    center_offset_y = max(0, (height - crop_h) // 2)
                    center_path = tmp_root / "center.jpg"
                    if sips_make_variant_fn(
                        sips_bin,
                        str(path),
                        str(center_path),
                        max_side=max_side,
                        jpeg_quality=jpeg_quality,
                        crop_h=crop_h,
                        crop_w=crop_w,
                        offset_y=center_offset_y,
                        offset_x=0,
                    ):
                        try:
                            candidate_bytes.append(center_path.read_bytes())
                        except Exception:
                            pass

                if include_bottom and len(candidate_bytes) < max_variants:
                    bottom_crop_h = max(
                        220,
                        min(height, int(round(height * bottom_crop_height_ratio))),
                    )
                    bottom_offset_y = max(0, height - bottom_crop_h)
                    bottom_path = tmp_root / "bottom.jpg"
                    if sips_make_variant_fn(
                        sips_bin,
                        str(path),
                        str(bottom_path),
                        max_side=max_side,
                        jpeg_quality=jpeg_quality,
                        crop_h=bottom_crop_h,
                        crop_w=width,
                        offset_y=bottom_offset_y,
                        offset_x=0,
                    ):
                        try:
                            candidate_bytes.append(bottom_path.read_bytes())
                        except Exception:
                            pass

    # Fallback if preprocessing unavailable/failed.
    if not candidate_bytes:
        candidate_bytes.append(original_bytes)

    payloads, accepted_sizes = _dedupe_and_cap_payloads(
        candidate_bytes,
        original_bytes=original_bytes,
        max_bytes=max_bytes,
        max_variants=max_variants,
        preferred_fallback_bytes=full_frame_bytes,
        logger=logger,
    )
    if logger is not None:
        logger.info(
            "llm.vlm_image.preprocess enabled=%s profile=%s candidate_count=%s accepted_count=%s accepted_sizes=%s max_side=%s max_bytes=%s",
            preprocess_enabled,
            profile_key,
            len(candidate_bytes),
            len(payloads),
            accepted_sizes,
            max_side,
            max_bytes,
        )
    return payloads


def _clamp01(value: float) -> float:
    """Clamp numeric value into [0.0, 1.0]."""
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _normalize_roi_bbox(raw) -> Optional[Tuple[float, float, float, float]]:
    """Normalize ROI bbox as (x,y,w,h) in [0,1] coordinates."""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x = _clamp01(float(raw[0]))
        y = _clamp01(float(raw[1]))
        w = _clamp01(float(raw[2]))
        h = _clamp01(float(raw[3]))
    except Exception:
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    if x + w > 1.0:
        w = max(0.0, 1.0 - x)
    if y + h > 1.0:
        h = max(0.0, 1.0 - y)
    if w <= 0.0 or h <= 0.0:
        return None
    return (x, y, w, h)


def _crop_roi_base64(
    image_path: str,
    bbox: Tuple[float, float, float, float],
    *,
    threshold_float_fn: Callable[[str, float], float] = _threshold_float,
    threshold_int_fn: Callable[[str, int], int] = _threshold_int,
    sips_binary_fn: Callable[[], Optional[str]] = _sips_binary,
    sips_dimensions_fn: Callable[[str, str], Optional[Tuple[int, int]]] = _sips_dimensions,
    sips_make_variant_fn: Callable[..., bool] = _sips_make_variant,
    encode_image_base64_fn: Callable[[str], Optional[str]] = _encode_image_base64,
) -> Optional[str]:
    """Crop one normalized ROI from original image and return base64 JPEG payload."""
    src_path = Path(image_path)
    if not src_path.exists() or not src_path.is_file():
        return None
    pad_ratio = max(
        0.0,
        min(0.5, threshold_float_fn("vlm_fill_verify_roi_padding_ratio", 0.18)),
    )
    max_side = max(640, threshold_int_fn("vlm_fill_verify_roi_max_side_px", 1600))
    quality = max(40, min(95, threshold_int_fn("vlm_fill_verify_roi_jpeg_quality", 85)))

    def _pixel_box(width: int, height: int) -> Tuple[int, int, int, int]:
        x, y, w, h = bbox
        x_px = int(round(x * width))
        y_px = int(round(y * height))
        w_px = max(1, int(round(w * width)))
        h_px = max(1, int(round(h * height)))
        pad_x = int(round(w_px * pad_ratio))
        pad_y = int(round(h_px * pad_ratio))
        left = max(0, x_px - pad_x)
        top = max(0, y_px - pad_y)
        right = min(width, x_px + w_px + pad_x)
        bottom = min(height, y_px + h_px + pad_y)
        return left, top, right, bottom

    sips_bin = sips_binary_fn()
    dims = sips_dimensions_fn(sips_bin, str(src_path)) if sips_bin else None
    if sips_bin and dims:
        width, height = dims
        left, top, right, bottom = _pixel_box(width, height)
        crop_w = max(1, right - left)
        crop_h = max(1, bottom - top)
        with tempfile.TemporaryDirectory(prefix="vlm_roi_") as tmp_dir:
            out_path = Path(tmp_dir) / "roi.jpg"
            ok = sips_make_variant_fn(
                sips_bin,
                str(src_path),
                str(out_path),
                max_side=max_side,
                jpeg_quality=quality,
                crop_h=crop_h,
                crop_w=crop_w,
                offset_y=top,
                offset_x=left,
            )
            if ok:
                try:
                    return base64.b64encode(out_path.read_bytes()).decode("ascii")
                except Exception:
                    pass

    if Image is not None:
        try:
            with Image.open(src_path) as image:
                width, height = image.size
                left, top, right, bottom = _pixel_box(width, height)
                roi = image.crop((left, top, right, bottom))
                if max(roi.size) > max_side:
                    scale = float(max_side) / float(max(roi.size))
                    new_size = (
                        max(1, int(round(roi.size[0] * scale))),
                        max(1, int(round(roi.size[1] * scale))),
                    )
                    resample = getattr(Image, "Resampling", Image).LANCZOS
                    roi = roi.resize(new_size, resample=resample)
                if getattr(roi, "mode", "") not in ("RGB", "L"):
                    roi = roi.convert("RGB")
                with tempfile.TemporaryDirectory(prefix="vlm_roi_pil_") as tmp_dir:
                    out_path = Path(tmp_dir) / "roi.jpg"
                    roi.save(out_path, format="JPEG", quality=quality, optimize=True)
                    return base64.b64encode(out_path.read_bytes()).decode("ascii")
        except Exception:
            pass

    return encode_image_base64_fn(str(src_path))
