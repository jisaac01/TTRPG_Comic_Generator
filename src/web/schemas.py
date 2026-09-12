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


class EpisodeResponse(BaseModel):
    slug: str
    title: str | None
    url: str | None
    created_at: str | None
    has_images: bool


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeResponse]


class ArtStyleResponse(BaseModel):
    id: str
    stem: str
    label: str
    source: str


class ArtStyleListResponse(BaseModel):
    styles: list[ArtStyleResponse]


class PromptListItem(BaseModel):
    key: str
    label: str
    exists: bool
    kind: str


class PromptListResponse(BaseModel):
    prompts: list[PromptListItem]


class PromptContentResponse(BaseModel):
    key: str
    content: str


class VersionResponse(BaseModel):
    version: str
    label: str
    status: str | None
    starred: bool
    description: str
    has_images: bool
    editable: bool


class VersionListResponse(BaseModel):
    versions: list[VersionResponse]


class VersionStatusResponse(BaseModel):
    status: str | None
    version: str | None
    checkpoints: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    starred: bool = False
    description: str = ""
    run_config: dict = Field(default_factory=dict)


class VersionFileItem(BaseModel):
    key: str
    kind: str
    exists: bool


class VersionFileListResponse(BaseModel):
    files: list[VersionFileItem]


class VersionFileContentResponse(BaseModel):
    key: str
    content: str
