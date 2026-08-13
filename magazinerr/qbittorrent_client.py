"""Thin wrapper around qBittorrent's WebUI API v2.

Auth is cookie-based: login once, the session cookie carries subsequent calls.
"""

import time

import requests


class QBittorrentError(RuntimeError):
    pass


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()

    def login(self) -> None:
        response = self._session.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.text.strip() != "Ok.":
            raise QBittorrentError(f"qBittorrent login failed: {response.text!r}")

    def ensure_category(self, category: str, save_path: str = "") -> None:
        response = self._session.post(
            f"{self.base_url}/api/v2/torrents/createCategory",
            data={"category": category, "savePath": save_path},
            timeout=self.timeout,
        )
        # 409 (already exists on newer qBittorrent) is fine; anything else is not.
        if response.status_code not in (200, 409):
            raise QBittorrentError(
                f"Failed to create qBittorrent category {category!r}: "
                f"{response.status_code} {response.text}"
            )

    def add_torrent(
        self, url: str, category: str, save_path: str | None = None, poll_attempts: int = 10
    ) -> str | None:
        """Add a torrent and return its info-hash.

        The add endpoint's response body doesn't include the hash (just "Ok."),
        and the torrent's *name* as later reported by qBittorrent is often not
        the release title we searched with — matching a later-completed
        download back to this grab by name is unreliable. Instead, snapshot the
        category's hashes before and after, and poll briefly for the new one to
        appear (qBittorrent needs a moment to fetch the .torrent and register
        it). Returns None if no new hash appears — most likely because this is
        a duplicate of a torrent qBittorrent already had.
        """
        before = {t["hash"] for t in self.list_torrents(category=category)}

        data = {"urls": url, "category": category}
        if save_path:
            data["savepath"] = save_path
        response = self._session.post(
            f"{self.base_url}/api/v2/torrents/add", data=data, timeout=self.timeout
        )
        response.raise_for_status()
        if response.text.strip() not in ("Ok.", ""):
            raise QBittorrentError(f"Failed to add torrent {url!r}: {response.text!r}")

        for _ in range(poll_attempts):
            time.sleep(0.5)
            after = {t["hash"] for t in self.list_torrents(category=category)}
            new_hashes = after - before
            if new_hashes:
                return next(iter(new_hashes))
        return None

    def list_torrents(self, category: str | None = None) -> list[dict]:
        params = {"category": category} if category else {}
        response = self._session.get(
            f"{self.base_url}/api/v2/torrents/info", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def get_files(self, torrent_hash: str) -> list[dict]:
        response = self._session.get(
            f"{self.base_url}/api/v2/torrents/files",
            params={"hash": torrent_hash},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
