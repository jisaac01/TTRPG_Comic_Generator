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


class TextContentRequest(BaseModel):
    content: str


class VersionMetaUpdate(BaseModel):
    starred: bool | None = None
    description: str | None = None


class SettingsResponse(BaseModel):
    gemini_api_key_configured: bool
    gemini_api_key_masked: str
    default_model: str
    image_generation_model: str
    text_models: list[str]
    image_models: list[str]
    warnings: list[str] = Field(default_factory=list)
    fetch_error: str | None = None


class SettingsUpdate(BaseModel):
    gemini_api_key: str | None = None
    default_model: str | None = None
    image_generation_model: str | None = None


class RunLaunchRequest(BaseModel):
    url: str
    campaign: str
    generate_images: bool = False
    panel_count: int = 6
    total_pages: int = 1
    aspect_ratio: str = "3:2"
    generation_mode: str = "page"
    vignette: bool = False
    cache_buster: bool = True
    unstyled_prompts: bool = False
    chat_mode: bool = False
    pg13_mode: bool = False
    art_style: str | None = None
    rerun_from: str | None = None
    stop_after: str | None = None
    recap_version: str = "standard"


class RunSnapshotResponse(BaseModel):
    id: str | None = None
    status: str
    phase: str | None = None
    version: str | None = None
    failed_phases: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
