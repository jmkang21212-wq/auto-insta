"""브랜드 이미지: 인스타그램 프로필 사진(1080×1080).

python -m gongsi.brand            # assets/brand/ipov-profile.png
프로필 사진은 API로 바꿀 수 없어서 인스타 앱에서 직접 올립니다 (프로필 편집 → 사진 변경).
워드마크 모양은 카드 템플릿과 같은 규칙: ipo 파란색, i 점 노란색, Pretendard Black.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from gongsi.config import settings
from gongsi.render import font_css

ROOT = settings.templates_dir.parent.parent
OUT = ROOT / "assets" / "brand"

PROFILE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
{fonts}
body{{margin:0;width:1080px;height:1080px;background:#0F1115;display:flex;align-items:center;justify-content:center;font-family:"Pretendard",sans-serif}}
.wm{{font-size:330px;font-weight:900;letter-spacing:-0.045em;line-height:1;color:#F2F4F6;white-space:nowrap;margin-top:-40px}}
.wm .b{{color:#5EA9FF}} .wm .dot{{position:relative;display:inline-block}}
.wm .dot i{{position:absolute;left:50%;top:.02em;width:.2em;height:.2em;border-radius:50%;background:#F5C84B;transform:translateX(-50%)}}
</style></head><body><span class="wm"><span class="b"><span class="dot">ı<i></i></span>po</span>v</span></body></html>"""


def profile_image(out: Path | None = None) -> Path:
    out = out or OUT / "ipov-profile.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1080, "height": 1080})
        page.set_content(PROFILE_HTML.format(fonts=font_css()), wait_until="load")
        page.wait_for_timeout(200)
        page.screenshot(path=str(out))
        b.close()
    return out


if __name__ == "__main__":
    path = profile_image(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    brand = json.loads((settings.fixtures_dir / "brand.json").read_text(encoding="utf-8"))
    print(json.dumps({"profile": str(path), "name": brand["name"], "reading": brand["reading"]}, ensure_ascii=False))
