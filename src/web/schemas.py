"""JSON request/response models. Client payloads must not include filesystem paths."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    warnings: list[str] = Field(default_factory=list)


class CampaignListResponse(BaseModel):
    campaigns: list[str]


class CreateCampaignRequest(BaseModel):
    name: str


class CampaignResponse(BaseModel):
    name: str
