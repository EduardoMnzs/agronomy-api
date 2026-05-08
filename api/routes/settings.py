from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import require_admin
from core import app_settings
from db.models import User
from db.session import get_db

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


@router.get("")
def get_settings(_: User = Depends(require_admin)):
    return {"settings": app_settings.get_all_for_admin()}


@router.put("")
def update_settings(
    body: SettingsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    app_settings.set_many(db, body.values)
    return {"settings": app_settings.get_all_for_admin()}
