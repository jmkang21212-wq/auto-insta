"""훅 릴스: 위에 제목(훅) + 둘째 줄 숫자, 가운데 스톡 영상 + 밈 자막, 아래 '▼ 자세한 내용은 아래에 ▼' (ekke.now 방식).

영상은 훅에 나오는 일상 물건으로 Pixabay에서 고른다(검색어는 훅이 정함). 같은 영상을 다시 쓰지 않게
state/used_media.json에 쓴 영상 ID를 남긴다. 영상 왼쪽 위에 '자료 화면'을 붙여 회사 실제 모습으로 오해하지 않게 한다.
소리는 비워 두고, 음악은 게시할 때 인스타 음악 API(audio_configuration)로 붙인다.

  python -m gongsi.reel <제목> <둘째 줄> <자막> <영상 검색어> <출력.mp4>   # 단독 시험
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from gongsi.config import settings
from gongsi.verify import line_width

ROOT = settings.templates_dir.parent.parent
USED = ROOT / "state" / "used_media.json"
CACHE = settings.out_dir / "videos"
W, H, SECONDS = 1080, 1920, 7
VIDEO_TOP, VIDEO_H = 600, 780  # 영상 자리. 그 아래 '자세한 내용' 줄은 릴스 화면의 아이디·캡션 글자에 가리지 않을 높이


def used_ids() -> set[str]:
    try:
        return set(json.loads(USED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def mark_used(media_id: str) -> None:
    ids = sorted(used_ids() | {media_id})
    USED.parent.mkdir(parents=True, exist_ok=True)
    USED.write_text(json.dumps(ids, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


# 검색어에서 뺄 꾸밈말: 이 말들만 맞는 엉뚱한 영상(예: 'water flowing' → 바다 파도)을 막는다
GENERIC = {"water", "flowing", "close", "closeup", "close-up", "up", "macro", "slow", "motion", "glowing", "spinning",
           "light", "lights", "background", "abstract", "video", "footage", "the", "a", "of", "and", "with", "in", "on"}


def _keywords(query: str) -> list[str]:
    return [w for w in query.lower().replace("-", " ").split() if len(w) >= 3 and w not in GENERIC]


PEOPLE = {"woman", "man", "girl", "boy", "people", "person", "lady", "portrait", "face", "model", "couple", "child", "kid", "baby"}
# 금융 계정에 안 맞는 장면. '샤워기'로 찾으면 샤워하는 여성 영상(seductive·glamour 태그)이 섞여 나와서 넣었다
UNFIT = {"seductive", "glamour", "sexy", "sensual", "erotic", "nude", "naked", "lingerie", "bikini", "underwear", "swimsuit"}


def _score(tags: str, words: list[str]) -> int:
    """태그가 훅의 물건과 얼마나 맞는지: 3 '두 단어 그대로'(shower head), 2 '첫 명사 그대로'(shower),
    1 '첫 명사가 들어간 다른 말'(rain shower), 0 없음, -1 금융 계정에 안 맞는 장면"""
    toks = [t.strip().lower() for t in tags.split(",")]
    if any(w in UNFIT for t in toks for w in t.split()):
        return -1
    if len(words) > 1 and " ".join(words[:2]) in toks:
        return 3
    if words[0] in toks:
        return 2
    return 1 if any(words[0] in t for t in toks) else 0


def _has_people(tags: str) -> bool:
    return any(w in PEOPLE for t in tags.lower().split(",") for w in t.split())


def find_video(query: str, skip: set[str] | None = None) -> tuple[Path, dict] | None:
    """Pixabay 영상 검색 → 아직 안 쓴 영상 중 훅의 물건이 태그에 가장 잘 맞는 것을 받아 둔다.
    검색어를 넓혀 가며(전체 → 핵심 명사들 → 첫 명사) 모으고, 점수 2 이상만 쓴다(짧은 영상은 반복 재생)."""
    key = os.getenv("PIXABAY_API_KEY", "")
    words = _keywords(query)
    if not key or not words:
        return None
    skip = skip if skip is not None else used_ids()
    best = None
    for q in dict.fromkeys([query, " ".join(words), words[0]]):
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "per_page": 50, "safesearch": "true"}, timeout=30)
        for hit in r.json().get("hits", []):
            v = hit["videos"].get("medium") or hit["videos"].get("small")
            if f"pixabay:{hit['id']}" in skip or not v or not v.get("url") or min(v["width"], v["height"]) < 540 or hit.get("duration", 0) < 4:
                continue
            # 사람이 나와도 쓴다. 다만 물건이 주인공인 훅이면 같은 점수에서 사람 없는 영상을 먼저 고른다
            rank = (_score(hit.get("tags", ""), words), not _has_people(hit.get("tags", "")), min(v["width"], v["height"]))
            if rank[0] >= 2 and (best is None or rank > best[0]):
                best = (rank, hit, v, q)
    if not best:
        return None
    _, hit, v, q = best
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"pixabay_{hit['id']}.mp4"
    if not path.exists():
        path.write_bytes(requests.get(v["url"], timeout=120).content)
    meta = {"id": f"pixabay:{hit['id']}", "query": q, "user": hit.get("user"), "page": hit.get("pageURL"),
            "tags": hit.get("tags"), "people": _has_people(hit.get("tags", "")), "license": "Pixabay Content License"}
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return path, meta


def split_title(text: str, max_w: float = 11.0) -> list[str]:
    """큰 제목을 두 줄로: 어절 단위, 두 줄 폭이 비슷하게"""
    if line_width(text) <= max_w:
        return [text]
    words = text.split(" ")
    best = min(((max(line_width(" ".join(words[:i])), line_width(" ".join(words[i:]))), i) for i in range(1, len(words))))
    return [" ".join(words[:best[1]]), " ".join(words[best[1]:])]


def overlay_png(title: str, second: str, sub: str, out: Path) -> Path:
    """영상 자리만 투명한 글자 판"""
    from playwright.sync_api import sync_playwright

    from gongsi.render import font_css

    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    lines = "<br>".join(esc(x) for x in split_title(title))
    html = f"""<html><head><meta charset=utf-8><style>{font_css()}
