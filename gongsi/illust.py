"""API 없이 그리는 업종별 벡터 일러스트 (SVG). images.py 의 폴백이자 기본값.

업종 문자열의 키워드로 테마를 고르고, 카드 번호에 따라 장면을 바꿉니다.
  1장: 업종 전경   2장: 제품 클로즈업   7장: 같은 테마 + 기울어진 저울(위험)
팔레트는 카드와 같은 다크 토큰이고, 사람·글자·로고는 그리지 않습니다.
"""
from __future__ import annotations

import math
import random

W, H = 1080, 800
BG, PANEL, BLUE, BLUE2, AMBER, LINE = "#0F1115", "#1A1D24", "#4795FF", "#2C77DF", "#F5C84B", "#2A2F3A"

THEMES = [
    ("semi", ["반도체", "전자", "디스플레이", "장비", "소재", "부품", "웨이퍼", "검사"]),
    ("bio", ["바이오", "제약", "의료", "헬스", "진단", "신약", "병원"]),
    ("soft", ["소프트웨어", "ai", "인공지능", "플랫폼", "it", "데이터", "클라우드", "보안", "게임", "앱"]),
    ("energy", ["배터리", "2차전지", "이차전지", "에너지", "전력", "태양", "수소"]),
    ("robot", ["로봇", "자동화", "기계", "모빌리티", "자동차", "드론", "항공"]),
    ("fin", ["금융", "핀테크", "증권", "보험", "결제", "스팩", "기업인수"]),
]


def theme_for(industry: str) -> str:
    s = (industry or "").lower()
    for name, keys in THEMES:
        if any(k in s for k in keys):
            return name
    return "abstract"


def _glow() -> str:
    return (
        '<defs>'
        '<radialGradient id="g1" cx="0.72" cy="0.3" r="0.5"><stop offset="0" stop-color="#1e3a8a" stop-opacity="0.9"/><stop offset="1" stop-color="#0F1115" stop-opacity="0"/></radialGradient>'
        '<radialGradient id="g2" cx="0.18" cy="0.9" r="0.45"><stop offset="0" stop-color="#7a5a1a" stop-opacity="0.8"/><stop offset="1" stop-color="#0F1115" stop-opacity="0"/></radialGradient>'
        '<clipPath id="wafer"><circle cx="700" cy="330" r="250"/></clipPath>'
        '</defs>'
        f'<rect width="{W}" height="{H}" fill="{BG}"/>'
        f'<rect width="{W}" height="{H}" fill="url(#g1)"/><rect width="{W}" height="{H}" fill="url(#g2)"/>'
    )


def _grid_dots(seed: str, n: int = 40) -> str:
    r = random.Random(seed)
    return "".join(
        f'<circle cx="{r.randint(40, W-40)}" cy="{r.randint(40, H-40)}" r="{r.choice([2,2,3])}" fill="#ffffff" opacity="{r.choice([0.08,0.12,0.18])}"/>'
        for _ in range(n)
    )


def _scale(cx: int = 540, cy: int = 470) -> str:
    """위험 카드용 기울어진 저울."""
    a = math.radians(-11)
    L = 300
    x1, y1 = cx - L * math.cos(a), cy - L * math.sin(a)
    x2, y2 = cx + L * math.cos(a), cy + L * math.sin(a)
    return (
        f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy+170}" stroke="{LINE}" stroke-width="14" stroke-linecap="round"/>'
        f'<line x1="{cx-110}" y1="{cy+170}" x2="{cx+110}" y2="{cy+170}" stroke="{LINE}" stroke-width="14" stroke-linecap="round"/>'
        f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{AMBER}" stroke-width="12" stroke-linecap="round"/>'
        f'<circle cx="{cx}" cy="{cy}" r="18" fill="{BLUE}"/>'
        f'<path d="M{x1-70:.0f} {y1+70:.0f} L{x1+70:.0f} {y1+70:.0f} L{x1+50:.0f} {y1+120:.0f} L{x1-50:.0f} {y1+120:.0f} Z" fill="{PANEL}" stroke="{AMBER}" stroke-width="5"/>'
        f'<path d="M{x2-70:.0f} {y2+70:.0f} L{x2+70:.0f} {y2+70:.0f} L{x2+50:.0f} {y2+120:.0f} L{x2-50:.0f} {y2+120:.0f} Z" fill="{PANEL}" stroke="{AMBER}" stroke-width="5"/>'
        f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x1:.0f}" y2="{y1+70:.0f}" stroke="{AMBER}" stroke-width="5"/>'
        f'<line x1="{x2:.0f}" y1="{y2:.0f}" x2="{x2:.0f}" y2="{y2+70:.0f}" stroke="{AMBER}" stroke-width="5"/>'
    )


