from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from kioskarr.db import get_db
from kioskarr.models import Grab, GrabStatus
from kioskarr.templating import templates

router = APIRouter(prefix="/ui/grabs", tags=["ui"])


@router.get("")
def list_grabs_page(request: Request, status: GrabStatus | None = None, db: Session = Depends(get_db)):
    query = db.query(Grab)
    if status is not None:
        query = query.filter(Grab.status == status)
    grabs = query.order_by(Grab.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "grabs_list.html",
        {
            "active_nav": "grabs",
            "grabs": grabs,
            "statuses": list(GrabStatus),
            "selected_status": status,
        },
    )