html,body{{margin:0;background:transparent}}
.c{{width:{W}px;height:{H}px;position:relative;font-family:'Pretendard','Noto Color Emoji'}}
.top{{position:absolute;left:0;top:0;width:{W}px;height:{VIDEO_TOP}px;background:#0F1115;display:flex;flex-direction:column;align-items:center}}
.bot{{position:absolute;left:0;top:{VIDEO_TOP + VIDEO_H}px;width:{W}px;height:{H - VIDEO_TOP - VIDEO_H}px;background:#0F1115}}
.wm{{margin-top:84px;font-size:72px;font-weight:900;letter-spacing:-.045em;color:#F2F4F6;line-height:1}}
.wm .b{{color:#5EA9FF}} .wm .dot{{position:relative;display:inline-block}}
.wm .dot i{{position:absolute;left:50%;top:.02em;width:.2em;height:.2em;border-radius:50%;background:#F5C84B;transform:translateX(-50%)}}
.t{{margin-top:44px;text-align:center;color:#fff;font-size:84px;font-weight:900;line-height:1.18;letter-spacing:-.035em;word-break:keep-all}}
.s{{margin-top:18px;color:#F5C84B;font-size:60px;font-weight:800;letter-spacing:-.02em}}
.sub{{position:absolute;top:{VIDEO_TOP + VIDEO_H - 120}px;left:0;width:{W}px;text-align:center;color:#fff;font-size:62px;font-weight:900;text-shadow:0 3px 14px #000,0 0 4px #000}}
.tag{{position:absolute;top:{VIDEO_TOP + 24}px;left:28px;font-size:26px;font-weight:600;color:rgba(255,255,255,.85);background:rgba(0,0,0,.45);padding:6px 14px;border-radius:999px}}
.ar{{position:absolute;top:{VIDEO_TOP + VIDEO_H + 70}px;left:0;width:{W}px;text-align:center;color:#5EA9FF;font-size:56px;font-weight:800}}
</style></head><body><div class=c>
<div class=top><div class=wm><span class=b><span class=dot>ı<i></i></span>po</span>v</div>
<div class=t>{lines}</div><div class=s>{esc(second)}</div></div>
<div class=tag>자료 화면</div><div class=sub>{esc(sub)}</div>
<div class=bot></div><div class=ar>▼ 자세한 내용은 아래에 ▼</div></div></body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.set_content(html, wait_until="load")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out), omit_background=True)
        b.close()
    return out


def compose(video: Path, overlay: Path, out: Path, seconds: int = SECONDS) -> Path:
    """배경색 → 가운데 영상(꽉 채워 자름) → 글자 판. 소리는 빈 트랙 (음악은 게시 때 API로)"""
    vf = (f"color=c=0x0F1115:s={W}x{H}:r=30:d={seconds}[bg];"
          f"[0:v]trim=0:{seconds},setpts=PTS-STARTPTS,scale={W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{VIDEO_H},fps=30[v];[bg][v]overlay=0:{VIDEO_TOP}[b1];[b1][1:v]overlay=0:0,format=yuv420p[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(video), "-i", str(overlay),
                    "-f", "lavfi", "-t", str(seconds), "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", vf, "-map", "[out]", "-map", "2:a", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def make_reel(title: str, second: str, sub: str, query: str, out: Path) -> dict:
    """훅 릴스 한 편. 쓴 영상은 used_media에 남긴다. 돌려주는 meta는 캡션 '📷 자료 화면' 줄과 로그용"""
    found = find_video(query)
    if not found:
        raise RuntimeError(f"'{query}' 영상을 못 찾음")
    video, meta = found
    with tempfile.TemporaryDirectory() as tmp:
        compose(video, overlay_png(title, second, sub, Path(tmp) / "overlay.png"), out)
    mark_used(meta["id"])
    return meta


if __name__ == "__main__":
    t, s2, sub, q, o = sys.argv[1:6]
    print(json.dumps(make_reel(t, s2, sub, q, Path(o)), ensure_ascii=False))
