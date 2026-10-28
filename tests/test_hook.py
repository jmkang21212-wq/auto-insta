"""훅·릴스 도우미 시험 (네트워크·LLM 없이). `python tests/test_hook.py` 또는 pytest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi.adapters.dart_doc import parse  # noqa: E402
from gongsi.hook import Hook, aliases, check_hook, display_term, explain_line, line2  # noqa: E402
from gongsi.models import IpoFacts  # noqa: E402
from gongsi.reel import SAFE_BOTTOM, SAFE_TOP, VIDEO_H, VIDEO_TOP, _has_people, _keywords, _score, layout_boxes, split_title  # noqa: E402

GLOSSARY_DOC = """II. 사업의 내용
사업에 대한 이해를 돕기 위해 당사가 영위하는 사업 분야 및 기술, 제품에 대한 전문용어 해설을 아래 표와 같이 기재합니다.
| [용어의 정의]
| 용어 | 내용
| 내식성 | 화학물질 및 부식 환경에 견디는 성질
| Showerhead(샤워헤드) | 반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품
| Class 100Clean Room | 0.5μm 이상 입자가 1입방피트당 100개 이하로 유지되는 청정 등급
1. 사업의 개요
"""

FACTS = IpoFacts(corp_name="동원파츠", business_evidence=[
    "당사의 주요 제품은 반도체 전공정 장비에 적용되는 샤워헤드(Showerhead), 챔버파츠(Chamber Parts), 히터(Heater), 스핀척(Spin Chuck) 및 각종 조립품입니다."],
    glossary={"Showerhead(샤워헤드)": "반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품",
              "Class 100Clean Room": "0.5μm 이상 입자가 1입방피트당 100개 이하로 유지되는 청정 등급"},
    band_low_krw=23000, band_high_krw=26500, min_shares=20, deposit_rate_pct=50, subscription_start="2026-11-02")

GOOD = Hook(title="반도체 '샤워기' 만드는 회사가 상장함", term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="shower head water")


def test_glossary_table_parsed():
    g = parse(GLOSSARY_DOC).glossary
    assert g["Showerhead(샤워헤드)"] == "반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품"
    assert "용어" not in g and len(g) == 3


def test_good_hook_passes_and_explains_from_filing():
    assert check_hook(GOOD, FACTS) == []
    assert explain_line("샤워헤드", FACTS.glossary) == ": 샤워헤드는 반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품임"
    assert display_term("Showerhead(샤워헤드)") == "샤워헤드"
    assert explain_line("Class 100Clean Room", FACTS.glossary) == ""  # 숫자가 든 풀이는 안 씀


def test_explain_finds_english_glossary_key_from_korean_term():
    """신고서 해설 표 키가 영어뿐('Spin Chuck')이어도 사업 원문의 '스핀척(Spin Chuck)' 짝으로 풀이를 찾는다"""
    gl = {"Spin Chuck": "웨이퍼를 고정 및 회전시키는 핵심 부품", "Heater": "웨이퍼 및 장비 내부 온도를 제어하는 열 제어 부품",
          "Etch": "플라즈마 및 화학반응을 통해 웨이퍼 표면을 선택적으로 제거하는 식각 공정",
          "High Aspect Ratio Etch": "높은 종횡비 구조를 정밀하게 식각하는 반도체 공정 기술"}
    al = aliases(["주요 제품은 샤워헤드(Showerhead), 히터(Heater), 스핀척(Spin Chuck) 및 각종 조립품(Assembly) 제품 등으로"])
    assert explain_line("스핀척", gl, al) == ": 스핀척은 웨이퍼를 고정 및 회전시키는 핵심 부품임"
    assert explain_line("Heater", gl, al) == ": 히터는 웨이퍼 및 장비 내부 온도를 제어하는 열 제어 부품임"  # 한글 이름으로 보여 줌
    assert explain_line("Etch", gl, al).startswith(": Etch는 플라즈마")  # 이름이 똑같은 키가 부분 일치보다 먼저
    # 훅 용어를 '샤워헤드(Showerhead)'처럼 적어도 캡션엔 한글 이름만 (2026-10-27 Actions 실행에서 괄호째 나왔던 것)
    assert explain_line("샤워헤드(Showerhead)", FACTS.glossary) == ": 샤워헤드는 반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품임"
    # 해설 표에도 원문 정의 문장에도 없으면 비유가 무엇을 빗댄 건지만 밝힌다 ('이 회사 제품'은 부품사에 틀릴 수 있어 안 씀)
    assert explain_line("조립품", gl, al, "반도체 '레고' 만드는 회사가 상장함") == ": '레고'는 조립품을 빗댄 말임"
    assert explain_line("조립품", gl, al) == ""
    # 비유가 용어와 안 닮았으면(전기장판·히터) 무엇을 빗댔는지 먼저, 닮았으면(샤워기·샤워헤드) 바로 정의
    assert explain_line("Heater", gl, al, "칩용 '전기장판' 만드는 회사가 상장함") == \
        ": '전기장판'은 히터를 빗댄 말임. 히터는 웨이퍼 및 장비 내부 온도를 제어하는 열 제어 부품임"
    assert explain_line("샤워헤드", FACTS.glossary, None, "반도체 장비 속 '샤워기' 만드는 회사가 상장함").startswith(": 샤워헤드는")


def test_explain_from_business_text_when_no_glossary():
    """용어 해설 표가 없는 회사(옵토닉스, 2026-10-27): 사업 원문의 정의 문장을 음슴체로. 영문 괄호만 뺀다"""
    ev = ["당사는 “광학식 자이로스코프”인 RLG (Ring Laser Gyroscope)의 핵심 광학 구성품을 제조합니다.",
          "자이로스코프(Gyroscope)는 물체의 회전 운동을 감지하고 측정하는 센서로, 관성항법장치(Inertial Navigation System, INS)의 핵심 구성 요소입니다."]
    line = explain_line("자이로스코프", {}, aliases(ev), "비행기 '균형 감각' 만드는 회사가 상장함", ev)
    assert line == (": '균형 감각'은 자이로스코프를 빗댄 말임. "
                    "자이로스코프는 물체의 회전 운동을 감지하고 측정하는 센서로, 관성항법장치의 핵심 구성 요소임"), line
    from gongsi import cards
    lines = cards.wrap(line)
    assert sum(l.startswith(": ") for l in lines) == 2 and not any(l.endswith(".") for l in lines), lines
    assert explain_line("자이로스코프", {}, None, "", ["자이로스코프는 1초에 100번 회전을 재는 센서입니다."]) == ""  # 숫자 든 정의는 안 씀


def test_fixed_term_explanation_when_filing_has_none():
    """신고서에 정의가 없으면 fixtures/terms.json의 고정 풀이 (에스에프씨 '발광재료', 2026-10-28)"""
    line = explain_line("발광재료", {}, None, "'화면 물감' 만드는 회사가 상장함", ["당사는 OLED 소재를 생산합니다."])
    assert line == ": '화면 물감'은 발광재료를 빗댄 말임. 발광재료는 전기를 받으면 스스로 빛을 내는 재료임", line
    h = Hook(title="t", term="x", emoji="e", sub="s", video="kid finger painting", alt_videos=["colorful paint splash", "kid finger painting"])
    assert h.queries() == ["kid finger painting", "colorful paint splash"]


def test_use_line_checked_against_business_text():
    """썸네일 '쓰이는 곳'은 사업 원문 낱말만 (띄어쓰기는 무시)"""
    from gongsi.hook import check_use
    f = IpoFacts(corp_name="x", business_evidence=["국내외 AI데이터센터용 SOFC 수소발전 터보기계와 친환경 수소차용 터보기계, 친환경 오폐수 처리"])
    assert check_use("오폐수 처리·AI 데이터센터·수소차에 쓰임", f) == []
    assert any("우주선" in p for p in check_use("우주선·수소차에 쓰임", f))
    assert check_use("", f) and any("숫자" in p for p in check_use("수소차 3종에 쓰임", f))


def test_bad_hooks_rejected():
    no_source = Hook(title="반도체 '냉장고' 만드는 회사가 상장함", term="냉장고", emoji="🧊", sub="진짜 냉장고 아님", video="fridge door")
    assert any("근거 용어" in p for p in check_hook(no_source, FACTS))
    with_number = Hook(title="매출 922억 '샤워기' 회사", term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="shower head")
    assert any("숫자" in p for p in check_hook(with_number, FACTS))
    hype = Hook(title="반도체 '샤워기' 대박 회사가 상장함", term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="shower head")
    assert any("대박" in p for p in check_hook(hype, FACTS))
    korean_query = Hook(title=GOOD.title, term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="샤워기 물줄기")
    assert any("영상 검색어" in p for p in check_hook(korean_query, FACTS))
    rocket = Hook(title=GOOD.title, term="샤워헤드", emoji="🚀", sub="진짜 샤워기 아님", video="shower head")
    assert any("🚀" in p for p in check_hook(rocket, FACTS))  # 브랜드 규칙의 상승 암시 이모지
    sure = Hook(title=GOOD.title, term="샤워헤드", emoji="🚿", sub="무조건 샤워기", video="shower head")
    assert any("무조건" in p for p in check_hook(sure, FACTS))
    dash = Hook(title=GOOD.title, term="샤워헤드", emoji="🚿", sub="샤워기 — 아님", video="shower head")
    assert any("긴 대시" in p for p in check_hook(dash, FACTS))


def test_second_line_numbers():
    """둘째 줄은 경쟁률 (2026-10-28: 얼마부터 청약 가능보다 경쟁률이 더 궁금함)"""
    assert line2(FACTS) == "경쟁률은 청약 전에 공개 👀"  # 수요예측 날짜를 못 읽었을 때
    assert line2(FACTS.model_copy(update={"forecast_end": "2026-10-28"})) == "경쟁률은 10월 28일 이후 공개 👀"
    hot = IpoFacts(corp_name="x", demand_ratio=1134.5)
    assert line2(hot) == "기관 경쟁률 1,134.5:1 🔥"
    assert line2(IpoFacts(corp_name="x", demand_ratio=88.2)) == "기관 경쟁률 88.2:1 👀"


def test_video_tag_scoring():
    words = _keywords("shower head water flowing")
    assert words == ["shower", "head"]
    assert _score("shower head, watercourse, water, shower", words) == 4  # 두 단어 모두 + '두 단어 그대로'
    assert _score("water, flow, rush, waterfall, showers", words) == 0  # 핵심 명사 하나만 맞으면 안 씀
    assert _score("ocean, sea, wave, water", words) == 0
    assert _score("woman, shower head, bathing, soap", words) >= 2 and _has_people("woman, shower, bathing, soap")  # 사람은 허용
    assert _score("woman, shower head, seductive, glamour", words) == -1  # 선정적인 장면만 제외
    coil = _keywords("electric heating coil glowing")
    assert _score("space, galaxy, electric, nebula", coil) == 0  # 'electric' 하나만 맞는 우주 영상
    assert _score("heating, coil, red, hot, metal", coil) == 2
    assert _score("pizza, slice, food", _keywords("pizza slices closeup")) == 2  # 복수형도 맞춤


def test_title_split_two_balanced_lines():
    assert split_title("반도체 '샤워기' 만드는 회사가 상장함") == ["반도체 '샤워기'", "만드는 회사가 상장함"]


def test_reel_text_inside_safe_zone():
    """릴스 글자가 인스타 화면 위·아래 가림 영역 밖에. 브라우저로 실제 배치를 잰다.
    2026-10-27 '아이폰에서 로고 윗부분이 가려짐' → 위 270px 비움. 2026-10-28 '어디에 쓰이는지 썸네일에' + '로고는 두고 경쟁률은 영상 아래로'.
    프로필 격자는 가운데 3:4(y 240~1680)만 보여서 그 안에 다 들어가야 함"""
    # 2026-10-28 밤: 영상 아래 줄은 가격만('15,300원 → 21,304원') 크게, 아래 공간을 더 씀(SAFE_BOTTOM 1600).
    # 길면(희망 공모가 범위) 한 줄로 화면 안(양옆 40px)에 들어가게 글자가 줄어야 함
    for line in ("(15,300원 → 21,304원)", "(12,600~15,300원 → 21,304원)", "(기관 경쟁률 1,134.5:1 🔥)"):
        bx = layout_boxes("비행기 '균형 감각' 만드는 회사가 상장함", line, "비행기용 달팽이관", "오폐수 처리·AI 데이터센터·수소차에 쓰임")
        assert 40 - 1 <= bx["s_x"][0] and bx["s_x"][1] <= 1080 - 40 + 1, (line, bx["s_x"])
        assert bx["s"][1] - bx["s"][0] < 100, bx  # 두 줄로 안 접힘
        assert bx["ar"][0] - bx["s"][1] >= 28 and bx["ar"][1] <= SAFE_BOTTOM <= 1680, bx  # 격자(3:4) 안
    bx = layout_boxes("비행기 '균형 감각' 만드는 회사가 상장함", "(기관 경쟁률 1,134.5:1 🔥)", "비행기용 달팽이관", "오폐수 처리·AI 데이터센터·수소차에 쓰임")
    assert SAFE_TOP <= bx["wm"][0] < bx["wm"][1] < bx["t"][0] < bx["t"][1] < bx["u"][0], bx  # 로고 → 제목 → 쓰이는 곳
    assert bx["u"][1] <= VIDEO_TOP - 10, bx  # 쓰이는 곳이 영상에 안 겹침
    assert VIDEO_TOP <= bx["sub"][0] and bx["sub"][1] <= VIDEO_TOP + VIDEO_H, bx  # 자막은 영상 안
    assert VIDEO_TOP + VIDEO_H < bx["s"][0] < bx["s"][1] < bx["ar"][0], bx  # 경쟁률은 영상 아래, 그 아래 '자세한 내용'
    assert bx["ar"][0] - bx["s"][1] >= 28, bx  # 2026-10-28 '경쟁률 문구랑 간격을 좀 띄워줘' (전에는 10px)
    assert bx["ar"][1] <= SAFE_BOTTOM, bx  # 아이디·캡션 줄 위


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
