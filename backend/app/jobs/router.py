"""Route handlers for /api/jobs (Settings → Jobs)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_active_region_id, require_token
from app.jobs import service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    return service.job_list(region_id)


@router.post("/{job_id}/run", status_code=202)
async def run_job(job_id: str, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    try:
        return service.start_background(job_id, region_id)
    except KeyError:
        raise HTTPException(404, "unknown job")
    except service.JobBusy:
        raise HTTPException(409, "job is already running")
