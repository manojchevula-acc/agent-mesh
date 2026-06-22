"""POST /ingest, GET /ingest/{job_id} — async document ingestion."""

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from ...ingestion.pipeline import IngestionPipeline
from ...models.ingestion import IngestionResult, IngestionStatus
from ...utils.logging import get_logger
from ..deps import get_ingestion_pipeline, verify_auth

logger = get_logger(__name__)
router = APIRouter()

# job_id → IngestionResult or the literal status string 'running'
_jobs: dict[str, IngestionResult | str] = {}


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(default=""),  # "" => auto-detect from filename/content
    product_applicability: str = Form(default=""),
    effective_date: str = Form(default=""),
    pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
    _: None = Depends(verify_auth),
) -> dict:
    """Upload a document and trigger async ingestion."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = IngestionStatus.RUNNING.value

    # Save upload to a temp file.
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    products = [p.strip() for p in product_applicability.split(",") if p.strip()]
    original_name = file.filename or tmp_path.name

    async def run_ingestion() -> None:
        try:
            result = await pipeline.ingest_file(
                tmp_path,
                document_type,
                products,
                effective_date,
                original_name=original_name,
            )
            _jobs[job_id] = result
        except Exception as exc:  # pragma: no cover - pipeline already guards
            logger.error("Ingestion job failed", job_id=job_id, error=str(exc))
            _jobs[job_id] = IngestionResult(
                file_path=str(tmp_path),
                chunks_created=0,
                status=IngestionStatus.ERROR.value,
                error=str(exc),
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    background_tasks.add_task(run_ingestion)
    return {"job_id": job_id, "status": "accepted"}


@router.get("/ingest/{job_id}")
async def get_ingestion_status(job_id: str) -> dict:
    result = _jobs.get(job_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if result == IngestionStatus.RUNNING.value:
        return {"job_id": job_id, "status": "running"}
    assert isinstance(result, IngestionResult)
    return {
        "job_id": job_id,
        "status": result.status,
        "chunks_created": result.chunks_created,
        "error": result.error,
    }
