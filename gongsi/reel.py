"""훅 릴스: 위에 제목(훅) + 둘째 줄 숫자, 가운데 스톡 영상 + 밈 자막, 아래 '▼ 자세한 내용은 아래에 ▼' (ekke.now 방식).

영상은 훅에 나오는 일상 물건으로 Pixabay에서 고른다(검색어는 훅이 정함). 같은 영상을 다시 쓰지 않게
state/used_media.json에 쓴 영상 ID를 남긴다. 영상 왼쪽 위에 '자료 화면'을 붙여 회사 실제 모습으로 오해하지 않게 한다.
소리는 비워 두고, 음악은 게시할 때 인스타 음악 API(audio_configuration)로 붙인다.

  python -m gongsi.reel <제목> <둘째 줄> <자막> <영상 검색어> <출력.mp4>   # 단독 시험
"""
from __future__ import annotations

import json
import os
import re
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
# 릴스 화면 안전 영역: 위 14%(약 270px)는 인스타 앱 상단(상태 표시줄·릴스 머리글)에, 아래 약 25%는 아이디·캡션·오디오 줄에 가린다.
# 2026-10-27 사용자 피드백 "아이폰에서 로고 윗부분이 가려짐"으로 로고를 84px → 270px로 내리고 영상 높이를 줄였다.
# (14%는 업계 가이드에 흔히 쓰는 값이고 메타 공식 문서로는 확인 필요)
SAFE_TOP, SAFE_BOTTOM = 270, 1440
VIDEO_TOP, VIDEO_H = 620, 720  # 영상 자리. 그 아래 '자세한 내용' 줄까지 SAFE_BOTTOM 안
# 2026-10-28: 큰 로고는 빼고(사용자: '로고는 안 보여도 돼') 그 자리에 '어디에 쓰이는지' 한 줄. 작은 로고는 영상 오른쪽 위


