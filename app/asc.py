"""Minimal App Store Connect API client: reads keywords per localization."""

from __future__ import annotations

import os
import time

import httpx
import jwt

API = "https://api.appstoreconnect.apple.com/v1"

KEY_ID = os.environ.get("ASC_KEY_ID", "")
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "")
KEY_PATH = os.environ.get("ASC_PRIVATE_KEY_PATH", "")

# Prefer the live version; fall back to whatever is being prepared.
STATE_PRIORITY = ["READY_FOR_SALE", "READY_FOR_DISTRIBUTION"]


class AscError(Exception):
    pass


def configured() -> bool:
    return bool(KEY_ID and ISSUER_ID and KEY_PATH and os.path.isfile(KEY_PATH))


def _token() -> str:
    with open(KEY_PATH) as f:
        key = f.read()
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"},
        key,
        algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


def _get(path: str, params: dict | None = None) -> dict:
    resp = httpx.get(
        f"{API}{path}",
        params=params,
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            detail = resp.json()["errors"][0]["detail"]
        except Exception:
            detail = resp.text[:300]
        raise AscError(f"App Store Connect {resp.status_code}: {detail}")
    return resp.json()


def fetch_keywords(app_id: int) -> tuple[str, dict[str, list[str]]]:
    """Return (version string, {locale: [keyword, ...]}) for the app's iOS version."""
    if not configured():
        raise AscError("App Store Connect credentials are not configured")
    versions = _get(
        f"/apps/{app_id}/appStoreVersions",
        {"filter[platform]": "IOS", "limit": 20},
    )["data"]
    if not versions:
        raise AscError("No App Store versions found for this app")

    def priority(v: dict) -> int:
        state = v["attributes"].get("appStoreState") or v["attributes"].get("appVersionState")
        return STATE_PRIORITY.index(state) if state in STATE_PRIORITY else len(STATE_PRIORITY)

    version = min(versions, key=priority)
    locs = _get(
        f"/appStoreVersions/{version['id']}/appStoreVersionLocalizations",
        {"limit": 200, "fields[appStoreVersionLocalizations]": "locale,keywords"},
    )["data"]

    result: dict[str, list[str]] = {}
    for loc in locs:
        attrs = loc["attributes"]
        words = [w.strip() for w in (attrs.get("keywords") or "").split(",") if w.strip()]
        if words:
            result[attrs["locale"]] = words
    return version["attributes"].get("versionString", "?"), result
