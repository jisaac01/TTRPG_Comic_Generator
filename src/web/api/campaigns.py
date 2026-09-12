"""Campaign catalog endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from web.schemas import CampaignListResponse, CampaignResponse, CreateCampaignRequest

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
