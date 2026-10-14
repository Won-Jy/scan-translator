"""Google Drive 입출력.

Google Cloud 프로젝트를 따로 만들 필요 없이, Apps Script 웹앱이
자기 OAuth 토큰(Drive 권한)을 비밀키 확인 후 빌려준다.
토큰은 1시간짜리라 만료되면 자동으로 다시 받아온다.
"""
import json

import requests

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class Gas:
    def __init__(self, url: str, secret: str):
        self.url, self.secret = url, secret

    def call(self, action: str, **kw) -> dict:
        r = requests.post(self.url, data=json.dumps({"secret": self.secret, "action": action, **kw}),
                          headers={"Content-Type": "text/plain"}, timeout=60)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"Apps Script 응답이 JSON이 아닙니다 (배포 설정 확인): {r.text[:200]}")
        if not data.get("ok"):
            raise RuntimeError(f"Apps Script 오류: {data.get('error')}")
        return data

    def token(self) -> str:
        return self.call("token")["token"]


class Drive:
    def __init__(self, gas: Gas):
        self.gas = gas
        self._token = gas.token()

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        for attempt in range(2):
            headers = kw.pop("headers", {}) | {"Authorization": f"Bearer {self._token}"}
            r = requests.request(method, url, headers=headers, timeout=300, **kw)
            if r.status_code == 401 and attempt == 0:
                self._token = self.gas.token()
                kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Drive {method} 실패 {r.status_code}: {r.text[:300]}")
            return r
        raise RuntimeError("Drive 인증 실패")

    def download(self, file_id: str) -> bytes:
        return self._req("GET", f"{API}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}).content

    def find(self, folder_id: str, name: str) -> str | None:
        q = f"'{folder_id}' in parents and name = '{name.replace(chr(39), chr(92) + chr(39))}' and trashed = false"
        r = self._req("GET", API, params={"q": q, "fields": "files(id)", "supportsAllDrives": "true",
                                          "includeItemsFromAllDrives": "true"})
        files = r.json().get("files", [])
        return files[0]["id"] if files else None

    def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> str:
        """같은 이름이 있으면 새 버전으로 덮어쓰고, 없으면 만든다. 파일 ID 반환."""
        existing = self.find(folder_id, name)
        if existing:
            r = self._req("PATCH", f"{UPLOAD}/{existing}",
                          params={"uploadType": "resumable", "supportsAllDrives": "true"},
                          headers={"Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Type": mime}, data="{}")
        else:
            meta = json.dumps({"name": name, "parents": [folder_id]})
            r = self._req("POST", UPLOAD, params={"uploadType": "resumable", "supportsAllDrives": "true"},
                          headers={"Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Type": mime}, data=meta.encode())
        session = r.headers["Location"]
        r2 = requests.put(session, data=data, headers={"Content-Type": mime}, timeout=600)
        if r2.status_code >= 400:
            raise RuntimeError(f"Drive 업로드 실패 {r2.status_code}: {r2.text[:300]}")
        return r2.json()["id"]

    def load_json(self, folder_id: str, name: str) -> dict:
        fid = self.find(folder_id, name)
        if not fid:
            return {}
        try:
            return json.loads(self.download(fid).decode("utf-8"))
        except ValueError:
            return {}

    def save_json(self, folder_id: str, name: str, obj: dict) -> None:
        self.upload(folder_id, name, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")