# --- themes --------------------------------------------------------------------
def semi(card_n: int) -> str:
    if card_n == 2:  # 칩 클로즈업
        body = f'<rect x="340" y="200" width="400" height="400" rx="28" fill="{PANEL}" stroke="{BLUE}" stroke-width="6"/>'
        body += f'<rect x="420" y="280" width="240" height="240" rx="14" fill="#11151f" stroke="{BLUE2}" stroke-width="4"/>'
        for i in range(10):
            y = 230 + i * 36
            body += f'<rect x="282" y="{y}" width="58" height="14" rx="4" fill="{AMBER}" opacity="0.9"/><rect x="740" y="{y}" width="58" height="14" rx="4" fill="{AMBER}" opacity="0.9"/>'
            x = 370 + i * 36
            body += f'<rect x="{x}" y="142" width="14" height="58" rx="4" fill="{AMBER}" opacity="0.9"/><rect x="{x}" y="600" width="14" height="58" rx="4" fill="{AMBER}" opacity="0.9"/>'
        for i in range(5):
            body += f'<line x1="440" y1="{300+i*44}" x2="640" y2="{300+i*44}" stroke="{BLUE}" stroke-width="3" opacity="0.5"/>'
        return body
    # 웨이퍼 + 다이 격자 + 검사 빔
    body = f'<circle cx="700" cy="330" r="250" fill="{PANEL}" stroke="{BLUE}" stroke-width="5"/>'
    body += '<g clip-path="url(#wafer)">'
    for gx in range(430, 980, 46):
        for gy in range(60, 610, 46):
            body += f'<rect x="{gx}" y="{gy}" width="40" height="40" rx="4" fill="#11151f" stroke="{BLUE2}" stroke-width="2" opacity="0.9"/>'
    body += f'<rect x="440" y="300" width="560" height="22" fill="{AMBER}" opacity="0.35"/>'
    body += '</g>'
    body += f'<line x1="120" y1="640" x2="700" y2="330" stroke="{AMBER}" stroke-width="6" stroke-linecap="round" opacity="0.9"/>'
    body += f'<circle cx="700" cy="330" r="16" fill="{AMBER}"/>'
    body += f'<rect x="60" y="600" width="140" height="100" rx="18" fill="{PANEL}" stroke="{BLUE}" stroke-width="5"/>'
    body += f'<circle cx="130" cy="650" r="26" fill="{BLUE}" opacity="0.8"/>'
    return body


def bio(card_n: int) -> str:
    if card_n == 2:
        return (f'<rect x="300" y="330" width="480" height="150" rx="75" fill="{PANEL}" stroke="{BLUE}" stroke-width="6"/>'
                f'<path d="M540 330 h240 a75 75 0 0 1 0 150 h-240 z" fill="{AMBER}" opacity="0.9"/>'
                f'<circle cx="420" cy="405" r="22" fill="{BLUE}" opacity="0.6"/>')
    r = random.Random("bio")
    nodes = [(r.randint(180, 900), r.randint(120, 680)) for _ in range(9)]
    body = ""
    for i, (x, y) in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            x2, y2 = nodes[j]
            if math.hypot(x - x2, y - y2) < 300:
                body += f'<line x1="{x}" y1="{y}" x2="{x2}" y2="{y2}" stroke="{BLUE2}" stroke-width="4" opacity="0.6"/>'
    for i, (x, y) in enumerate(nodes):
        body += f'<circle cx="{x}" cy="{y}" r="{26 if i%3 else 40}" fill="{AMBER if i%4==0 else PANEL}" stroke="{BLUE}" stroke-width="5"/>'
    return body


