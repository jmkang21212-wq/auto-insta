"""모션 리포트 릴스 (2026-11-03 결정: 7초 훅 한 장 → 20초 안팎 움직이는 리포트 + TTS 내레이션 + 자막).

장면
  1. 훅      : 훅 비유 스톡 영상(Pixabay, 이미 비전 확인됨) 위에 훅 제목 + 단계 도장(내일 상장·청약 D-n)
  2. 사업    : 카드 1장 사진 배경 + 로고 + 무엇을 하는 회사인지(요약 business_lines, 신고서 근거)
  3. 몸값    : 공모가(또는 희망 공모가) → 주관사 몸값 숫자가 차오름
  4. 수요    : 기관 경쟁률(점 285개) → 의무보유 확약 도넛. 수요예측 전이면 상장일 유통 물량 도넛 + 청약 일정
  5. 마무리  : ipov + "자세한 숫자는 캡션에"
숫자는 모두 facts(신고서·발행실적보고서 파싱값)에서만 가져온다. 화면 글자는 숫자, 내레이션은 같은 숫자를 한글로 읽는다.

TTS: HeyGen 음성 API (HEYGEN_API_KEY). 키가 없으면 MotionUnavailable → 파이프라인은 기존 7초 훅 릴스를 쓴다.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

from gongsi.config import settings

W, H, FPS = 1080, 1920, 30
VOICE_ID = os.getenv("TTS_VOICE_ID", "fe3fbd12d84c41b5a791e6ea544f774d")  # HeyGen 한국어 여성 (2026-11-02 샘플에서 사용자 승인)
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.05"))  # 11-03 사용자: 전환이 빨라 숫자를 못 봄
TEMPLATE = settings.templates_dir / "motion.html"
FONTS = settings.templates_dir.parent / "fonts"


PAUSE = os.getenv("TTS_PAUSE", "1.3")  # 11-03 사용자: 장면 전환이 급함 → 문장 사이 1.3초  # 문장 사이 쉼: 숫자가 다 찬 뒤 멈춰 있는 시간


class MotionUnavailable(RuntimeError):
    pass


# ── 숫자를 한글로 읽기 ─────────────────────────────────────────────
_D = "영일이삼사오육칠팔구"


def _under_10000(n: int) -> str:
    out = ""
    for unit, val in (("천", 1000), ("백", 100), ("십", 10)):
        q, n = divmod(n, val)
        if q:
            out += ("" if q == 1 else _D[q]) + unit
    if n:
        out += _D[n]
    return out


def num_ko(n: int) -> str:
    """29551 → 이만 구천오백오십일, 19000 → 만 구천, 1200000000 → 십이억"""
    if n == 0:
        return "영"
    parts = []
    for unit, val in (("조", 10 ** 12), ("억", 10 ** 8), ("만", 10 ** 4)):
        q, n = divmod(n, val)
        if q:
            parts.append(("" if (q == 1 and unit == "만") else _under_10000(q)) + unit)
    if n:
        parts.append(_under_10000(n))
    return " ".join(parts)


MONTH = {6: "유월", 10: "시월"}


def date_ko(iso: str) -> str:
    y, m, d = map(int, iso[:10].split("-"))
    return f"{MONTH.get(m, num_ko(m) + '월')} {num_ko(d)}일"


def josa(word: str, a: str, b: str) -> str:
    ch = word[-1]
    return a if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else b


# ── 대본 ──────────────────────────────────────────────────────────
@dataclass
class Seg:
    scene: str
    tokens: list[tuple[str, str]] = field(default_factory=list)  # (읽는 말, 화면 자막)

    def add(self, spoken: str, shown: str | None = None) -> "Seg":
        if shown is None:
            for w in spoken.split():
                self.tokens.append((w, w))
        else:
            self.tokens.append((spoken, shown))
        return self

    def spoken(self) -> str:
        return " ".join(s for s, _ in self.tokens)


def spoken_title(title: str) -> str:
    t = re.sub(r"['\"‘’“”]", "", title).strip()
    for a, b in (("상장함", "상장해요."), ("풀림", "풀려요."), ("나옴", "나왔어요."), ("마감함", "마감해요.")):
        if t.endswith(a):
            return t[: -len(a)] + b
    return t if t.endswith(".") else t + "."


def spoken_explain(explain: str) -> str:
    """캡션 풀이 줄(': 정전척은 … 고정하는 장치임')을 말투로 ('… 장치예요.'). 숫자가 있으면 읽기가 어긋나니 뺀다"""
    t = explain.strip().lstrip(":").strip()
    if not t or re.search(r"\d", t):
        return ""
    t = re.sub(r"\s+", " ", t).rstrip(".")
    for a, b in (("하는 장치임", "하는 장치예요"), ("장치임", "장치예요"), ("말임", "말이에요"), ("부품임", "부품이에요"),
                 ("함", "해요"), ("됨", "돼요")):
        if t.endswith(a):
            t = t[: -len(a)] + b
            break
    else:
        if t.endswith("임") and len(t) > 1:  # 센서임 → 센서예요, 장비임 → 장비예요, 부품임 → 부품이에요
            prev = t[-2]
            closed = "가" <= prev <= "힣" and (ord(prev) - 0xAC00) % 28
            t = t[:-1] + ("이에요" if closed else "예요")
    return t + "."


def _pct_spoken(p: float) -> tuple[str, str]:
    """(읽는 말, 자막) 1% 미만은 '1%도 안 돼요'로 읽고 자막엔 실제 숫자"""
    if p < 1:
        return "일 퍼센트도 안 돼요.", f"{p:g}%, 1%도 안 돼요."
    return f"약 {num_ko(round(p))} 퍼센트예요.", f"{p:g}%예요."


def _term_short(term: str) -> str:
    """'Showerhead(샤워헤드)' → 샤워헤드, 'GPU 분할 가상화(Fractional GPU)' → GPU 분할 가상화"""
    m = re.match(r"\s*([^()]+?)\s*\(([^()]+)\)\s*$", term or "")
    if m:
        a, b = m.groups()
        return b if re.search(r"[가-힣]", b) and not re.search(r"[가-힣]", a) else a
    return (term or "").strip()


def stamp_text(facts, phase: str, today: date) -> str:
    if phase == "listing" and facts.listing_date:
        d = date.fromisoformat(facts.listing_date)
        return f"{d.month}월 {d.day}일 상장" + (" · 내일" if (d - today).days == 1 else "")
    if facts.subscription_start and facts.subscription_end:
        s, e = date.fromisoformat(facts.subscription_start), date.fromisoformat(facts.subscription_end)
        if s <= today <= e:
            return "오늘 청약 마감" if today == e else "오늘 청약"
        if today < s:
            return f"청약 D-{(s - today).days}"
    if facts.listing_date:
        d = date.fromisoformat(facts.listing_date)
        return f"{d.month}월 {d.day}일 상장"
    return "공모주"


def script(facts, summary, hook, phase: str, today: date) -> list[Seg]:
    segs = [Seg("hook").add(spoken_title(hook.title))]
    corp = facts.corp_name
    biz = " ".join(l.strip() for l in (summary.business_lines or []) if l.strip())
    if biz:
        segs.append(Seg("about").add(f"{corp}{josa(corp, '은', '는')}").add(biz if biz.endswith(".") else biz + "."))
    d = spoken_explain(hook.explain or "")
    if d:  # 비유한 제품이 실제로 뭔지 한 문장 (신고서 용어 해설 그대로, 11-03 사용자: "정전척이 뭔지 설명해줘야지")
        segs.append(Seg("define").add(d))
    v = facts.valuation_krw
    if v:
        s = Seg("value")
        if facts.final_price_krw:
            s.add("공모가는").add(f"{num_ko(facts.final_price_krw)} 원.", f"{facts.final_price_krw:,}원.")
        elif facts.band_low_krw and facts.band_high_krw:
            s.add("희망 공모가는").add(f"{num_ko(facts.band_low_krw)} 원에서", f"{facts.band_low_krw:,}원에서")
            s.add(f"{num_ko(facts.band_high_krw)} 원.", f"{facts.band_high_krw:,}원.")
        segs.append(s)  # 공모가에서 한 번 쉬고 몸값 (같은 장면)
        segs.append(Seg("value").add("주관사가 계산한 몸값은").add(f"{num_ko(v)} 원이에요.", f"{v:,}원이에요."))
    dr = facts.demand_ratio
    if dr:
        s = Seg("demand").add("기관 경쟁률은").add(f"{num_ko(int(dr))} 대 일.", f"{dr:,.2f}".rstrip("0").rstrip(".") + ":1.")
        segs.append(s)
        if facts.lockup_ratio_pct is not None:
            sp, sh = _pct_spoken(facts.lockup_ratio_pct)
            segs.append(Seg("lockup").add("그런데 상장 뒤에 안 팔겠다고 약속한 물량은,").add(sp, sh))
    else:  # 수요예측 전: 첫날 유통 물량(있으면) + 청약 일정
        if facts.float_ratio_pct:
            sp, sh = _pct_spoken(facts.float_ratio_pct)
            segs.append(Seg("float").add("상장 첫날 팔 수 있는 물량은").add(sp, sh))
        if facts.subscription_start and facts.subscription_end:
            segs.append(Seg("sched").add("청약은").add(f"{date_ko(facts.subscription_start)}부터",
                                                       f"{int(facts.subscription_start[5:7])}월 {int(facts.subscription_start[8:10])}일부터")
                        .add(f"{date_ko(facts.subscription_end)}까지예요.",
                             f"{int(facts.subscription_end[5:7])}월 {int(facts.subscription_end[8:10])}일까지예요."))
    # 마무리 (11-03 사용자: "결론이 뭐야" → 숫자 정리 + 시청자에게 묻는 질문. 판단을 대신하지 않고 묻기만 한다)
    rc, rows = recap(facts)
    if rc.tokens and os.getenv("MOTION_RECAP", "1") != "0":
        segs.append(rc)
    segs.append(Seg("ask").add(ask_line(phase)).add("댓글로 알려 주세요."))
    return segs


def recap(facts) -> tuple[Seg, list[dict]]:
    """정리 문장과 화면 줄. 경쟁률·확약·첫날 유통 물량 (있는 것만), 없으면 몸값·유통"""
    s, rows = Seg("recap").add("정리하면,"), []
    items = []
    if facts.demand_ratio:
        r = facts.demand_ratio
        shown = f"{r:,.2f}".rstrip("0").rstrip(".") + ":1"
        items.append(("기관 경쟁률", f"{num_ko(int(r))} 대 일", shown, "🔥"))
    if facts.lockup_ratio_pct is not None:
        p = facts.lockup_ratio_pct
        items.append(("의무보유 확약", "일 퍼센트 미만" if p < 1 else f"약 {num_ko(round(p))} 퍼센트", f"{p:g}%", "🔒"))
    if not items and facts.valuation_krw:
        items.append(("주관사 몸값", f"{num_ko(facts.valuation_krw)} 원", f"{facts.valuation_krw:,}원", "🧮"))
    if facts.float_ratio_pct is not None:
        p = facts.float_ratio_pct
        items.append(("상장 첫날 팔 수 있는 물량", f"약 {num_ko(round(p))} 퍼센트", f"{p:g}%", "📦"))
    for i, (label, sp, sh, icon) in enumerate(items):
        last = i == len(items) - 1
        s.add(label).add(sp + ("예요." if last else ","), sh + ("예요." if last else ","))
        rows.append({"label": label, "value": sh, "icon": icon})
    return (s if items else Seg("recap")), rows


def ask_line(phase: str) -> str:
    return {"listing": "상장 첫날, 여러분이라면 살 건가요, 팔 건가요?",
            "post": "여러분은 이 공모주, 청약할 건가요?"}.get(phase, "여러분은 청약할 생각 있나요?")


def tts_text(segs: list[Seg]) -> str:
    # '힘/토크센서'의 빗금을 '빗금'으로 읽지 않게 (낱말 수는 그대로)
    return f' <break time="{PAUSE}s"/> '.join(s.spoken().replace("/", "·") for s in segs)


# ── TTS ──────────────────────────────────────────────────────────
def tts(text: str, out: Path) -> list[dict]:
    """HeyGen 음성 → out(mp3)과 단어 타임스탬프"""
    key = os.getenv("HEYGEN_API_KEY")
    if not key:
        raise MotionUnavailable("HEYGEN_API_KEY 없음")
    r = requests.post("https://api.heygen.com/v3/voices/speech", headers={"X-Api-Key": key, "Content-Type": "application/json"},
                      json={"text": text, "voice_id": VOICE_ID, "speed": TTS_SPEED, "language": "ko"}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"heygen speech {r.status_code}: {r.text[:200]}")
    d = r.json().get("data") or r.json()
    out.write_bytes(requests.get(d["audio_url"], timeout=120).content)
    return d.get("word_timestamps") or []


def align(segs: list[Seg], words: list[dict], total: float) -> tuple[list[dict], list[dict]]:
    """TTS 단어 시각을 대본 토큰에 맞춘다 → (자막 [{d,s,e}], 장면 [{scene,t0,t1}])"""
    ws = [w for w in words if not str(w.get("word", "")).startswith("<")]
    need = sum(len(sp.split()) for s in segs for sp, _ in s.tokens)
    caps, starts = [], []
    if len(ws) == need:
        i = 0
        for s in segs:
            first = None
            for sp, sh in s.tokens:
                k = len(sp.split())
                a, b = ws[i]["start"], ws[i + k - 1]["end"]
                i += k
                caps.append({"d": sh, "s": a, "e": b})
                first = a if first is None else first
            starts.append(first)
    else:  # 개수가 안 맞으면 글자 수 비례로 (드묾)
        n = sum(len(sp) for s in segs for sp, _ in s.tokens)
        t = 0.2
        for s in segs:
            starts.append(t)
            for sp, sh in s.tokens:
                d = (total - 0.4) * len(sp) / n
                caps.append({"d": sh, "s": t, "e": t + d})
                t += d
    scenes = []
    for j, s in enumerate(segs):
        if scenes and scenes[-1]["scene"] == s.scene:  # 같은 장면이 이어지면 한 장면으로
            continue
        t0 = 0.0 if j == 0 else max(0.0, starts[j] - 0.12)
        scenes.append({"scene": s.scene, "t0": t0})
    for j in range(len(scenes)):
        scenes[j]["t1"] = scenes[j + 1]["t0"] if j + 1 < len(scenes) else total
    return caps, scenes


# ── 그림 재료 ─────────────────────────────────────────────────────
def _uri(p: Path | None) -> str:
    if not p:
        return ""
    mime = {".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(p.suffix.lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _photo(src: Path | None, out: Path, blur: float, dim: float) -> str:
    """사진을 1080x1920으로 채워 자르고 흐림·어둡게 → data URI. 사진이 없거나 SVG(그라데이션 대체)면 빈 값"""
    if not src or src.suffix.lower() == ".svg" or not src.exists():
        return ""
    from PIL import Image, ImageEnhance, ImageFilter

    im = Image.open(src).convert("RGB")
    s = max(W * 1.12 / im.width, H * 1.12 / im.height)  # 켄 번스 확대 여유
    im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)))
    l, t = (im.width - int(W * 1.12)) // 2, (im.height - int(H * 1.12)) // 2
    im = im.crop((l, t, l + int(W * 1.12), t + int(H * 1.12)))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    im = ImageEnhance.Brightness(im).enhance(dim)
    im.save(out, "JPEG", quality=86)
    return _uri(out)


def _clip_frames(video: Path, start: float, seconds: float, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(video),
                    "-t", f"{seconds:.2f}", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                    "-q:v", "4", str(out_dir / "f%04d.jpg")], check=True)
    return len(list(out_dir.glob("f*.jpg")))


def _audio_bed(voice: Path, total: float, out: Path) -> Path:
    """목소리 + 직접 합성한 잔잔한 배경음 (저작권 걱정 없음)"""
    d = f"{total:.2f}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=f=110:d={d}", "-f", "lavfi", "-i", f"sine=f=164.81:d={d}",
                    "-f", "lavfi", "-i", f"sine=f=220:d={d}", "-f", "lavfi", "-i", f"sine=f=277.18:d={d}",
                    "-f", "lavfi", "-i", f"anoisesrc=d={d}:c=pink:a=0.02", "-i", str(voice),
                    "-filter_complex",
                    "[0][1][2][3]amix=inputs=4:normalize=1,tremolo=f=0.25:d=0.35,lowpass=f=900[pad];"
                    "[4]highpass=f=300,lowpass=f=3000[air];"
                    f"[pad][air]amix=inputs=2:weights='1 0.25',volume=0.12,afade=t=in:d=1.2,afade=t=out:st={max(0, total - 1.6):.2f}:d=1.5[bed];"
                    "[5]loudnorm=I=-15:TP=-1.5:LRA=11,apad[v];"
                    "[v][bed]amix=inputs=2:normalize=0:duration=longest[a]",
                    "-map", "[a]", "-t", d, "-ar", "44100", str(out)], check=True)
    return out


# ── 렌더 ──────────────────────────────────────────────────────────
def render(facts, summary, hook, phase: str, today: date, out: Path, *, clip: Path | None, clip_start: float = 0.0,
           hero: Path | None = None, back: Path | None = None, logo: Path | None = None, logo_tone: str = "dark",
           source_line: str = "", voice: Path | None = None, words: list[dict] | None = None) -> dict:
    """모션 리포트 한 편 → out(mp4). voice·words를 주면 TTS를 부르지 않는다 (로컬 시험·다시 만들기)"""
    from playwright.sync_api import sync_playwright

    segs = script(facts, summary, hook, phase, today)
    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        if voice is None:
            voice = tmp / "voice.mp3"
            words = tts(tts_text(segs), voice)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(voice)],
                                   capture_output=True, text=True, check=True).stdout.strip())
        total = round(dur + 0.9, 2)
        caps, scenes = align(segs, words or [], total)
        hook_t1 = scenes[0]["t1"] + 0.5
        # 11-03 사용자: 훅 영상보다 정지 사진 위 모션 그래픽이 낫다 → 스톡 영상은 쓰지 않음 (MOTION_CLIP=1이면 예전처럼)
        n_frames = _clip_frames(clip, clip_start, hook_t1, tmp / "clip") if clip and clip.exists() and os.getenv("MOTION_CLIP") == "1" else 0
        data = {
            "W": W, "H": H, "total": total, "scenes": scenes, "caps": caps,
            "hook": {"emoji": hook.emoji, "title": re.sub(r"'([^']+)'", r"<b>'\1'</b>", hook.title),
                     "stamp": stamp_text(facts, phase, today), "sub": hook.sub, "term": _term_short(hook.term),
                     "parts": list(getattr(hook, "parts", []) or [])},
            "about": {"lines": [l for l in (summary.business_lines or []) if l.strip()], "corp": facts.corp_name,
                      "meta": " · ".join(x for x in (facts.industry, facts.market) if x),
                      "explain": (hook.explain or "").lstrip(": ").strip()},
            "value": {"price": facts.final_price_krw, "low": facts.band_low_krw, "high": facts.band_high_krw,
                      "val": facts.valuation_krw, "model": facts.valuation_model, "mult": facts.valuation_multiple},
            "demand": {"ratio": facts.demand_ratio, "lock": facts.lockup_ratio_pct},
            "float": {"pct": facts.float_ratio_pct},
            "recap": recap(facts)[1] if os.getenv("MOTION_RECAP", "1") != "0" else [],
            "sched": {"s": facts.subscription_start, "e": facts.subscription_end},
            "clip": {"dir": (tmp / "clip").as_uri(), "n": n_frames},
            "img": {"hero": _photo(hero, tmp / "hero.jpg", 0, 0.62), "back": _photo(back or hero, tmp / "back.jpg", 18, 0.42)},
            "logo": _uri(logo), "logoTone": logo_tone, "source": source_line,
        }
        fonts = "".join(f"@font-face{{font-family:P;font-weight:{w};src:url('{(FONTS / f'Pretendard-{n}.otf').as_uri()}')}}"
                        for n, w in (("Medium", 500), ("SemiBold", 600), ("Bold", 700), ("ExtraBold", 800), ("Black", 900)))
        page_html = TEMPLATE.read_text(encoding="utf-8").replace("/*FONTS*/", fonts).replace("__DATA__", json.dumps(data, ensure_ascii=False))
        (tmp / "page.html").write_text(page_html, encoding="utf-8")
        silent = tmp / "video.mp4"
        n = int(round(total * FPS))
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--allow-file-access-from-files"])
            page = b.new_page(viewport={"width": W, "height": H})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto((tmp / "page.html").as_uri())
            try:
                page.wait_for_function("window.ready === true", timeout=30000)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"모션 페이지 준비 실패: {errs[:3]}") from e
            ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), str(silent)],
                                  stdin=subprocess.PIPE)
            for i in range(n):
                page.evaluate("t => setT(t)", i / FPS)
                ff.stdin.write(page.screenshot(type="jpeg", quality=90))
            ff.stdin.close()
            if ff.wait() != 0:
                raise RuntimeError("ffmpeg 프레임 인코딩 실패")
            b.close()
        audio = _audio_bed(voice, total, tmp / "audio.wav")
        part = out.with_name(out.stem + ".part.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(part)], check=True)
        shutil.move(str(part), out)
    return {"motion": True, "seconds": total, "scenes": [s["scene"] for s in scenes], "voice": VOICE_ID,
            "script": [s.spoken() for s in segs]}


def make_for(facts, summary, hook, phase: str, today: date, out: Path, video_meta: dict | None, source_line: str,
             voice: Path | None = None, words: list[dict] | None = None) -> dict:
    """파이프라인용: 훅 스톡 영상(이미 받아 둔 것)·카드 사진(캐시)·로고를 찾아 render. 사진은 카드와 같은 것을 쓴다"""
    if voice is None and not os.getenv("HEYGEN_API_KEY"):
        raise MotionUnavailable("HEYGEN_API_KEY 없음")
    from gongsi.images import illustration
    from gongsi.logos import logo_info
    from gongsi.reel import CACHE

    vid = (video_meta or {}).get("id", "").split(":")[-1]
    clip = CACHE / f"pixabay_{vid}.mp4" if vid else None
    ind = facts.industry or ""
    hero, _ = illustration(1, ind)
    back, _ = illustration(0, ind)
    logo, tone = logo_info(facts.corp_name)
    return render(facts, summary, hook, phase, today, out, clip=clip, clip_start=(video_meta or {}).get("start", 0.0),
                  hero=hero, back=back, logo=logo, logo_tone=tone, source_line=source_line, voice=voice, words=words)


AI_VOICE_LINE = "🎙️ 내레이션: AI 음성"


def credit(caption: str) -> str:
    """캡션 출처 줄 아래에 AI 음성 표기 (한 번만)"""
    if AI_VOICE_LINE in caption:
        return caption
    if "📷 자료 화면: Pixabay" in caption:
        return caption.replace("📷 자료 화면: Pixabay", "📷 자료 화면: Pixabay\n" + AI_VOICE_LINE, 1)
    lines = caption.rstrip().split("\n")
    for i, l in enumerate(lines):  # '※ 투자 권유 아님' 줄 바로 위에 (해시태그 뒤로 밀리지 않게)
        if l.startswith("※"):
            return "\n".join(lines[:i] + ["📷 사진·영상: Pixabay", AI_VOICE_LINE] + lines[i:])
    return caption.rstrip() + "\n" + AI_VOICE_LINE
