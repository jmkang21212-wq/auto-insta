"""Facebook 로그인 토큰 점검 + 음악 붙이기 시험. 아무것도 게시하지 않습니다.

컨테이너(게시 전 단계)만 만들고 media_publish는 부르지 않습니다. 컨테이너는 24시간 뒤 저절로 사라집니다.
필요: FB_ACCESS_TOKEN (Graph API 탐색기에서 만든 사용자 토큰). 토큰 값은 출력하지 않습니다.

  python tools/fb_probe.py

확인하는 것
1. 토큰: 만료 시각, 받은 권한
2. 연결: 페이스북 페이지와 거기 연결된 인스타 비즈니스 계정 ID
3. 음악 목록: API로 고를 수 있는 곡이 있는지, 앞 5곡
4. 캐러셀 + 곡 / 사진 1장 + 곡 / 릴스 + 곡: 각각 API가 받아 주는지, 거절하면 무슨 말로 거절하는지
"""
from __future__ import annotations

import json
import os
import time

import requests

G = "https://graph.facebook.com/v26.0"
RAW = "https://raw.githubusercontent.com/jmkang21212-wq/auto-insta/main"
IMAGES = [f"{RAW}/posts/2026-10-27/20261023000331/01.jpg", f"{RAW}/posts/2026-10-27/20261023000331/02.jpg"]
VIDEO = os.getenv("PROBE_VIDEO_URL", f"{RAW}/assets/probe/probe.mp4")


def call(method: str, path: str, **params) -> dict:
    params["access_token"] = os.environ["FB_ACCESS_TOKEN"]
    r = requests.request(method, f"{G}/{path}", params=params if method == "GET" else None,
                         data=params if method == "POST" else None, timeout=60)
    try:
        return r.json()
    except ValueError:
        return {"error": {"message": r.text[:200]}}


def brief(j: dict) -> dict:
    e = j.get("error")
    return {"ok": False, "code": e.get("code"), "sub": e.get("error_subcode"), "msg": e.get("message")} if e else {"ok": True, **j}


def wait(cid: str) -> str:
    for _ in range(20):
        st = call("GET", cid, fields="status_code,status")
        if st.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"):
            return f"{st.get('status_code')} {st.get('status', '')}".strip()
        time.sleep(3)
    return "IN_PROGRESS (시간 초과)"


def main() -> int:
    if not os.getenv("FB_ACCESS_TOKEN"):
        print("FB_ACCESS_TOKEN 없음: 깃허브 Secrets에 넣은 뒤 다시 실행")
        return 0
    out: dict = {}
    d = call("GET", "debug_token", input_token=os.environ["FB_ACCESS_TOKEN"]).get("data", {})
    out["token"] = {k: d.get(k) for k in ("type", "application", "is_valid", "expires_at", "data_access_expires_at", "scopes")}
    perms = call("GET", "me/permissions").get("data", [])
    out["granted"] = sorted(p["permission"] for p in perms if p.get("status") == "granted")
    pages = call("GET", "me/accounts", fields="id,name,instagram_business_account{id,username}").get("data", [])
    out["pages"] = [{"page": p.get("name"), "page_id": p.get("id"), "ig": p.get("instagram_business_account")} for p in pages]
    ig = next((p["instagram_business_account"]["id"] for p in pages if p.get("instagram_business_account")), None)
    if not ig:
        out["stop"] = "인스타 계정이 연결된 페이지가 없음 (페이지 연결 또는 토큰 만들 때 페이지·인스타 선택 확인)"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    songs = call("GET", "ig_audio", audio_type="music", user_id=ig)
    items = songs.get("data", [])
    out["music"] = brief(songs) if "error" in songs else {
        "count_first_page": len(items), "fields": sorted(items[0].keys()) if items else [],
        "first5": [{k: s.get(k) for k in ("id", "audio_id", "title", "display_artist", "artist", "duration_in_ms", "duration")
                    if s.get(k) is not None} for s in items[:5]]}
    aid = next((s.get("audio_id") or s.get("id") for s in items), None)
    if not aid:
        out["stop"] = "고를 수 있는 곡이 없어 첨부 시험은 건너뜀"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    audio = json.dumps({"audio_id": str(aid), "audio_volume": 100, "video_volume": 50})

    kids = [call("POST", f"{ig}/media", image_url=u, is_carousel_item="true").get("id") for u in IMAGES]
    car = call("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(k for k in kids if k), caption="probe",
               audio_configuration=audio)
    out["carousel_with_music"] = brief(car)
    if car.get("id"):
        out["carousel_with_music"]["status"] = wait(car["id"])
        out["carousel_with_music"]["fields_back"] = call("GET", car["id"], fields="id,status_code")
    img = call("POST", f"{ig}/media", image_url=IMAGES[0], caption="probe", audio_configuration=audio)
    out["image_with_music"] = brief(img)
    reel = call("POST", f"{ig}/media", media_type="REELS", video_url=VIDEO, caption="probe", audio_configuration=audio)
    out["reel_with_music"] = brief(reel)
    if reel.get("id"):
        out["reel_with_music"]["status"] = wait(reel["id"])
    out["published"] = "없음 (media_publish 호출 안 함)"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
