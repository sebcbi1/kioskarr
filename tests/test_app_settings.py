import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kioskarr.app_settings import ensure_app_settings_seeded, get_app_settings
from kioskarr.config import settings as env_settings
from kioskarr.db import Base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_ensure_app_settings_seeded_creates_row_from_env_defaults(db_session):
    app_settings = ensure_app_settings_seeded(db_session)

    assert app_settings.prowlarr_url == env_settings.prowlarr_url
    assert app_settings.qbittorrent_category == env_settings.qbittorrent_category
    assert app_settings.match_confidence_threshold == env_settings.match_confidence_threshold


def test_ensure_app_settings_seeded_is_idempotent(db_session):
    first = ensure_app_settings_seeded(db_session)
    first.prowlarr_url = "http://changed:9696"
    db_session.commit()

    second = ensure_app_settings_seeded(db_session)

    assert second.id == first.id
    assert second.prowlarr_url == "http://changed:9696"  # not overwritten by re-seeding


def test_get_app_settings_raises_if_never_seeded(db_session):
    with pytest.raises(RuntimeError, match="AppSettings row is missing"):
        get_app_settings(db_session)


def test_get_app_settings_returns_current_values_after_update(db_session):
    ensure_app_settings_seeded(db_session)
    app_settings = get_app_settings(db_session)
    app_settings.match_confidence_threshold = 90.0
    db_session.commit()

    refetched = get_app_settings(db_session)
    assert refetched.match_confidence_threshold == 90.0


def test_require_download_client_and_require_prowlarr(db_session):
    app_settings = ensure_app_settings_seeded(db_session)
    app_settings.prowlarr_api_key = ""
    app_settings.qbittorrent_password = ""

    with pytest.raises(RuntimeError, match="Prowlarr API key"):
        app_settings.require_prowlarr()
    with pytest.raises(RuntimeError, match="Prowlarr API key"):
        app_settings.require_download_client()

    app_settings.prowlarr_api_key = "key"
    app_settings.require_prowlarr()  # no longer raises
    with pytest.raises(RuntimeError, match="qBittorrent password"):
        app_settings.require_download_client()

    app_settings.qbittorrent_password = "pw"
    app_settings.require_download_client()  # no longer raises
