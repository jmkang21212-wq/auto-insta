from __future__ import annotations

from abc import ABC, abstractmethod

from gongsi.models import Disclosure, IpoFacts


class Adapter(ABC):
    name: str = "base"
    account: str = "kr"

    @abstractmethod
    def list_disclosures(self, date_from: str, date_to: str) -> list[Disclosure]:
        """기간 내 공시 목록. 유형 필터 전 단계."""

    @abstractmethod
    def ipo_facts(self, d: Disclosure) -> IpoFacts:
        """증권신고서(지분증권) 한 건의 구조화 필드."""
