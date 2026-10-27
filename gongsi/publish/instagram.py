"""인스타그램 캐러셀 게시 (Instagram API with Instagram Login, graph.instagram.com).

2026-10-27 메타 문서(developers.facebook.com/documentation/instagram-platform/content-publishing) 기준:
  1. POST /<IG_ID>/media  image_url=<공개 JPEG URL>  is_carousel_item=true   → 자식 컨테이너 id (장마다)
  2. POST /<IG_ID>/media  media_type=CAROUSEL  children=<id,id,...(최대 20)>  caption=...  → 캐러셀 컨테이너 id
  3. GET  /<컨테이너 id>?fields=status_code  → FINISHED 될 때까지 (1분 간격, 최대 5분 권장)
  4. POST /<IG_ID>/media_publish  creation_id=<캐러셀 id>  → 게시물 id
  - 이미지는 JPEG만, 공개 URL이어야 하고, 캐러셀은 첫 장 비율로 잘린다 (우리는 전부 4:5라 그대로)
  - 한도: 문서에 24시간당 100개(캐러셀은 1개로 셈)와 50개가 같이 적혀 있음. GET /<IG_ID>/content_publishing_limit 로 확인
  - 토큰: 장기 토큰 60일. GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=... 으로 연장 (24시간 지난 토큰만)

필요한 것: 인스타그램 프로페셔널(비즈니스/크리에이터) 계정, Meta 개발자 앱에 'Instagram' 제품 추가,
  권한 instagram_business_basic + instagram_business_content_publish, 그 계정의 장기 액세스 토큰(IG_ACCESS_TOKEN)과 IG 사용자 ID(IG_USER_ID).
  페이스북 로그인 방식(graph.facebook.com, 페이지 연결)을 쓰면 IG_GRAPH_HOST=graph.facebook.com 으로 바꾸면 된다.
"""
from __future__ import annotations

import os
import time

import requests


def _host() -> str:
    return os.getenv("IG_GRAPH_HOST", "graph.instagram.com")


def _ver() -> str:
    return os.getenv("IG_API_VERSION", "v23.0")  # 확인 필요: 앱 대시보드의 API 버전에 맞출 것


def _token() -> str:
    return os.environ["IG_ACCESS_TOKEN"]


_IG_ID: str | None = None


def _ig() -> str:
    """게시에 쓰는 인스타그램 프로페셔널 계정 ID. IG_USER_ID가 비어 있으면 토큰으로 /me 를 불러 user_id를 쓴다."""
    global _IG_ID
    if os.getenv("IG_USER_ID"):
        return os.environ["IG_USER_ID"]
    if _IG_ID is None:
        me = _call("GET", "me", fields="user_id,username")
        _IG_ID = str(me.get("user_id") or me["id"])
    return _IG_ID


def _call(method: str, path: str, **params) -> dict:
    url = f"https://{_host()}/{_ver()}/{path}"
    params["access_token"] = _token()
    r = requests.request(method, url, params=params if method == "GET" else None, data=params if method != "GET" else None, timeout=60)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"instagram {path}: HTTP {r.status_code} {r.text[:200]}")
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"instagram {path}: ({e.get('code')}/{e.get('error_subcode')}) {e.get('message')}")
    return data


def whoami() -> dict:
    return _call("GET", "me", fields="id,user_id,username,account_type,media_count")


def publishing_limit() -> dict:
    return _call("GET", f"{_ig()}/content_publishing_limit", fields="quota_usage,config")


def wait_finished(container_id: str, minutes: int = 5) -> str:
    for _ in range(minutes * 2):
        st = _call("GET", container_id, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return st
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"컨테이너 {container_id} 상태 {st}")
        time.sleep(30)
    raise RuntimeError(f"컨테이너 {container_id} 가 {minutes}분 안에 FINISHED 되지 않음")


def publish_carousel(image_urls: list[str], caption: str) -> dict:
    """게시하고 {"media_id", "permalink", "children"} 를 돌려준다. 실패하면 RuntimeError."""
    if not 2 <= len(image_urls) <= 20:
        raise ValueError("캐러셀은 2~20장")
    children = []
    for u in image_urls:
        children.append(_call("POST", f"{_ig()}/media", image_url=u, is_carousel_item="true")["id"])
    carousel = _call("POST", f"{_ig()}/media", media_type="CAROUSEL", children=",".join(children), caption=caption[:2200])["id"]
    wait_finished(carousel)
    media_id = _call("POST", f"{_ig()}/media_publish", creation_id=carousel)["id"]
    permalink = _call("GET", media_id, fields="permalink").get("permalink")
    return {"media_id": media_id, "permalink": permalink, "children": children}


def refresh_token() -> dict:
    """장기 토큰 연장. 돌려주는 access_token을 저장소 Secret에 다시 넣어야 한다 (Actions에서는 gh secret set)."""
    r = requests.get(f"https://{_host()}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _token()}, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"refresh_access_token: {data['error'].get('message')}")
    return data  # {"access_token", "token_type", "expires_in"(초)}
