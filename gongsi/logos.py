"""회사 로고 자동 수집: 신고서 표지의 홈페이지 주소 → 그 사이트의 자기 로고 → PNG.

순서
1. 원문 표지에서 "(홈페이지) https://..." 를 읽는다 (본문이 아니라 표지라 body_text 전이 필요).
2. 홈페이지 HTML에서 후보를 모은다: <img>(src·alt), <link rel=icon|apple-touch-icon>, og:image.
3. 점수: alt/src에 회사명(한글·도메인 이름)이 있으면 +, 'logo'가 있으면 +, customer/partner/client/sponsor 경로면 -.
   Claude 키가 있으면 상위 후보 목록을 주고 "이 회사 자기 로고"를 고르게 한다 (고객사 로고 띠가 흔해서).
4. 받아서 PNG로 저장. SVG는 Playwright로 렌더. 결과는 assets/logos/<회사명>.png + .json(출처 URL, 고른 근거).

로고는 상표라서 '공시 사실을 식별하는 용도'로만 쓰고 출처를 json에 남깁니다. 실패하면 조용히 None → 템플릿은 회사명 글자로 간다.
"""
from __future__ import annotations

import base64
import html as html_mod
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from gongsi.config import settings

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LOGO_DIR = settings.templates_dir.parent.parent / "assets" / "logos"


def homepage_from_text(text: str) -> str | None:
    """표지 '(홈페이지) https://www.lablup.com/ko' → URL."""
    # 표지 서식만: "(홈페이지) URL" 또는 "홈페이지 : URL". 본문의 "주관사 홈페이지(https://...)" 는 괄호가 뒤에 와서 안 걸린다.
    # 투자설명서는 앞에 정정표가 길게 붙어 표지가 수십만 자 뒤에 올 수 있으니 전체를 본다
    m = re.search(r"\(\s*홈\s*페\s*이\s*지\s*\)\s*:?\s*(https?://[^\s|<>\"]+|www\.[^\s|<>\"]+)", text) \
        or re.search(r"(?:^|\|)\s*홈\s*페\s*이\s*지\s*:\s*(https?://[^\s|<>\"]+|www\.[^\s|<>\"]+)", text, re.M)
    if not m:
        return None
    url = m.group(1).rstrip(".,)")
    return url if url.startswith("http") else "http://" + url


def _candidates(html: str, base: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        src = re.search(r"\bsrc\s*=\s*[\"']([^\"']+)", tag, re.I)
        if not src:
            continue
        alt = re.search(r"\balt\s*=\s*[\"']([^\"']*)", tag, re.I)
        cls = re.search(r"\bclass\s*=\s*[\"']([^\"']*)", tag, re.I)
        out.append({"url": urljoin(base, html_mod.unescape(src.group(1))), "alt": (alt.group(1) if alt else ""), "cls": (cls.group(1) if cls else ""), "kind": "img"})
    for m in re.finditer(r"<link\b[^>]*rel\s*=\s*[\"'][^\"']*(apple-touch-icon|icon)[^\"']*[\"'][^>]*>", html, re.I):
        tag = m.group(0)
        href = re.search(r"\bhref\s*=\s*[\"']([^\"']+)", tag, re.I)
        if href:
            out.append({"url": urljoin(base, html_mod.unescape(href.group(1))), "alt": m.group(1), "cls": "", "kind": "icon"})
    m = re.search(r"<meta\b[^>]*property\s*=\s*[\"']og:image[\"'][^>]*content\s*=\s*[\"']([^\"']+)", html, re.I)
    if m:
        out.append({"url": urljoin(base, html_mod.unescape(m.group(1))), "alt": "og:image", "cls": "", "kind": "og"})
    seen, uniq = set(), []
    for c in out:
        if c["url"] not in seen:
            seen.add(c["url"])
            c["pos"] = len(uniq)  # 문서 순서: 헤더 로고는 보통 맨 앞 <img>
            uniq.append(c)
    return uniq


def _score(c: dict, corp: str, domain: str) -> int:
    s = 0
    hay = (c["url"] + " " + c["alt"] + " " + c["cls"]).lower()
    root = domain.split(".")[0]
    if corp.lower() in hay or (len(root) >= 4 and root in hay):
        s += 4
    if "logo" in hay:
        s += 3
    if re.search(r"customer|partner|client|sponsor|award|cert|badge|banner|thumb|icon-", hay):
        s -= 5
    if c["kind"] == "og":
        s += 1
    if c["kind"] == "icon":
        s += 0 if "apple" in hay else -1
    if hay.endswith(".svg"):
        s += 1
    if c["kind"] == "img" and c.get("pos", 99) <= 1:  # 헤더 자리 (텍스트 단서가 없는 Framer류 사이트용)
        s += 2 - c["pos"]
    return s


def _pick_with_claude(corp: str, domain: str, cands: list[dict]) -> int | None:
    if not settings.anthropic_api_key or not cands:
        return None
    import anthropic
    from gongsi.summarize import MODEL

    listing = "\n".join(f"{i}: alt=\"{c['alt'][:40]}\" url={c['url'][:120]}" for i, c in enumerate(cands))
    prompt = (f"회사 '{corp}'(홈페이지 {domain})의 홈페이지에서 모은 이미지 목록입니다. 이 중 '이 회사 자기 로고'(고객사·파트너 로고, 아이콘, 사진 제외)로 가장 적합한 번호 하나만 답하세요. "
              f"없으면 none.\n{listing}\n답은 번호 또는 none만.")
    msg = anthropic.Anthropic(api_key=settings.anthropic_api_key).messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])
    ans = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return int(ans) if ans.isdigit() and int(ans) < len(cands) else None


