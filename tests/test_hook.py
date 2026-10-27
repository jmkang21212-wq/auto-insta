"""훅·릴스 도우미 시험 (네트워크·LLM 없이). `python tests/test_hook.py` 또는 pytest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi.adapters.dart_doc import parse  # noqa: E402
from gongsi.hook import Hook, check_hook, display_term, explain_line, line2  # noqa: E402
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


def test_bad_hooks_rejected():
    no_source = Hook(title="반도체 '냉장고' 만드는 회사가 상장함", term="냉장고", emoji="🧊", sub="진짜 냉장고 아님", video="fridge door")
    assert any("근거 용어" in p for p in check_hook(no_source, FACTS))
    with_number = Hook(title="매출 922억 '샤워기' 회사", term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="shower head")
    assert any("숫자" in p for p in check_hook(with_number, FACTS))
    hype = Hook(title="반도체 '샤워기' 대박 회사가 상장함", term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="shower head")
    assert any("대박" in p for p in check_hook(hype, FACTS))
    korean_query = Hook(title=GOOD.title, term="샤워헤드", emoji="🚿", sub="진짜 샤워기 아님", video="샤워기 물줄기")
    assert any("영상 검색어" in p for p in check_hook(korean_query, FACTS))


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
