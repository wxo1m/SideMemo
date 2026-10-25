"""Supabase synchronization client for SideMemo.

Uses only the Python standard library (urllib) so the PyInstaller bundle stays
small and needs no extra dependencies. Talks to two Supabase services:

* GoTrue  (``/auth/v1``)      - email sign-up / sign-in / refresh / sign-out
* PostgREST (``/rest/v1``)    - the ``sidememo_notes`` and ``sidememo_settings``

Every row is scoped to the signed-in user by row level security, so the public
anon key can safely ship inside the desktop app and the web page.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 15
NOTES_TABLE = "sidememo_notes"
SETTINGS_TABLE = "sidememo_settings"


class SyncError(Exception):
    """Raised when Supabase rejects a request or the network is unreachable."""


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


class SupabaseClient:
    """Small REST client for the two Supabase endpoints SideMemo needs."""

    def __init__(self, url: str = "", anon_key: str = "", session: dict | None = None):
        self.url = normalize_url(url)
        self.anon_key = (anon_key or "").strip()
        self.session = dict(session or {})

    # ------------------------------------------------------------------ core
    @property
    def configured(self) -> bool:
        return bool(self.url and self.anon_key)

    @property
    def email(self) -> str:
        return str(self.session.get("email", ""))

    @property
    def user_id(self) -> str:
        return str(self.session.get("user_id", ""))

    @property
    def signed_in(self) -> bool:
        return bool(self.session.get("access_token") and self.user_id)

    def _call(self, method: str, path: str, *, body=None, params=None,
              token: str | None = None, prefer: str | None = None):
        if not self.configured:
            raise SyncError("Supabase 주소와 anon key를 먼저 입력해 주세요.")
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "apikey": self.anon_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise SyncError(self._describe_http_error(exc)) from None
        except urllib.error.URLError as exc:
            raise SyncError(f"네트워크에 연결하지 못했습니다 ({exc.reason}).") from None
        except TimeoutError:
            raise SyncError("요청 시간이 초과되었습니다.") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _describe_http_error(exc: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = (payload.get("msg") or payload.get("error_description")
                      or payload.get("message") or payload.get("error") or "")
            if not detail and payload.get("error_code"):
                detail = str(payload["error_code"])
        friendly = {
            400: "요청이 올바르지 않습니다",
            401: "이메일 또는 비밀번호가 올바르지 않습니다",
            403: "권한이 없습니다",
            404: "테이블을 찾을 수 없습니다 (SQL 설정을 실행했는지 확인해 주세요)",
            409: "이미 존재하는 계정입니다",
            422: "입력 값을 확인해 주세요",
            429: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요",
        }
        base = friendly.get(exc.code, f"서버 오류 ({exc.code})")
        return f"{base} - {detail}" if detail else base

    # ------------------------------------------------------------------ auth
    def _store_session(self, payload: dict, email: str) -> dict:
        user = payload.get("user") or {}
        expires_in = payload.get("expires_in") or 3600
        self.session = {
            "access_token": payload.get("access_token", ""),
            "refresh_token": payload.get("refresh_token", ""),
            "expires_at": time.time() + float(expires_in),
            "user_id": user.get("id", self.session.get("user_id", "")),
            "email": user.get("email") or email or self.session.get("email", ""),
        }
        return self.session

    def sign_up(self, email: str, password: str) -> dict:
        payload = self._call("POST", "/auth/v1/signup",
                             body={"email": email, "password": password})
        payload = payload or {}
        if payload.get("access_token"):
            return self._store_session(payload, email)
        # Email confirmation is enabled on the project.
        raise SyncError("가입 확인 메일을 보냈습니다. 메일의 링크를 누른 뒤 로그인해 주세요.")

    def sign_in(self, email: str, password: str) -> dict:
        payload = self._call("POST", "/auth/v1/token",
                             params={"grant_type": "password"},
                             body={"email": email, "password": password})
        return self._store_session(payload or {}, email)

    def refresh(self) -> dict:
        refresh_token = self.session.get("refresh_token")
        if not refresh_token:
            raise SyncError("다시 로그인해 주세요.")
        payload = self._call("POST", "/auth/v1/token",
                             params={"grant_type": "refresh_token"},
                             body={"refresh_token": refresh_token})
        if not payload or not payload.get("access_token"):
            self.session = {}
            raise SyncError("로그인 정보가 만료되었습니다. 다시 로그인해 주세요.")
        return self._store_session(payload, self.email)

    def sign_out(self):
        token = self.session.get("access_token")
        if token and self.configured:
            try:
                self._call("POST", "/auth/v1/logout", token=token)
            except SyncError:
                pass
        self.session = {}

    def token(self) -> str | None:
        access_token = self.session.get("access_token")
        if not access_token:
            return None
        expires_at = float(self.session.get("expires_at") or 0)
        if expires_at and time.time() > expires_at - 60:
            try:
                self.refresh()
            except SyncError:
                return self.session.get("access_token")
        return self.session.get("access_token")

    # ------------------------------------------------------------------ data
    def pull_notes(self, since: str | None = None) -> list[dict]:
        params = {
            "select": "note_id,position,payload,deleted,updated_at",
            "order": "updated_at.asc",
        }
        if since:
            params["updated_at"] = f"gt.{since}"
        rows = self._call("GET", f"/rest/v1/{NOTES_TABLE}", params=params,
                          token=self.token())
        return rows or []

    def push_notes(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        result = self._call("POST", f"/rest/v1/{NOTES_TABLE}", body=rows,
                            token=self.token(),
                            prefer="resolution=merge-duplicates,return=representation")
        return result or []

    def delete_note(self, note_id: str) -> None:
        self._call("DELETE", f"/rest/v1/{NOTES_TABLE}",
                   params={"note_id": f"eq.{note_id}"}, token=self.token())

    def pull_settings(self) -> dict | None:
        rows = self._call("GET", f"/rest/v1/{SETTINGS_TABLE}",
                          params={"select": "payload,updated_at", "limit": "1"},
                          token=self.token())
        return rows[0] if rows else None

    def push_settings(self, payload: dict) -> dict | None:
        rows = self._call("POST", f"/rest/v1/{SETTINGS_TABLE}",
                          body={"user_id": self.user_id, "payload": payload},
                          token=self.token(),
                          prefer="resolution=merge-duplicates,return=representation")
        return rows[0] if rows else None
