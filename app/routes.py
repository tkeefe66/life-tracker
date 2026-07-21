"""Protected API routes."""
from fastapi import APIRouter, Depends

import database as db
from app.auth import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/targets")
def get_targets():
    return db.get_targets()
