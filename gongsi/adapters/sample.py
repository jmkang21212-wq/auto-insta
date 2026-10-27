"""키 없이 돌리기 위한 샘플 어댑터. fixtures/sample_ipo.json을 그대로 돌려줍니다.

픽스처의 회사는 가상이고, 응답 모양만 DART 실제 응답과 같습니다.
"""
from __future__ import annotations

import json
from pathlib import Path

from gongsi.adapters.base import Adapter
from gongsi.models import Disclosure, IpoFacts


class SampleAdapter(Adapter):
    name = "sample"
    account = "kr"

    def __init__(self, fixtures_dir: Path):
        self.data = json.loads((fixtures_dir / "sample_ipo.json").read_text(encoding="utf-8"))

    def list_disclosures(self, date_from: str, date_to: str) -> list[Disclosure]:
        return [Disclosure(**d) for d in self.data["disclosures"]]

    def ipo_facts(self, d: Disclosure) -> IpoFacts:
        return IpoFacts(**self.data["facts"][d.rcept_no])
