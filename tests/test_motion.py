"""모션 리포트: 숫자 읽기, 훅 말투, 대본·자막 맞추기"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gongsi import motion
from gongsi.hook import Hook
from gongsi.models import IpoFacts, Summary

ROOT = Path(__file__).resolve().parent.parent


def test_num_ko():
    assert motion.num_ko(19000) == "만 구천"
    assert motion.num_ko(29551) == "이만 구천오백오십일"
    assert motion.num_ko(284) == "이백팔십사"
    assert motion.num_ko(1200000000) == "십이억"
    assert motion.num_ko(110000) == "십일만"
    assert motion.date_ko("2026-11-05") == "십일월 오일"
    assert motion.date_ko("2026-06-10") == "유월 십일"


def test_spoken_title():
    assert motion.spoken_title("AI칩 '피자 썰어주는' 회사가 내일 상장함") == "AI칩 피자 썰어주는 회사가 내일 상장해요."
    assert motion.spoken_title("반도체 '샤워기' 만드는 회사, 묶인 주식 풀림") == "반도체 샤워기 만드는 회사, 묶인 주식 풀려요."


def _labup():
    b = json.loads((ROOT / "posts/2026-11-02/20261027000261/bundle.json").read_text(encoding="utf-8"))
    return IpoFacts(**b["facts"]), Summary(**b["summary"]), Hook(**b["hooks"][b["chosen"]])


def test_script_numbers_come_from_facts_and_align():
    facts, summary, hook = _labup()
    segs = motion.script(facts, summary, hook, "listing", date(2026, 11, 2))
    assert [s.scene for s in segs] == ["hook", "about", "define", "value", "value", "demand", "lockup", "outro"]
    shown = " ".join(sh for s in segs for _, sh in s.tokens)
    assert "19,000원." in shown and "29,551원이에요." in shown and "284.53:1." in shown and "2.09%예요." in shown
    spoken = motion.tts_text(segs)
    assert "만 구천 원" in spoken and "이만 구천오백오십일 원" in spoken and not any(ch.isdigit() for ch in re.sub(r"<break[^>]*>", "", spoken))
    # TTS가 띄어쓰기대로 단어를 돌려준다고 보고 맞추기
    words, t = [], 0.0
    for w in re.sub(r"<break[^>]*>", "", spoken).split():
        words.append({"word": w, "start": t, "end": t + 0.3}); t += 0.35
    caps, scenes = motion.align(segs, words, t + 1)
    assert caps[0]["s"] == 0.0 and scenes[0]["t0"] == 0.0 and scenes[-1]["t1"] == t + 1
    assert all(a["t1"] == b["t0"] for a, b in zip(scenes, scenes[1:]))
    assert any(c["d"] == "29,551원이에요." for c in caps)
    assert [x["scene"] for x in scenes] == ["hook", "about", "define", "value", "demand", "lockup", "outro"]
    assert motion._term_short("Showerhead(샤워헤드)") == "샤워헤드" and motion._term_short("정전척") == "정전척"


def test_spoken_explain():
    assert motion.spoken_explain(": 정전척은 진공 환경에서 정전기력을 이용해 웨이퍼를 고정하는 장치임") == "정전척은 진공 환경에서 정전기력을 이용해 웨이퍼를 고정하는 장치예요."
    assert motion.spoken_explain(": 6세대 이상 라인에 쓰임") == ""


def test_stamp_and_credit():
    facts, _, _ = _labup()
    assert motion.stamp_text(facts, "listing", date(2026, 11, 2)) == "11월 3일 상장 · 내일"
    c = motion.credit("📄 DART\n📷 자료 화면: Pixabay\n※ 투자 권유 아님")
    assert c.count(motion.AI_VOICE_LINE) == 1 and motion.credit(c) == c


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
