"""Minimal App Store Connect API client: store listing metadata per locale.

Credentials are an API key of the developer account that owns the app
(App Store Connect → Users and Access → Integrations → Team Keys). One key
covers every app of that account.
"""

from __future__ import annotations

import time

import httpx
import jwt

API = "https://api.appstoreconnect.apple.com"

# Prefer the live version / app info; fall back to whatever is being prepared.
LIVE_STATES = ["READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "ACCEPTED"]
MAX_PRODUCTS = 25


class AscError(Exception):
    pass


class Client:
    def __init__(self, key_id: str, issuer_id: str, private_key: str):
        self.key_id = key_id.strip()
        self.issuer_id = issuer_id.strip()
        self.private_key = private_key.strip()

    def _token(self) -> str:
        now = int(time.time())
        try:
            return jwt.encode(
                {"iss": self.issuer_id, "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"},
                self.private_key, algorithm="ES256", headers={"kid": self.key_id, "typ": "JWT"},
            )
        except Exception as e:
            raise AscError(f"Не удалось подписать токен — проверьте .p8 ключ: {e}")

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = httpx.get(f"{API}{path}", params=params,
                         headers={"Authorization": f"Bearer {self._token()}"}, timeout=30)
        if resp.status_code != 200:
            try:
                detail = resp.json()["errors"][0]["detail"]
            except Exception:
                detail = resp.text[:300]
            raise AscError(f"App Store Connect {resp.status_code}: {detail}")
        return resp.json()

    def list_apps(self) -> list[dict]:
        data = self.get("/v1/apps", {"limit": 200, "fields[apps]": "name,bundleId"})["data"]
        return [{"id": int(a["id"]), "name": a["attributes"]["name"]} for a in data]

    def has_app(self, app_id: int) -> bool:
        try:
            self.get(f"/v1/apps/{app_id}", {"fields[apps]": "name"})
            return True
        except AscError:
            return False

    def _product_names(self, app_id: int) -> dict[str, list[str]]:
        """Localized names of in-app purchases and subscriptions, by locale."""
        names: dict[str, list[str]] = {}

        def add(locs: list[dict]) -> None:
            for loc in locs:
                a = loc["attributes"]
                if a.get("name") and a.get("locale"):
                    names.setdefault(a["locale"], []).append(a["name"])

        for iap in self.get(f"/v1/apps/{app_id}/inAppPurchasesV2", {"limit": MAX_PRODUCTS})["data"]:
            add(self.get(f"/v2/inAppPurchases/{iap['id']}/inAppPurchaseLocalizations", {"limit": 50})["data"])
        groups = self.get(f"/v1/apps/{app_id}/subscriptionGroups", {"limit": 10, "include": "subscriptions"})
        subs = [i for i in groups.get("included", []) if i["type"] == "subscriptions"][:MAX_PRODUCTS]
        for sub in subs:
            add(self.get(f"/v1/subscriptions/{sub['id']}/subscriptionLocalizations", {"limit": 50})["data"])
        return names

    def fetch_metadata(self, app_id: int) -> dict:
        """{version, date, locales: {locale: {title, subtitle, keywords, iap_names}}}"""
        versions = self.get(f"/v1/apps/{app_id}/appStoreVersions", {"filter[platform]": "IOS", "limit": 20})["data"]
        if not versions:
            raise AscError("У приложения нет версий в App Store Connect")
        version = _pick_live(versions, ("appStoreState", "appVersionState"))

        locales: dict[str, dict] = {}
        for loc in self.get(f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations",
                            {"limit": 200})["data"]:
            a = loc["attributes"]
            locales.setdefault(a["locale"], {})["keywords"] = a.get("keywords") or ""

        infos = self.get(f"/v1/apps/{app_id}/appInfos", {"limit": 10})["data"]
        if infos:
            info = _pick_live(infos, ("appStoreState", "state"))
            for loc in self.get(f"/v1/appInfos/{info['id']}/appInfoLocalizations", {"limit": 200})["data"]:
                a = loc["attributes"]
                entry = locales.setdefault(a["locale"], {})
                entry["title"] = a.get("name") or ""
                entry["subtitle"] = a.get("subtitle") or ""

        try:
            products = self._product_names(app_id)
        except AscError:
            products = {}  # the key's role may not cover in-app purchases
        for loc, names in products.items():
            locales.setdefault(loc, {})["iap_names"] = names

        attrs = version["attributes"]
        return {"version": attrs.get("versionString", "?"),
                "date": (attrs.get("createdDate") or "")[:10], "locales": locales}


def _pick_live(items: list[dict], state_attrs: tuple[str, ...]) -> dict:
    def priority(item: dict) -> int:
        attrs = item["attributes"]
        state = next((attrs[a] for a in state_attrs if attrs.get(a)), None)
        return LIVE_STATES.index(state) if state in LIVE_STATES else len(LIVE_STATES)

    return min(items, key=priority)
