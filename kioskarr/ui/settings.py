"""Server-rendered Settings page. Reuses api.settings.update_settings directly."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from kioskarr.api.settings import AppSettingsUpdate, update_settings
from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.templating import templates
from kioskarr.ui.publications import _redirect

router = APIRouter(prefix="/ui/settings", tags=["ui"])


@router.get("")
def settings_page(request: Request, db: Session = Depends(get_db)):
    app_settings = get_app_settings(db)
    return templates.TemplateResponse(
        request,
        "settings_form.html",
        {
            "active_nav": "settings",
            "s": app_settings,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type"),
        },
    )


@router.post("")
async def settings_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    payload = AppSettingsUpdate(
        prowlarr_url=form["prowlarr_url"],
        prowlarr_api_key=form["prowlarr_api_key"],
        qbittorrent_url=form["qbittorrent_url"],
        qbittorrent_username=form["qbittorrent_username"],
        qbittorrent_password=form["qbittorrent_password"],
        qbittorrent_category=form["qbittorrent_category"],
        qbittorrent_downloads_local_path=form.get("qbittorrent_downloads_local_path") or "",
        library_root=form["library_root"],
        search_interval_hours=float(form["search_interval_hours"]),
        import_interval_minutes=float(form["import_interval_minutes"]),
        default_min_seeders=int(form["default_min_seeders"]),
        match_confidence_threshold=float(form["match_confidence_threshold"]),
    )
    update_settings(payload, db)
    return _redirect("/ui/settings", "Settings saved")
