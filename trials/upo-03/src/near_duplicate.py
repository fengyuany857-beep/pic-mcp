from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass
from typing import Any

import imagehash
from PIL import Image


@dataclass(frozen=True)
class ImageFingerprint:
    sha256: str
    phash: str
    crop_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateComparison:
    exact_bytes: bool
    phash_distance: int
    crop_match: bool
    crop_distance: float
    decision: str
    policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint_bytes(data: bytes) -> ImageFingerprint:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        normalized = image.convert("RGB")
        ph = imagehash.phash(normalized)
        crop = imagehash.crop_resistant_hash(normalized)
    return ImageFingerprint(
        sha256=hashlib.sha256(data).hexdigest(),
        phash=str(ph),
        crop_hash=str(crop),
    )


def compare_fingerprints(
    left: ImageFingerprint,
    right: ImageFingerprint,
    *,
    phash_threshold: int = 8,
    crop_bit_error_rate: float = 0.25,
    crop_phash_guard: int = 16,
) -> DuplicateComparison:
    left_ph = imagehash.hex_to_hash(left.phash)
    right_ph = imagehash.hex_to_hash(right.phash)
    left_crop = imagehash.hex_to_multihash(left.crop_hash)
    right_crop = imagehash.hex_to_multihash(right.crop_hash)

    phash_distance = int(left_ph - right_ph)
    crop_match = bool(left_crop.matches(right_crop, region_cutoff=1, bit_error_rate=crop_bit_error_rate))
    crop_distance = float(left_crop - right_crop)
    exact = left.sha256 == right.sha256

    # Trial policy only. The thresholds are explicit and versionable rather
    # than hidden inside ImageHash or the recommendation core.
    if exact:
        decision = "EXACT_DUPLICATE"
    elif phash_distance <= phash_threshold:
        decision = "NEAR_DUPLICATE_PHASH"
    elif crop_match and phash_distance <= crop_phash_guard:
        decision = "NEAR_DUPLICATE_CROP_ASSISTED"
    else:
        decision = "DISTINCT"

    return DuplicateComparison(
        exact_bytes=exact,
        phash_distance=phash_distance,
        crop_match=crop_match,
        crop_distance=crop_distance,
        decision=decision,
        policy={
            "phash_threshold": phash_threshold,
            "crop_bit_error_rate": crop_bit_error_rate,
            "crop_phash_guard": crop_phash_guard,
            "status": "PROVISIONAL_TRIAL_POLICY",
        },
    )