def soft(card_n: int) -> str:
    if card_n == 2:
        return (f'<rect x="380" y="120" width="320" height="580" rx="44" fill="{PANEL}" stroke="{BLUE}" stroke-width="6"/>'
                f'<rect x="420" y="190" width="240" height="120" rx="18" fill="{BLUE}" opacity="0.85"/>'
                f'<rect x="420" y="340" width="240" height="40" rx="10" fill="{LINE}"/><rect x="420" y="400" width="180" height="40" rx="10" fill="{LINE}"/>'
                f'<rect x="420" y="480" width="240" height="60" rx="16" fill="{AMBER}"/>')
    body = ""
    for i, (x, y) in enumerate([(120, 160), (240, 240), (360, 320)]):
        body += f'<rect x="{x}" y="{y}" width="560" height="360" rx="24" fill="{PANEL}" stroke="{BLUE if i==2 else LINE}" stroke-width="5"/>'
    body += f'<rect x="400" y="370" width="200" height="22" rx="8" fill="{AMBER}"/><rect x="400" y="420" width="420" height="16" rx="8" fill="{LINE}"/><rect x="400" y="456" width="360" height="16" rx="8" fill="{LINE}"/>'
    body += f'<polyline points="400,600 480,560 560,580 640,500 720,520 800,440" fill="none" stroke="{BLUE}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
    return body


def energy(card_n: int) -> str:
    if card_n == 2:
        return (f'<rect x="340" y="260" width="400" height="200" rx="34" fill="{PANEL}" stroke="{BLUE}" stroke-width="6"/>'
                f'<rect x="740" y="320" width="40" height="80" rx="10" fill="{BLUE}"/>'
                f'<rect x="370" y="290" width="280" height="140" rx="20" fill="{AMBER}" opacity="0.9"/>'
                f'<polygon points="560,230 500,370 560,370 520,500 620,330 560,330" fill="{BG}" opacity="0.9"/>')
    body = ""
    for i in range(4):
        x = 150 + i * 210
        body += f'<rect x="{x}" y="200" width="150" height="400" rx="26" fill="{PANEL}" stroke="{BLUE}" stroke-width="5"/>'
        body += f'<rect x="{x+50}" y="170" width="50" height="30" rx="8" fill="{BLUE}"/>'
        body += f'<rect x="{x+20}" y="{600-20-(i+1)*85}" width="110" height="{(i+1)*85}" rx="14" fill="{AMBER}" opacity="0.9"/>'
    return body


def robot(card_n: int) -> str:
    body = (f'<rect x="120" y="560" width="300" height="80" rx="20" fill="{PANEL}" stroke="{BLUE}" stroke-width="5"/>'
            f'<polyline points="270,560 300,380 520,300 760,340" fill="none" stroke="{BLUE}" stroke-width="40" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="300" cy="380" r="34" fill="{AMBER}"/><circle cx="520" cy="300" r="34" fill="{AMBER}"/>'
            f'<rect x="760" y="300" width="120" height="80" rx="18" fill="{PANEL}" stroke="{AMBER}" stroke-width="6"/>')
    if card_n == 2:
        body += f'<rect x="700" y="470" width="240" height="120" rx="22" fill="{PANEL}" stroke="{BLUE}" stroke-width="5"/>'
    return body


def fin(card_n: int) -> str:
    body = ""
    for i in range(3):
        for j in range(3 + i):
            body += f'<ellipse cx="{200+i*180}" cy="{600-j*28}" rx="80" ry="22" fill="{PANEL}" stroke="{AMBER}" stroke-width="5"/>'
    body += f'<polyline points="620,560 700,480 780,520 860,380 940,300" fill="none" stroke="{BLUE}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>'
    body += f'<circle cx="940" cy="300" r="18" fill="{BLUE}"/>'
    return body


def abstract(card_n: int) -> str:
    r = random.Random(f"abs{card_n}")
    body = ""
    for i in range(6):
        cx, cy, rad = r.randint(200, 900), r.randint(150, 650), r.randint(80, 220)
        body += f'<circle cx="{cx}" cy="{cy}" r="{rad}" fill="none" stroke="{BLUE if i%2 else AMBER}" stroke-width="{r.randint(3,9)}" opacity="0.55"/>'
    return body


DRAW = {"semi": semi, "bio": bio, "soft": soft, "energy": energy, "robot": robot, "fin": fin, "abstract": abstract}


def svg(card_n: int, industry: str) -> str:
    theme = theme_for(industry)
    body = DRAW[theme](card_n)
    if card_n == 7:
        body = f'<g opacity="0.35">{body}</g>' + _scale()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            + _glow() + _grid_dots(f"{theme}{card_n}") + body + '</svg>')
