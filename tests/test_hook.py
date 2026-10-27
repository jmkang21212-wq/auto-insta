"""훅·릴스 도우미 시험 (네트워크·LLM 없이). `python tests/test_hook.py` 또는 pytest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi.adapters.dart_doc import parse  # noqa: E402
from gongsi.hook import Hook, aliases, check_hook, display_term, explain_line, line2  # noqa: E402
from gongsi.models import IpoFacts  # noqa: E402
from gongsi.reel import _has_people, _keywords, _score, split_title  # noqa: E402

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
    # 해설 표에 없으면 비유가 무엇을 빗댄 건지만 밝힌다
    assert explain_line("조립품", gl, al, "반도체 '레고' 만드는 회사가 상장함") == ": '레고'는 이 회사 제품 조립품을 빗댄 말임"
    assert explain_line("조립품", gl, al) == ""


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
    assert line2(FACTS) == "23만원부터 청약 가능 💸"
    hot = IpoFacts(corp_name="x", demand_ratio=1134.5)
    assert line2(hot) == "경쟁률 1,134.5:1 🔥"
    assert line2(IpoFacts(corp_name="x", demand_ratio=88.2)) == "경쟁률 88.2:1 👀"


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


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
