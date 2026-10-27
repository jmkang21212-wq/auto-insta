"""HTML 템플릿 → PNG. 이미지형 카드(1·2·7장)는 images.illustration() 결과를 넣습니다."""
from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

from gongsi.cards import Card
from gongsi.config import settings
from gongsi.images import illustration

import re
from markupsafe import Markup, escape

# 강조 대상: 단위가 붙은 숫자(억·만·%·원·주·곳·:1), 콤마·소수점 숫자, 날짜(11.03~04). 맨숫자(2공장)는 제외
NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?(?:~\d+)?(?:\s?(?:억|만|%|원|주|곳|:1))|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+(?:~\d+)?)")


def highlight(text: str) -> Markup:
    """숫자(+단위)를 <em>으로 감싼다. 본문은 흰색 굵게, 제목은 CSS에서 노란색."""
    parts = NUM.split(str(text))
    out = []
    for i, part in enumerate(parts):
        out.append(f"<em>{escape(part)}</em>" if i % 2 else str(escape(part)))
    return Markup("".join(out))


def font_css() -> str:
    d = settings.templates_dir.parent / "fonts"
    faces = []
    for name, weight in (("Regular", 400), ("Medium", 500), ("SemiBold", 600), ("Bold", 700), ("ExtraBold", 800), ("Black", 900)):
        f = d / f"Pretendard-{name}.otf"
        if f.exists():
            b64 = base64.b64encode(f.read_bytes()).decode()
            faces.append(f"@font-face{{font-family:'Pretendard';font-weight:{weight};src:url(data:font/otf;base64,{b64}) format('opentype');}}")
    return "".join(faces)


def render(cards: list[Card], corp: str, industry: str, out_dir: Path, prefix: str) -> tuple[list[Path], dict[int, str]]:
    env = Environment(loader=FileSystemLoader(settings.templates_dir), autoescape=select_autoescape(["html"]))
    env.filters["hl"] = highlight
    tpl = env.get_template("card.html")
    fonts = font_css()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    image_log: dict[int, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": settings.card_width, "height": settings.card_height}, device_scale_factor=1)
        def data_uri(p: Path) -> str:
            mime = {"svg": "image/svg+xml", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(p.suffix.lstrip("."), "image/png")
            return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()

        # 정보형 카드(3~7·9·10)의 흐린 배경은 1장 전경과 다른 사진으로 (card_n=0). 이미지형 카드는 1·2·8
        bg_path, bg_how = illustration(0, industry)
        bg_uri = data_uri(bg_path)
        image_log[0] = f"background={bg_how}"
        from gongsi.logos import logo_info

        logo, logo_tone = logo_info(corp)
        logo_uri = data_uri(logo) if logo else ""
        for c in cards:
            image = bg_uri
            if c.kind == "image":
                img_path, how = illustration({"hero": 1, "about": 2, "risk": 8}.get(c.role, c.n), industry)
                image, image_log[c.n] = data_uri(img_path), how
            html = tpl.render(card=c, corp=corp, w=settings.card_width, h=settings.card_height,
                              image=image, fonts=fonts, logo=logo_uri, logo_tone=logo_tone, total=len(cards))
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(200)
            path = out_dir / f"{prefix}_{c.n:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            paths.append(path)
        browser.close()
    return paths, image_log
