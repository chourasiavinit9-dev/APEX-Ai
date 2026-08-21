"""
schemas/asset.py — Data models for Digital Asset & Source Verification.

Tracks official manufacturer product pages, datasheets, and images
with full verification status, evidence, and rejection reasons.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, field_validator


class SourceStatus(str, Enum):
    VERIFIED = "verified"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"


class AssetType(str, Enum):
    PRODUCT_PAGE = "product_page"
    DATASHEET = "datasheet"
    IMAGE = "image"


class DigitalAsset(BaseModel):
    asset_type: str  # product_page | datasheet | image
    url: Optional[str] = None
    official_domain: Optional[str] = None
    status: SourceStatus
    evidence: Optional[str] = None
    resource_url: Optional[str] = None
    rejection_reason: Optional[str] = None

    @field_validator("url")
    @classmethod
    def url_not_empty_string(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            return None
        return v


class ProductSources(BaseModel):
    product_page: DigitalAsset
    datasheet: DigitalAsset
    images: List[DigitalAsset] = []
    source_coverage_score: float  # 0.0, 0.4, 0.7, or 1.0
    needs_human_review: bool

    def to_export_dict(self) -> dict:
        """Serialize for JSON / JSON-LD export — stripped of None values."""

        def _asset(a: DigitalAsset) -> dict:
            return {
                k: v
                for k, v in {
                    "url": a.url,
                    "status": a.status.value,
                    "evidence": a.evidence,
                    "rejection_reason": a.rejection_reason,
                }.items()
                if v is not None
            }

        return {
            "product_page": _asset(self.product_page),
            "datasheet": _asset(self.datasheet),
            "images": [_asset(i) for i in self.images],
            "source_coverage_score": self.source_coverage_score,
            "needs_human_review": self.needs_human_review,
        }
