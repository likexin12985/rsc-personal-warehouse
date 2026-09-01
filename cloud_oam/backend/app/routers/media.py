import hashlib
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_user
from ..models import MediaAttachment, StocktakeTask, Transfer, User, Warehouse


router = APIRouter(prefix="/media", tags=["media"])
settings = get_settings()
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}


def authorize_media_entity(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    user: User,
    write: bool,
) -> None:
    if entity_type == "transfer":
        entity = db.get(Transfer, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="调拨单不存在")
        warehouse_id = entity.target_warehouse_id
        if user.role == "technician" and entity.recipient_user_id != user.id:
            raise HTTPException(status_code=403, detail="不能访问他人的调拨附件")
    elif entity_type == "stocktake":
        entity = db.get(StocktakeTask, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="盘点任务不存在")
        warehouse_id = entity.warehouse_id
        if user.role == "technician" and entity.assignee_id != user.id:
            raise HTTPException(status_code=403, detail="不能访问他人的盘点附件")
    else:
        raise HTTPException(status_code=400, detail="不支持的附件对象")

    if user.role not in {"admin", "technician"} and user.province:
        warehouse = db.get(Warehouse, warehouse_id)
        if not warehouse or warehouse.province != user.province:
            raise HTTPException(status_code=403, detail="不能访问其他省份附件")


@router.post("/{entity_type}/{entity_id}")
async def upload_media(
    entity_type: str,
    entity_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize_media_entity(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user=user,
        write=True,
    )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 JPG、PNG、WebP、MP4、MOV、WebM")
    suffix = Path(file.filename or "upload").suffix.lower()[:12]
    relative = Path(entity_type) / entity_id / f"{uuid.uuid4()}{suffix}"
    target = Path(settings.upload_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="单个文件不能超过120MB")
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    attachment = MediaAttachment(
        entity_type=entity_type,
        entity_id=entity_id,
        original_name=(file.filename or "上传文件")[:255],
        storage_path=str(relative),
        mime_type=mime,
        size_bytes=size,
        sha256=digest.hexdigest(),
        uploaded_by_id=user.id,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return {
        "id": attachment.id,
        "name": attachment.original_name,
        "mimeType": attachment.mime_type,
        "sizeBytes": attachment.size_bytes,
        "url": f"/api/media/{attachment.id}/content",
    }


@router.get("/{entity_type}/{entity_id}")
def list_media(
    entity_type: str,
    entity_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize_media_entity(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user=user,
        write=False,
    )
    rows = list(
        db.scalars(
            select(MediaAttachment)
            .where(
                MediaAttachment.entity_type == entity_type,
                MediaAttachment.entity_id == entity_id,
            )
            .order_by(MediaAttachment.created_at.desc())
        )
    )
    return [
        {
            "id": row.id,
            "name": row.original_name,
            "mimeType": row.mime_type,
            "sizeBytes": row.size_bytes,
            "url": f"/api/media/{row.id}/content",
            "uploadedBy": row.uploaded_by.name,
            "createdAt": row.created_at,
        }
        for row in rows
    ]


@router.get("/{attachment_id}/content")
def read_media(
    attachment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(MediaAttachment, attachment_id)
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    authorize_media_entity(
        db,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        user=user,
        write=False,
    )
    root = Path(settings.upload_dir).resolve()
    target = (root / row.storage_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return FileResponse(target, media_type=row.mime_type, filename=row.original_name)