def used_ids() -> set[str]:
    try:
        return set(json.loads(USED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


# 이번 실행 안에서 이미 고른 영상 (같은 날 여러 회사가 같은 영상을 고르지 않게).
# 파일(state/used_media.json)에는 게시 대기열에 올릴 때(publish stage) 남긴다: 검증에서 떨어진 릴스의 영상까지 막지 않으려고
_RUN_USED: set[str] = set()


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


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w  # slices → slice, coils → coil


def _score(tags: str, words: list[str]) -> int:
    """태그가 훅의 물건과 얼마나 맞는지. 검색어 핵심 명사가 태그에 몇 개 들어 있는지 세고,
    '두 단어 그대로'(shower head)면 2점 더한다. 핵심 명사가 2개 이상이면 2개 이상 맞아야 쓴다
    ('electric heating coil'인데 'electric'만 맞는 우주 영상 같은 걸 막음). 금융 계정에 안 맞는 장면은 -1"""
    toks = [t.strip().lower() for t in tags.split(",")]
    if any(w in UNFIT for t in toks for w in t.split()):
        return -1
    hits = sum(1 for w in words if any(_stem(w) in t for t in toks))
    if hits < min(2, len(words)):
        return 0
    return hits + (2 if len(words) > 1 and " ".join(words[:2]) in toks else 0)


def _has_people(tags: str) -> bool:
    return any(w in PEOPLE for t in tags.lower().split(",") for w in t.split())


MOTION_MIN = 2.5  # 이보다 낮으면 '정적인 영상' (2026-10-28 잰 값: 샤워 물줄기 0.8, 비행기 날개 0.5, 파도 5.5, 기타 연주 8.5, 폭포 26.6)


def motion_score(path: Path, seconds: float = 5.0, w: int = 64, h: int = 36) -> float:
    """영상이 얼마나 움직이는지: 앞 몇 초를 아주 작은 흑백으로 줄여 연속 프레임 밝기 차이의 평균(0~255).
    사용자 피드백(2026-10-28 '영상이 너무 정적'): 같은 물건이면 움직임이 큰 영상을 고르려고 잰다"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-vf", f"scale={w}:{h},fps=6,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (w * h)
    fr = [raw[i * w * h:(i + 1) * w * h] for i in range(n)]
    d = [sum(abs(a - b) for a, b in zip(x, y)) / (w * h) for x, y in zip(fr, fr[1:])]
    return round(sum(d) / len(d), 2) if d else 0.0


def lead_in(path: Path, upto: float = 3.0, w: int = 64, h: int = 36) -> float:
    """영상 앞부분이 비어 있으면(잉크가 들어오기 전 회색 화면 등) 건너뛸 초. 프레임 밝기 표준편차가
    앞 몇 초 중 가장 큰 값의 40%를 처음 넘는 지점. 첫 장면·표지가 빈 화면이 되지 않게 (2026-10-28)"""
    fps = 6
    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(upto + 1), "-i", str(path), "-vf", f"scale={w}:{h},fps={fps},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (w * h)
    stds = []
    for i in range(n):
        f = raw[i * w * h:(i + 1) * w * h]
        m = sum(f) / len(f)
        stds.append((sum((x - m) ** 2 for x in f) / len(f)) ** 0.5)
    if not stds or max(stds) < 1:
        return 0.0
    first = next(i for i, v in enumerate(stds) if v >= 0.4 * max(stds))
    return round(min(first / fps, upto), 2)


def frames_jpeg(path: Path, at: tuple[float, ...] = (0.5, 2.0, 3.5)) -> list[bytes]:
    """비전 확인용 장면 몇 장 (JPEG, 가로 480)"""
    out = []
    for t in at:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2",
                            "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    return out


def video_ok(thing: str, query: str, shots: list[bytes]) -> bool | None:
    """Claude 비전으로 장면 몇 장 확인: 훅의 비유(물건이나 장면)가 주인공이고, 눈에 띄게 움직이거나 재미있는 장면인가.
    키가 없으면 None(확인 생략). 태그만 보면 'electric heating coil'에 일렉 기타가 걸리는 식의 오답이 남아서 넣었다."""
    if not settings.anthropic_api_key or not shots:
        return None
    import base64

    import anthropic
    from gongsi.summarize import MODEL

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b).decode()}}
               for b in shots]
    content.append({"type": "text", "text": (
        f"같은 영상의 앞부분 장면 {len(shots)}장입니다. 인스타 릴스 배경으로 쓰려고 합니다.\n"
        f"'{thing}' 또는 검색어 장면('{query}')이 화면 주인공으로 분명히 보이나요?\n"
        "다음 중 하나라도 있으면 '아니오': 다른 것이 주인공, 읽을 수 있는 브랜드 로고·제품명 글자, 선정적인 장면, 저화질, "
        "장면이 거의 안 변하는 정지 화면 같은 영상.\n'예' 또는 '아니오'만 답하세요.")})
    msg = anthropic.Anthropic(api_key=settings.anthropic_api_key).messages.create(
        model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip().startswith("예")


def _get(url: str, path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(requests.get(url, timeout=120).content)
    return path


def find_video(query: str, skip: set[str] | None = None, thing: str = "") -> tuple[Path, dict] | None:
    """Pixabay 영상 검색 → 아직 안 쓴 영상 중 태그가 훅의 비유와 맞는 것 10개의 작은 판을 받아 움직임을 재고,
    (태그 점수, 움직임) 순으로 장면 3장을 비전으로 확인해 통과한 것을 받는다. 움직임이 MOTION_MIN보다 큰 영상을 먼저,
    없으면 그중 가장 많이 움직이는 것. 검색어를 넓혀 가며(전체 → 핵심 명사들 → 첫 명사) 모으고, 짧은 영상은 반복 재생한다."""
    key = os.getenv("PIXABAY_API_KEY", "")
    words = _keywords(query)
    if not key or not words:
        return None
    skip = skip if skip is not None else used_ids()
    cands: dict[str, tuple] = {}
    for q in dict.fromkeys([query, " ".join(words), words[0]]):
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "per_page": 50, "safesearch": "true"}, timeout=30)
        for hit in r.json().get("hits", []):
            v = hit["videos"].get("medium") or hit["videos"].get("small")
            mid = f"pixabay:{hit['id']}"
            if mid in skip or mid in cands or not v or not v.get("url") or min(v["width"], v["height"]) < 540 or hit.get("duration", 0) < 4:
                continue
            score = _score(hit.get("tags", ""), words)
            if score >= 1:
                cands[mid] = (score, hit, v, q)
    top = sorted(cands.values(), key=lambda c: c[0], reverse=True)[:10]
    measured = []
    for score, hit, v, q in top:
        small = hit["videos"].get("tiny") or hit["videos"].get("small") or v
        try:
            preview = _get(small["url"], CACHE / "preview" / f"pixabay_{hit['id']}.mp4")
            motion = motion_score(preview)
        except Exception:  # noqa: BLE001
            continue
        measured.append((score, motion, hit, v, q, preview))
    lively = [m for m in measured if m[1] >= MOTION_MIN]
    pool = sorted(lively or measured, key=lambda m: (m[0], min(m[1], 20.0)), reverse=True)
    for score, motion, hit, v, q, preview in pool[:5]:
        verdict = video_ok(thing or query, query, frames_jpeg(preview))
        if verdict is False:
            continue
        path = _get(v["url"], CACHE / f"pixabay_{hit['id']}.mp4")
        meta = {"id": f"pixabay:{hit['id']}", "query": q, "user": hit.get("user"), "page": hit.get("pageURL"),
                "tags": hit.get("tags"), "people": _has_people(hit.get("tags", "")), "vision": verdict, "motion": motion,
                "license": "Pixabay Content License"}
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return path, meta
    return None


def split_title(text: str, max_w: float = 11.0) -> list[str]:
    """큰 제목을 두 줄로: 어절 단위, 두 줄 폭이 비슷하게"""
    if line_width(text) <= max_w:
        return [text]
    words = text.split(" ")
    best = min(((max(line_width(" ".join(words[:i])), line_width(" ".join(words[i:]))), i) for i in range(1, len(words))))
    return [" ".join(words[:best[1]]), " ".join(words[best[1]:])]


def overlay_html(title: str, second: str, sub: str, use: str = "") -> str:
    """영상 자리만 투명한 글자 판 HTML. 제목·쓰이는 곳·둘째 줄은 SAFE_TOP 아래(프로필 격자에서도 보이게),
    '자세한 내용' 줄은 SAFE_BOTTOM 위. 작은 ipov 로고는 영상 오른쪽 위"""
    from gongsi.render import font_css

    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    lines = "<br>".join(esc(x) for x in split_title(title))
    use_html = f"<div class=u>📍 {esc(use)}</div>" if use else ""
    return f"""<html><head><meta charset=utf-8><style>{font_css()}
html,body{{margin:0;background:transparent}}
.c{{width:{W}px;height:{H}px;position:relative;font-family:'Pretendard','Noto Color Emoji'}}
.top{{position:absolute;left:0;top:0;width:{W}px;height:{VIDEO_TOP}px;background:#0F1115;display:flex;flex-direction:column;align-items:center}}
.bot{{position:absolute;left:0;top:{VIDEO_TOP + VIDEO_H}px;width:{W}px;height:{H - VIDEO_TOP - VIDEO_H}px;background:#0F1115}}
.t{{margin-top:{SAFE_TOP}px;text-align:center;color:#fff;font-size:76px;font-weight:900;line-height:1.16;letter-spacing:-.035em;word-break:keep-all}}
.u{{margin-top:16px;color:#C9D1D9;font-size:42px;font-weight:700;letter-spacing:-.02em;line-height:1.15}}
.s{{margin-top:12px;color:#F5C84B;font-size:56px;font-weight:800;letter-spacing:-.02em;line-height:1.1}}
.wm{{position:absolute;top:{VIDEO_TOP + 20}px;right:72px;font-size:36px;font-weight:900;letter-spacing:-.045em;color:#F2F4F6;line-height:1;
     text-shadow:0 2px 10px rgba(0,0,0,.6)}}
.wm .b{{color:#5EA9FF}} .wm .dot{{position:relative;display:inline-block}}
.wm .dot i{{position:absolute;left:50%;top:.02em;width:.2em;height:.2em;border-radius:50%;background:#F5C84B;transform:translateX(-50%)}}
.sub{{position:absolute;top:{VIDEO_TOP + VIDEO_H - 112}px;left:0;width:{W}px;text-align:center;color:#fff;font-size:58px;font-weight:900;text-shadow:0 3px 14px #000,0 0 4px #000}}
.tag{{position:absolute;top:{VIDEO_TOP + 24}px;left:72px;font-size:26px;font-weight:600;color:rgba(255,255,255,.85);background:rgba(0,0,0,.45);padding:6px 14px;border-radius:999px}}
.ar{{position:absolute;top:{VIDEO_TOP + VIDEO_H + 34}px;left:0;width:{W}px;text-align:center;color:#5EA9FF;font-size:50px;font-weight:800;line-height:1.1}}
</style></head><body><div class=c>
<div class=top><div class=t>{lines}</div>{use_html}<div class=s>{esc(second)}</div></div>
<div class=wm><span class=b><span class=dot>ı<i></i></span>po</span>v</div>
<div class=tag>자료 화면</div><div class=sub>{esc(sub)}</div>
<div class=bot></div><div class=ar>▼ 자세한 내용은 아래에 ▼</div></div></body></html>"""


def layout_boxes(title: str, second: str, sub: str, use: str = "") -> dict[str, tuple[float, float]]:
    """글자 판 요소의 세로 위치 {이름: (위, 아래)}. 안전 영역 시험용"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.set_content(overlay_html(title, second, sub, use), wait_until="load")
        pg.wait_for_timeout(300)
        out = {}
        for name in ("wm", "t", "u", "s", "sub", "tag", "ar"):
            if name == "u" and not use:
                continue
            bb = pg.locator(f".{name}").bounding_box()
            out[name] = (bb["y"], bb["y"] + bb["height"])
        b.close()
    return out


def overlay_png(title: str, second: str, sub: str, out: Path, use: str = "") -> Path:
    """영상 자리만 투명한 글자 판"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.set_content(overlay_html(title, second, sub, use), wait_until="load")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out), omit_background=True)
        b.close()
    return out


def compose(video: Path, overlay: Path, out: Path, seconds: int = SECONDS, start: float = 0.0) -> Path:
    """배경색 → 가운데 영상(꽉 채워 자름) → 글자 판. 소리는 빈 트랙 (음악은 게시 때 API로)"""
    vf = (f"color=c=0x0F1115:s={W}x{H}:r=30:d={seconds}[bg];"
          f"[0:v]trim=0:{seconds},setpts=PTS-STARTPTS,scale={W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{VIDEO_H},fps=30[v];[bg][v]overlay=0:{VIDEO_TOP}[b1];[b1][1:v]overlay=0:0,format=yuv420p[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(video), "-i", str(overlay),
                    "-f", "lavfi", "-t", str(seconds), "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", vf, "-map", "[out]", "-map", "2:a", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def thing_of(title: str) -> str:
    """훅 제목에서 빗댄 물건: "반도체 '샤워기' 만드는…" → 샤워기"""
    m = re.search(r"'([^']+)'", title)
    return m.group(1) if m else ""


def make_reel(title: str, second: str, sub: str, query: str, out: Path, thing: str | None = None, use: str = "") -> dict:
    """훅 릴스 한 편. 돌려주는 meta(영상 id·출처)는 캡션 '📷 자료 화면' 줄, 로그, 게시 대기열의 사용 기록용"""
    found = find_video(query, skip=used_ids() | _RUN_USED, thing=thing_of(title) if thing is None else thing)
    if not found:
        raise RuntimeError(f"'{query}' 영상을 못 찾음")
    video, meta = found
    meta["start"] = lead_in(video)
    with tempfile.TemporaryDirectory() as tmp:
        compose(video, overlay_png(title, second, sub, Path(tmp) / "overlay.png", use), out, start=meta["start"])
    _RUN_USED.add(meta["id"])
    return meta


if __name__ == "__main__":
    t, s2, sub, q, o = sys.argv[1:6]
    print(json.dumps(make_reel(t, s2, sub, q, Path(o)), ensure_ascii=False))