def _browser_html(url: str) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        html, final = page.content(), page.url
        b.close()
    return html, final


def _browser_download(page_url: str, img_url: str) -> bytes:
    """쿠키 챌린지 뒤의 이미지는 같은 브라우저 세션으로 받아야 한다."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        page.goto(page_url, wait_until="networkidle", timeout=30000)
        # page.request.get 은 한글 경로에서 실패해서(2026-10-27) 페이지 안 fetch로 base64를 받는다.
        # 쿠키 챌린지가 아직 안 풀렸으면 HTML이 오므로, 잠깐 기다렸다 최대 3번
        data = b""
        for _ in range(3):
            page.wait_for_timeout(1500)
            b64 = page.evaluate(
                "async (u) => { const r = await fetch(u); const bl = await r.blob(); "
                "return await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(bl); }); }",
                img_url)
            data = base64.b64decode(b64.split(",", 1)[1])
            if not data.lstrip().lower().startswith(b"<"):
                break
            page.reload(wait_until="networkidle")
        b.close()
    return data


def _looks_like_logo(corp: str, png: Path) -> bool | None:
    """Claude 비전으로 '회사 로고(심볼·워드마크)인가, 아니면 사진·배너·일러스트인가'. 키 없으면 None(판정 생략)."""
    if not settings.anthropic_api_key:
        return None
    import anthropic
    from io import BytesIO

    from PIL import Image
    from gongsi.summarize import MODEL

    im = Image.open(png).convert("RGBA")
    im.thumbnail((600, 600))
    bg = Image.new("RGBA", im.size, (235, 235, 235, 255))  # 흰 로고도 보이게 회색 바탕
    bg.alpha_composite(im)
    buf = BytesIO()
    bg.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    msg = anthropic.Anthropic(api_key=settings.anthropic_api_key).messages.create(
        model=MODEL, max_tokens=1500,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": f"이 이미지는 회사 '{corp}'의 로고(심볼 마크 또는 워드마크, 단독으로 쓸 수 있는 것)인가요? "
                                     "제품 사진, 홈페이지 배너·히어로 일러스트, 여러 아이콘이 모인 그림, 고객사 로고 모음이면 아니오입니다. '예' 또는 '아니오'만 답하세요."}]}])
    ans = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return ans.startswith("예")


def _svg_to_png(svg: bytes, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1200, "height": 400}, device_scale_factor=1)
        page.set_content(f"<html><body style='margin:0;background:transparent'><img id=l src='{uri}' style='height:300px;width:auto;display:block'></body></html>")
        page.wait_for_timeout(300)
        page.locator("#l").screenshot(path=str(out), omit_background=True)
        b.close()


def fetch_logo(corp: str, home: str | None) -> tuple[Path | None, str]:
    """(저장된 PNG 경로 또는 None, 로그 한 줄). 이미 assets/logos/<corp>.png 가 있으면 그걸 쓴다 (직접 넣어 둔 파일 우선)."""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGO_DIR / f"{corp}.png"
    if out.exists():
        return out, "cached"
    if not home:
        return None, "홈페이지 주소 없음"
    # https 먼저, 안 되면 적힌 주소 그대로 (신고서엔 http://가 많고, 일부 환경은 https만 나간다)
    tries = list(dict.fromkeys([re.sub(r"^http://", "https://", home), home]))
    r = None
    err = ""
    for u in tries:
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            r, err = None, f"{u}: {type(e).__name__}"
    if r is None:
        return None, f"홈페이지 접속 실패 {err}"
    html, final_url = r.text, r.url
    cands = _candidates(html, final_url)
    via_browser = False
    if not cands or len(html) < 5000:
        # 국내 호스팅에 흔한 JS 쿠키 챌린지(cupid.js 등)나 JS 렌더 사이트: 실제 브라우저로 한 번 더
        try:
            html, final_url = _browser_html(final_url)
            cands = _candidates(html, final_url)
            via_browser = True
        except Exception as e:
            return None, f"{home}: 브라우저 로드 실패 {type(e).__name__}"
    domain = urlparse(final_url).netloc.replace("www.", "")
    if not cands:
        return None, f"{home}: 이미지 없음"
    r_url = final_url
    cands.sort(key=lambda c: -_score(c, corp, domain))
    top = cands[:12]
    idx = _pick_with_claude(corp, domain, top)
    order = ([top[idx]] if idx is not None else []) + [c for i, c in enumerate(top) if i != idx and _score(c, corp, domain) >= 0]
    if not order:
        return None, f"{home}: 로고로 볼 만한 후보 없음 (1위 {top[0]['url'][:80]})"
    chosen, reasons = None, []
    for c in order[:5]:  # 후보를 받아서 Claude(비전)에게 '이 회사 로고 맞나' 확인. 홈페이지 히어로 그림을 고르는 실수를 막는다
        try:
            if via_browser:
                data = _browser_download(r_url, c["url"])
            else:
                img = requests.get(c["url"], headers={"User-Agent": UA, "Referer": r_url}, timeout=20)
                img.raise_for_status()
                data = img.content
            is_svg = c["url"].lower().split("?")[0].endswith(".svg") or b"<svg" in data[:2000].lower()
            if is_svg:
                _svg_to_png(data, out)
            else:
                from io import BytesIO

                from PIL import Image

                im = Image.open(BytesIO(data)).convert("RGBA")
                if im.width < 48 or im.height < 24:
                    reasons.append(f"{c['url'][:60]}: 너무 작음 {im.size}")
                    continue
                im.save(out)
        except Exception as e:
            reasons.append(f"{c['url'][:60]}: {type(e).__name__}")
            continue
        ok = _looks_like_logo(corp, out)
        if ok is False:
            reasons.append(f"{c['url'][:60]}: 비전 판정 로고 아님")
            out.unlink(missing_ok=True)
            continue
        chosen = c
        break
    if not chosen:
        return None, f"{home}: 후보 전부 탈락 ({'; '.join(reasons)[:200]})"
    tone = logo_tone(out)
    (LOGO_DIR / f"{corp}.json").write_text(json.dumps(
        {"corp": corp, "homepage": home, "source": chosen["url"], "alt": chosen["alt"], "picked_by": "claude" if idx is not None else "score",
         "tone": tone, "candidates": [c["url"] for c in top]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out, f"{chosen['url']} ({'claude' if idx is not None else 'score'}, {tone})"


def logo_tone(path: Path) -> str:
    """불투명 픽셀의 평균 밝기로 'light'(흰 글자 로고 → 어두운 배경에 그대로) / 'dark'(→ 흰 칩 위에) 판정."""
    from PIL import Image

    im = Image.open(path).convert("RGBA")
    px = [p for p in im.getdata() if p[3] > 40]
    if not px:
        return "dark"
    lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b, _ in px) / len(px)
    return "light" if lum > 170 else "dark"


def logo_info(corp: str) -> tuple[Path | None, str]:
    """렌더용: (PNG 경로, tone). json이 없으면(직접 넣은 파일) 그 자리에서 밝기를 잰다."""
    png = LOGO_DIR / f"{corp}.png"
    if not png.exists():
        return None, "dark"
    meta = LOGO_DIR / f"{corp}.json"
    if meta.exists():
        try:
            return png, json.loads(meta.read_text(encoding="utf-8")).get("tone", "dark")
        except ValueError:
            pass
    return png, logo_tone(png)
