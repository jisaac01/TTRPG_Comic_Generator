"""Campaign catalog endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from art_styles import list_art_styles
from web.schemas import (
    ArtStyleListResponse,
    ArtStyleResponse,
    CampaignListResponse,
    CampaignResponse,
    CreateCampaignRequest,
    EpisodeListResponse,
    EpisodeResponse,
)

router = APIRouter()


@router.get("/api/campaigns", response_model=CampaignListResponse)
def list_campaigns(request: Request) -> CampaignListResponse:
    repository = request.app.state.services.repository
    return CampaignListResponse(campaigns=repository.list_campaigns())


@router.post("/api/campaigns", response_model=CampaignResponse, status_code=201)
def create_campaign(request: Request, body: CreateCampaignRequest) -> CampaignResponse:
    repository = request.app.state.services.repository
    try:
        path = repository.create_campaign(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Campaign already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CampaignResponse(name=path.name)


@router.get("/api/campaigns/{campaign}/episodes", response_model=EpisodeListResponse)
def list_episodes(campaign: str, request: Request) -> EpisodeListResponse:
    repository = request.app.state.services.repository
    episodes = []
    for episode in repository.list_episodes(campaign):
        episodes.append(
            EpisodeResponse(
                slug=episode.slug,
                title=episode.title,
                url=episode.url,
                created_at=episode.created_at,
                has_images=repository.episode_has_images(campaign, episode.slug),
            )
        )
    return EpisodeListResponse(episodes=episodes)


@router.get("/api/campaigns/{campaign}/art-styles", response_model=ArtStyleListResponse)
def list_campaign_art_styles(campaign: str, request: Request) -> ArtStyleListResponse:
    repository = request.app.state.services.repository
    styles = [
        ArtStyleResponse(
            id=style.id,
            stem=style.stem,
            label=style.label,
            source=style.source,
        )
        for style in list_art_styles(repository.campaigns_root, campaign)
    ]
    return ArtStyleListResponse(styles=styles)
