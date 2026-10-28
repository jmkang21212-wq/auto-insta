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

import json
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


def publish_reel(video_url: str, caption: str, music: bool = True) -> dict:
    """릴스 게시. 영상은 공개 URL(깃허브 raw), 9:16 H.264. 피드에도 보이게 share_to_feed=true.
    music=True이고 페이스북 로그인 토큰(FB_USER_TOKEN)이 있으면 메타 음악(Audio API)에서 곡을 골라 붙여 게시한다.
    토큰이 없거나 음악 쪽이 실패하면 지금처럼 Instagram 로그인으로 소리 없이 게시하고, 이유를 music_error에 남긴다"""
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption[:2200], "share_to_feed": "true", "thumb_offset": "500"}
    err = None
    if music and fb_ready():
        try:
            return _publish_reel_fb(params)
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
    container = _call("POST", f"{_ig()}/media", **params)["id"]
    wait_finished(container, minutes=10)  # 영상은 서버 처리 시간이 걸린다
    media_id = _call("POST", f"{_ig()}/media_publish", creation_id=container)["id"]
    permalink = _call("GET", media_id, fields="permalink").get("permalink")
    return {"media_id": media_id, "permalink": permalink, "container": container, "format": "reel", "audio_id": None,
            **({"music_error": err} if err else {})}


# ── 음악: Instagram Audio API (2026-06-01 공개, 페이스북 로그인 전용) ──────────────────────────────
# 메타 문서 developers.facebook.com/docs/instagram-platform/content-publishing/audio-api (2026-06-16 갱신, 10-28 확인):
#   필요: 비즈니스/크리에이터 계정 + 연결된 페이스북 페이지 + 페이스북 로그인으로 받은 instagram_basic·instagram_content_publish
#         권한의 사용자 토큰. Instagram 로그인(지금 IG_ACCESS_TOKEN)으로는 안 됨. 릴스에만 (캐러셀·사진은 음악 불가)
#   GET  graph.facebook.com/<ver>/ig_audio?audio_type=music&user_id=<IG id>[&search_query=…]  → {"audio": [{audio_id, title,
#        display_artist, duration_in_ms, …}]}  검색어가 없으면 인기 곡. 제3자 사용이 허락된 곡만 나옴(메타 사운드 컬렉션 등)
#   POST /<IG id>/media  media_type=REELS … audio_configuration={"audio_id", "audio_volume", "video_volume", "should_loop_audio"}
FB_HOST = "graph.facebook.com"


def fb_ready() -> bool:
    return bool(os.getenv("FB_USER_TOKEN"))


def _fb(method: str, path: str, **params) -> dict:
    url = f"https://{FB_HOST}/{_ver()}/{path}"
    params["access_token"] = os.environ["FB_USER_TOKEN"]
    r = requests.request(method, url, params=params if method == "GET" else None, data=params if method != "GET" else None, timeout=60)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"facebook {path}: HTTP {r.status_code} {r.text[:200]}")
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"facebook {path}: ({e.get('code')}/{e.get('error_subcode')}) {e.get('message')}")
    return data


_FB_IG_ID: str | None = None


def fb_ig_user_id() -> str:
    """페이스북 로그인 쪽 인스타 계정 ID(Instagram 로그인 쪽 ID와 다름). IG_FB_USER_ID가 없으면 토큰의 페이지에서 찾는다"""
    global _FB_IG_ID
    if os.getenv("IG_FB_USER_ID"):
        return os.environ["IG_FB_USER_ID"]
    if _FB_IG_ID is None:
        pages = _fb("GET", "me/accounts", fields="name,instagram_business_account{id,username}").get("data", [])
        accts = [p["instagram_business_account"] for p in pages if p.get("instagram_business_account")]
        want = os.getenv("IG_USERNAME", "ipo_view")
        pick = next((a for a in accts if a.get("username") == want), accts[0] if accts else None)
        if not pick:
            raise RuntimeError("토큰의 페이스북 페이지에 연결된 인스타 계정이 없음 (인스타 설정에서 페이지 연결 필요)")
        _FB_IG_ID = str(pick["id"])
    return _FB_IG_ID


def _used_audio_path():
    from gongsi.publish.store import ROOT
    return ROOT / "state" / "used_audio.json"


def pick_music(query: str | None = None, min_ms: int = 10_000, recent: int = 20) -> dict:
    """인기 곡(검색어 없을 때)에서 최근 쓴 곡을 빼고 10초 이상인 첫 곡"""
    f = _used_audio_path()
    used = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    params = {"audio_type": "music", "user_id": fb_ig_user_id()}
    if query:
        params["search_query"] = query
    got = _fb("GET", "ig_audio", **params)
    items = got.get("audio") or got.get("data") or []
    fresh = [a for a in items if a.get("audio_id") and str(a["audio_id"]) not in used[-recent:]
             and (a.get("duration_in_ms") or min_ms) >= min_ms]
    if not (fresh or items):
        raise RuntimeError("ig_audio 결과가 비어 있음")
    return (fresh or items)[0]


def _publish_reel_fb(params: dict) -> dict:
    ig = fb_ig_user_id()
    track = pick_music(os.getenv("REEL_MUSIC_QUERY") or None)
    # 우리 영상은 소리 없는 트랙이라 영상 소리 0, 음악 100
    params = {**params, "audio_configuration": json.dumps({"audio_id": str(track["audio_id"]), "audio_volume": 100,
                                                           "video_volume": 0, "should_loop_audio": True})}
    container = _fb("POST", f"{ig}/media", **params)["id"]
    for _ in range(20):  # 영상 처리 최대 10분
        st = _fb("GET", container, fields="status_code").get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"컨테이너 {container} 상태 {st}")
        time.sleep(30)
    else:
        raise RuntimeError(f"컨테이너 {container} 가 10분 안에 FINISHED 되지 않음")
    media_id = _fb("POST", f"{ig}/media_publish", creation_id=container)["id"]
    permalink = _fb("GET", media_id, fields="permalink").get("permalink")
    f = _used_audio_path()
    used = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps((used + [str(track["audio_id"])])[-50:]), encoding="utf-8")
    return {"media_id": media_id, "permalink": permalink, "container": container, "format": "reel", "host": FB_HOST,
            "audio_id": str(track["audio_id"]), "audio_title": track.get("title"), "audio_artist": track.get("display_artist")}


def refresh_token() -> dict:
    """장기 토큰 연장. 돌려주는 access_token을 저장소 Secret에 다시 넣어야 한다 (Actions에서는 gh secret set)."""
    r = requests.get(f"https://{_host()}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _token()}, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"refresh_access_token: {data['error'].get('message')}")
    return data  # {"access_token", "token_type", "expires_in"(초)}
