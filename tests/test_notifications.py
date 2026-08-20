from unittest.mock import patch

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kioskarr.app_settings import ensure_app_settings_seeded
from kioskarr.db import Base
from kioskarr.models import Issue, Publication, PublicationType
from kioskarr.notifications import notify_issue_available, send_notification


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def app_settings(db_session):
    settings = ensure_app_settings_seeded(db_session)
    settings.ntfy_enabled = True
    settings.ntfy_url = "https://ntfy.example.com"
    settings.ntfy_topic = "my-topic"
    db_session.commit()
    return settings


def _make_issue(tmp_path, with_cover=False):
    # get_or_generate_cover() treats file_path.with_suffix(".jpg") as the cover
    # cache location — writing the "issue" straight at that path, with real
    # content, is enough to make it look like a cover already exists, no need
    # to generate a real PDF/CBZ/EPUB just to exercise the attachment path.
    suffix = ".jpg" if with_cover else ".txt"
    file_path = tmp_path / f"N42 - Some Mag{suffix}"
    file_path.write_bytes(b"fake file contents")
    return Issue(
        id=1,
        publication_id=1,
        identifier="N42",
        file_path=str(file_path),
        source_release_title="Some Mag N42",
    )


def _make_publication():
    return Publication(id=1, title="Some Mag", type=PublicationType.magazine, target_dir="/tmp")


def test_noop_when_disabled(app_settings, tmp_path):
    app_settings.ntfy_enabled = False
    issue = _make_issue(tmp_path)
    with patch("kioskarr.notifications.requests.post") as mock_post:
        notify_issue_available(app_settings, issue, _make_publication())
    mock_post.assert_not_called()


def test_noop_when_no_topic(app_settings, tmp_path):
    app_settings.ntfy_topic = ""
    issue = _make_issue(tmp_path)
    with patch("kioskarr.notifications.requests.post") as mock_post:
        notify_issue_available(app_settings, issue, _make_publication())
    mock_post.assert_not_called()


def test_posts_message_without_cover(app_settings, tmp_path):
    issue = _make_issue(tmp_path, with_cover=False)
    with patch("kioskarr.notifications.requests.post") as mock_post:
        notify_issue_available(app_settings, issue, _make_publication())

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://ntfy.example.com/my-topic"
    assert kwargs["params"] == {"title": "Kioskarr", "message": "Some Mag - N42 available!"}
    assert "Authorization" not in kwargs["headers"]


def test_puts_file_with_cover(app_settings, tmp_path):
    issue = _make_issue(tmp_path, with_cover=True)
    with patch("kioskarr.notifications.requests.put") as mock_put:
        notify_issue_available(app_settings, issue, _make_publication())

    mock_put.assert_called_once()
    args, kwargs = mock_put.call_args
    assert args[0] == "https://ntfy.example.com/my-topic"
    assert kwargs["params"]["message"] == "Some Mag - N42 available!"
    assert kwargs["params"]["filename"] == "N42 - Some Mag.jpg"
    assert kwargs["data"].name == str(tmp_path / "N42 - Some Mag.jpg")


def test_swallows_request_exception(app_settings, tmp_path):
    issue = _make_issue(tmp_path)
    with patch(
        "kioskarr.notifications.requests.post", side_effect=requests.exceptions.ConnectionError("boom")
    ):
        notify_issue_available(app_settings, issue, _make_publication())  # must not raise


def test_send_notification_raises_on_failure(app_settings):
    with patch(
        "kioskarr.notifications.requests.post", side_effect=requests.exceptions.ConnectionError("boom")
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            send_notification(app_settings, "title", "message")


def test_send_notification_includes_bearer_auth_when_token_set(app_settings):
    app_settings.ntfy_token = "secret-token"
    with patch("kioskarr.notifications.requests.post") as mock_post:
        send_notification(app_settings, "title", "message")

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
