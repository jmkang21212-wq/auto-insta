"""참고 계정 비교: 다른 공모주·재테크 계정의 소개글과 최근 캡션을 공식 API로 모아 숫자로 비교합니다.

인스타 웹페이지는 긁지 않습니다. 메타가 허용한 Business Discovery(공개 필드만)로 읽습니다.
필요한 것 (Facebook 로그인으로 연결한 뒤):
  FB_ACCESS_TOKEN  권한 instagram_basic, instagram_manage_insights, pages_read_engagement
  IG_BUSINESS_ID   우리 인스타 계정의 비즈니스 계정 ID (GET /{page-id}?fields=instagram_business_account)

  python tools/benchmark_accounts.py 계정1 계정2 ... [--media 12]   # 참고 계정 비교
  python tools/benchmark_accounts.py --self                         # 우리 캡션(posts/*/caption.txt)만 같은 기준으로

결과: out/benchmark/<날짜>.json (원자료 그대로) + 표 출력. 좋아요·댓글 비율은 표본이 작아 참고용입니다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gongsi.verify import CAPTION_LINE_MAX, line_width  # noqa: E402

GRAPH = "https://graph.facebook.com/v26.0"
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")
CTA = {"저장": r"저장", "댓글": r"댓글", "팔로우": r"팔로우|팔로잉", "공유": r"공유", "링크": r"링크|프로필 링크|프로필의 링크"}
DISCLAIMER = re.compile(r"투자\s*권유|투자\s*판단|책임")


def caption_stats(cap: str) -> dict:
    lines = cap.split("\n")
    text = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    return {
        "chars": len(cap),
        "lines": len([l for l in lines if l.strip()]),
        "blank_lines": sum(1 for l in lines if not l.strip()),
        "max_line_width": round(max((line_width(l) for l in text), default=0), 1),
        "over_width_lines": sum(1 for l in text if line_width(l) > CAPTION_LINE_MAX),
        "emoji": len(EMOJI.findall(cap)),
        "hashtags": len(re.findall(r"#\w+", cap)),
        "question": "?" in cap,
        "cta": [k for k, p in CTA.items() if re.search(p, cap)],
        "disclaimer": bool(DISCLAIMER.search(cap)),
        "first_line": next((l.strip() for l in lines if l.strip()), "")[:60],
    }


def bio_stats(bio: str) -> dict:
    lines = [l for l in (bio or "").split("\n") if l.strip()]
    return {"chars": len(bio or ""), "lines": len(lines), "emoji": len(EMOJI.findall(bio or "")),
            "disclaimer": bool(DISCLAIMER.search(bio or "")), "text": bio or ""}


def fetch(username: str, n: int) -> dict:
    import requests

    tok, me = os.environ["FB_ACCESS_TOKEN"], os.environ["IG_BUSINESS_ID"]
    fields = (f"business_discovery.username({username}){{username,name,biography,website,followers_count,media_count,"
              f"media.limit({n}){{caption,like_count,comments_count,timestamp,media_type,media_product_type,permalink}}}}")
    r = requests.get(f"{GRAPH}/{me}", params={"fields": fields, "access_token": tok}, timeout=30)
    data = r.json()
    if "error" in data:  # 없는 아이디, 개인 계정, 연령 제한 계정은 여기로 온다
        return {"username": username, "error": data["error"].get("message")}
    return data["business_discovery"]


def summarize(acc: dict) -> dict:
    media = acc.get("media", {}).get("data", [])
    caps = [caption_stats(m.get("caption") or "") for m in media]
    fol = acc.get("followers_count") or 0
    med = lambda k: statistics.median([c[k] for c in caps]) if caps else None  # noqa: E731
    eng = [((m.get("like_count") or 0) + (m.get("comments_count") or 0)) / fol * 100 for m in media if fol]
    return {
        "username": acc.get("username"), "name": acc.get("name"), "followers": fol, "posts": acc.get("media_count"),
        "bio": bio_stats(acc.get("biography", "")),
        "caption_median": {k: med(k) for k in ("chars", "lines", "blank_lines", "max_line_width", "emoji", "hashtags")},
        "cta_share": {k: round(sum(k in c["cta"] for c in caps) / len(caps), 2) for k in CTA} if caps else {},
        "disclaimer_share": round(sum(c["disclaimer"] for c in caps) / len(caps), 2) if caps else None,
        "formats": sorted({f"{m.get('media_product_type')}/{m.get('media_type')}" for m in media}),
        "engagement_pct_median": round(statistics.median(eng), 2) if eng else None,
        "first_lines": [c["first_line"] for c in caps[:5]],
    }


def own_captions() -> list[dict]:
    return [{"post": p.parent.name, **caption_stats(p.read_text(encoding="utf-8"))}
            for p in sorted((ROOT / "posts").glob("*/*/caption.txt"))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("usernames", nargs="*")
    ap.add_argument("--media", type=int, default=12, help="계정마다 최근 게시물 몇 개")
    ap.add_argument("--self", action="store_true", help="우리 캡션만 같은 기준으로 계산")
    args = ap.parse_args(argv)

    out = {"date": date.today().isoformat(), "own": own_captions(), "accounts": []}
    if not args.self:
        for u in args.usernames:
            raw = fetch(u.lstrip("@"), args.media)
            out["accounts"].append({"raw": raw, "summary": summarize(raw) if "error" not in raw else {"error": raw["error"]}})
    path = ROOT / "out" / "benchmark" / f"{out['date']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"saved": str(path), "own": out["own"], "accounts": [a["summary"] for a in out["accounts"]]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
