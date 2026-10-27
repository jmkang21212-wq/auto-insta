"""카드 배경·일러스트 이미지.

IMAGE_PROVIDER 는 우선순위 목록입니다. 예: "pexels,gemini,none" → 사진 검색 실패 시 생성, 그것도 실패하면 벡터.
- pexels   : 사진 검색 (PEXELS_API_KEY)
- pixabay  : 사진 검색 (PIXABAY_API_KEY, 가입 즉시 발급). Content License, 상업 이용 가능으로 알려짐 (확인 필요)
- openverse: 사진 검색, 키 불필요. CC 라이선스라 작가·라이선스 표기 필요 (json에 attribution 저장)
- gemini : Google AI Studio 키 (GOOGLE_API_KEY), 모델 gemini-2.5-flash-image  (단가·약관 확인 필요)
- openai : OPENAI_API_KEY, 모델 gpt-image-1                                  (단가·약관 확인 필요)
- none   : illust.py 가 업종 키워드로 그리는 벡터 일러스트 (API 없음)

규칙
- 생성 프롬프트와 사진 검색어에 회사명·로고·실제 제품명·사람 얼굴·글자를 넣지 않는다. 업종 장면만.
- 같은 (업종, 카드) 조합은 out/images/ 에 캐시한다. 사진은 작가·출처 URL을 같은 이름의 .json에 남긴다.
- 실패는 예외 없이 다음 제공자로 내려가고, 어느 경로로 만들어졌는지 문자열로 돌려준다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import requests

from gongsi.config import settings
from gongsi.illust import theme_for

STYLE = (
    "cinematic editorial photograph, dark moody lighting, deep navy and amber tones, shallow depth of field, "
    "no text, no letters, no logos, no people, no faces, no brand marks, portrait 4:5, high detail"
)

SCENES = {
    1: "a wide establishing scene of the industry: {industry}",
    2: "the main product or equipment of a {industry} company, close-up, on a dark surface",
    7: "an abstract metaphor of business risk for a {industry} company: a single large basket, a tilted scale, dark",
}

# 사진 검색어: 테마별 (1장 전경 / 2장 제품 / 7장 위험 은유)
# 사진 검색: 테마별 (1장 전경 / 2장 제품 / 7장 위험 은유). 각 항목은 (검색어 후보들, 태그에 꼭 있어야 하는 단어들)
# 사진 검색: 테마별 (1장 넓은 장면 / 2장 제품 클로즈업). 각 항목은 (검색어, 태그에 꼭 있어야 하는 단어들)
PHOTO_QUERIES = {
    "semi": ([("semiconductor factory cleanroom", ["semiconductor", "cleanroom", "factory", "manufactur"]),
              ("industrial technology machine blue", ["industry", "machine", "technology"])],
             [("microchip macro", ["chip", "processor", "microchip"]), ("silicon wafer", ["wafer", "silicon"])]),
    "bio": ([("laboratory scientist research", ["laboratory", "lab", "research", "science"])],
            [("pills capsules medicine macro", ["capsule", "pill", "medicine", "pharmacy"]), ("test tubes laboratory", ["test tube", "laboratory", "lab"])]),
    "soft": ([("data center server room", ["server", "data center", "datacenter"]), ("city network night", ["city", "network", "night"])],
             [("programming code screen", ["code", "programming", "software"]), ("smartphone app screen", ["smartphone", "phone", "app"])]),
    "energy": ([("solar panels wind turbines", ["solar", "wind", "turbine", "energy"]), ("battery factory industrial", ["battery", "industry", "factory"])],
               [("lithium battery pack", ["battery", "lithium"]), ("electric car charging", ["electric", "charging", "car"])]),
    "robot": ([("industrial robot arm factory", ["robot", "industrial", "factory"]), ("factory automation line", ["factory", "automation", "industry"])],
              [("robotic arm closeup", ["robot", "robotic"]), ("gears machine macro", ["gear", "machine"])]),
    "fin": ([("financial district skyline night", ["city", "skyline", "finance", "building"]), ("stock market chart screen", ["stock", "market", "chart", "trading"])],
            [("coins money stack", ["coin", "money"]), ("credit card payment", ["credit card", "payment", "card"])]),
    "abstract": ([("abstract dark blue technology", ["abstract", "technology", "blue"]), ("city night bokeh", ["city", "night", "bokeh"])],
                 [("geometric architecture dark", ["architecture", "geometric"]), ("abstract light lines", ["abstract", "light", "line"])]),
}

# 7장(위험)은 업종과 무관한 은유 사진으로 톤을 가른다
RISK_QUERIES = [
    ("balance scale justice", ["scale", "balance", "justice"]),
    ("storm clouds dark sky", ["storm", "cloud", "sky"]),
    ("cracked glass", ["crack", "glass", "broken"]),
    ("tightrope rope", ["rope", "tightrope"]),
    ("fog road night", ["fog", "road", "mist"]),
]


_PLAN_CACHE: dict[str, dict] = {}


def query_plan(industry: str) -> dict | None:
    """업종 문자열로 카드별 영어 검색어와 필수 태그를 Claude가 만든다. 키가 없으면 None → 정적 표."""
    if not settings.anthropic_api_key:
        return None
    if industry in _PLAN_CACHE:
        return _PLAN_CACHE[industry]
    import anthropic
    from gongsi.summarize import MODEL
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = f"""회사 업종: "{industry}"
이 업종의 인스타그램 카드에 쓸 스톡 사진을 Pixabay에서 찾으려 합니다. 영어 검색어를 만들어 주세요.
- scene: 이 업종의 현장·설비·제품이 한눈에 보이는 넓은 장면 (예: 반도체 → "semiconductor cleanroom wafer")
- product: 이 업종의 핵심 제품이나 장비 클로즈업
- risk: 같은 업종 사진이지만 scene·product와 겹치지 않는 세 번째 피사체
각 항목마다 검색어 후보 3개와, 결과 사진의 태그에 반드시 하나는 들어 있어야 하는 영어 단어 목록(3~6개, 소문자, 업종을 식별하는 단어만)을 주세요.
사람 얼굴, 회사명, 로고, 글자가 나올 만한 검색어는 피하세요. 낡고 녹슬고 더러운 느낌(rust, old, vintage, broken)도 피하고, 깨끗한 현대 산업 장면을 고르세요.
JSON만 출력: {{"scene": [["query", ["tag1","tag2"]], ...], "product": [...], "risk": [...]}}"""
    msg = client.messages.create(model=MODEL, max_tokens=1200, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    plan = json.loads(text[text.find("{"): text.rfind("}") + 1])
    _PLAN_CACHE[industry] = plan
    (settings.out_dir / "images").mkdir(parents=True, exist_ok=True)
    (settings.out_dir / "images" / f"plan_{hashlib.sha1(industry.encode()).hexdigest()[:8]}.json").write_text(
        json.dumps({"industry": industry, **plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


def pick_hit(industry: str, purpose: str, hits: list[dict]) -> dict | None:
    """후보 사진들의 태그를 보고 가장 관련 있는 것을 Claude가 고른다. 전부 무관하면 None."""
    if not settings.anthropic_api_key or not hits:
        return max(hits, key=lambda x: x.get("downloads", 0)) if hits else None
    import anthropic
    from gongsi.summarize import MODEL
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    listing = "\n".join(f'{h["id"]}: {h.get("tags","")}' for h in hits[:20])
    prompt = f"""업종 "{industry}"의 인스타그램 카드에 쓸 배경 사진을 고릅니다. 용도: {purpose}.
아래는 후보 사진의 id와 태그입니다. 이 업종과 가장 직접적으로 관련된 사진 id 하나만 답하세요.
사람 얼굴·로고·글자가 주인공일 것 같은 태그(portrait, logo, text, sign)와 낡은 느낌(rust, rusty, old, vintage, grunge, broken)은 피하세요. 전부 무관하면 none.
{listing}
답은 id 숫자 또는 none 만."""
    msg = client.messages.create(model=MODEL, max_tokens=20, messages=[{"role": "user", "content": prompt}])
    ans = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    for h in hits:
        if str(h["id"]) == ans:
            return h
    return None


def photo_queries(card_n: int, industry: str) -> list[tuple[str, list[str]]]:
    try:
        plan = query_plan(industry)
    except Exception:
        plan = None
    if plan:
        # 0 = 정보 카드(3~7·9·10장)의 흐린 배경. 1장과 같은 'scene' 검색어를 쓰되 중복 제거로 다른 사진이 뽑힌다
        key = {0: "scene", 1: "scene", 2: "product", 8: "risk"}.get(card_n, "scene")
        qs = [(q, [t.lower() for t in tags]) for q, tags in plan.get(key, [])]
        if card_n == 0 and len(qs) > 1:
            qs = qs[1:] + qs[:1]  # 배경은 두 번째 검색어부터 (1장과 피사체까지 달라지게)
        return qs or photo_queries_static(card_n, industry)
    return photo_queries_static(card_n, industry)


def photo_queries_static(card_n: int, industry: str) -> list[tuple[str, list[str]]]:
    qs = PHOTO_QUERIES[theme_for(industry)]
    return qs[1 if card_n == 2 else 0]


def prompt_for(card_n: int, industry: str) -> str:
    return SCENES.get({8: 7}.get(card_n, card_n), SCENES[1]).format(industry=industry or "technology") + ", " + STYLE


def _cache_path(card_n: int, industry: str, provider: str, ext: str = "png") -> Path:
    key = hashlib.sha1(f"{provider}|{card_n}|{industry}".encode()).hexdigest()[:12]
    d = settings.out_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{provider}_{card_n}_{key}.{ext}"


# --- providers ---------------------------------------------------------------
def gen_pexels(card_n: int, industry: str, out: Path) -> None:
    key = os.environ["PEXELS_API_KEY"]
    q = photo_queries(card_n, industry)[0][0]
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                     params={"query": q, "orientation": "portrait", "per_page": 5, "size": "large"}, timeout=30)
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"pexels 결과 없음: {q}")
    p = photos[0]
    img = requests.get(p["src"]["large2x"], timeout=60)
    img.raise_for_status()
    out.write_bytes(img.content)
    out.with_suffix(".json").write_text(json.dumps(
        {"provider": "pexels", "query": q, "photographer": p.get("photographer"), "url": p.get("url"), "id": p.get("id")},
        ensure_ascii=False), encoding="utf-8")


def gen_pixabay(card_n: int, industry: str, out: Path) -> None:
    """Pixabay: 가입 후 https://pixabay.com/api/docs/ 에 키가 바로 표시됨. Content License (상업 이용 가능, 확인 필요).
    검색 결과가 엉뚱할 때가 있어(semiconductor wafer → 초콜릿 웨하스) 태그에 필수 단어가 있는 사진만 고르고, 그중 다운로드 수가 많은 것을 쓴다."""
    key = os.environ["PIXABAY_API_KEY"]
    # 같은 사진이 두 장에 들어가지 않게, 캐시에 이미 쓴 사진 id는 건너뛴다
    used = set()
    for j in (settings.out_dir / "images").glob("pixabay_*.json"):
        try:
            used.add(json.loads(j.read_text(encoding="utf-8")).get("id"))
        except Exception:
            pass
    tried = []
    for q, must in photo_queries(card_n, industry):
        r = requests.get("https://pixabay.com/api/", params={"key": key, "q": q, "image_type": "photo",
                         "orientation": "vertical", "per_page": 30, "safesearch": "true", "min_width": 1000, "order": "popular"}, timeout=30)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", [])
                if any(m in h.get("tags", "").lower() for m in must) and h.get("id") not in used]
        tried.append(f"{q}:{len(hits)}")
        if not hits:
            continue
        purpose = {0: "정보 카드 뒤에 흐리게 깔 배경(1장 전경과 다른 피사체)", 1: "업종 전경", 2: "핵심 제품·장비 클로즈업",
                   8: "위험 카드 배경(같은 업종의 다른 피사체)"}.get(card_n, "배경")
        pool = sorted(hits, key=lambda x: -x.get("downloads", 0))
        for _ in range(3):  # 태그로 고른 뒤, 실제 그림을 Claude 비전이 한 번 더 본다 (남의 상표·글자·낡은 느낌·무관한 피사체 탈락)
            if not pool:
                break
            h = pick_hit(industry, purpose, pool)
            if h is None:  # 태그만 보고 전부 무관하다고 하면, 다운로드 1위를 비전 판정에 넘겨 본다 (비전이 더 정확)
                tried[-1] += "(claude:none)"
                h = pool[0]
            img = requests.get(h["webformatURL"], timeout=60)  # 판정용 작은 그림
            img.raise_for_status()
            verdict = photo_ok(industry, purpose, img.content)
            if verdict is False:
                tried[-1] += f"(vision:no {h.get('id')})"
                pool = [x for x in pool if x.get("id") != h.get("id")]
                used.add(h.get("id"))
                continue
            big = requests.get(h["largeImageURL"], timeout=60)
            big.raise_for_status()
            out.write_bytes(big.content)
            out.with_suffix(".json").write_text(json.dumps(
                {"provider": "pixabay", "query": q, "tags": h.get("tags"), "user": h.get("user"), "url": h.get("pageURL"), "id": h.get("id"),
                 "vision": "ok" if verdict else "skipped"}, ensure_ascii=False), encoding="utf-8")
            return
    raise RuntimeError("pixabay 태그 일치 결과 없음: " + ", ".join(tried))


def photo_ok(industry: str, purpose: str, data: bytes) -> bool | None:
    """Claude 비전: 카드 배경으로 써도 되는 사진인가. 키 없으면 None(판정 생략).
    탈락 기준: 읽을 수 있는 회사명·브랜드 로고·글자(남의 상표가 카드에 실리면 안 됨), 사람 얼굴, 낡고 녹슨 느낌, 업종과 무관한 피사체."""
    if not settings.anthropic_api_key:
        return None
    import base64

    import anthropic
    from gongsi.summarize import MODEL

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(data).decode()}},
        {"type": "text", "text": f"이 사진을 '{industry}' 업종 공모주 카드뉴스의 {purpose}으로 써도 될까요?\n"
                                 "다음 중 하나라도 있으면 '아니오': 읽을 수 있는 회사명·브랜드 로고·제품명 글자, 사람 얼굴, 낡고 녹슬고 더러운 느낌, 업종과 상관없는 피사체(예: 반도체 부품에 옛날 라디오), 저화질.\n"
                                 "'예' 또는 '아니오'만 답하세요."}]}])
    ans = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return ans.startswith("예")


def gen_openverse(card_n: int, industry: str, out: Path) -> None:
    """Openverse (WordPress 재단): 키 없이 사용. CC 라이선스라 작가·라이선스 표기가 필요하므로 캡션에 넣어야 함."""
    q = photo_queries(card_n, industry)[0][0]
    r = requests.get("https://api.openverse.org/v1/images/", params={
        "q": q, "license_type": "commercial", "aspect_ratio": "tall", "page_size": 5,
        "extension": "jpg", "mature": "false"}, headers={"User-Agent": "gongsi-cards/0.1"}, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise RuntimeError(f"openverse 결과 없음: {q}")
    h = results[0]
    img = requests.get(h["url"], timeout=60, headers={"User-Agent": "gongsi-cards/0.1"})
    img.raise_for_status()
    out.write_bytes(img.content)
    out.with_suffix(".json").write_text(json.dumps(
        {"provider": "openverse", "query": q, "creator": h.get("creator"), "license": h.get("license"),
         "license_url": h.get("license_url"), "url": h.get("foreign_landing_url"), "attribution": h.get("attribution")},
        ensure_ascii=False), encoding="utf-8")


def gen_gemini(card_n: int, industry: str, out: Path) -> None:
    key = os.environ["GOOGLE_API_KEY"]
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    body = {"contents": [{"parts": [{"text": prompt_for(card_n, industry)}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
    r = requests.post(url, params={"key": key}, json=body, timeout=120)
    r.raise_for_status()
    for part in r.json()["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            out.write_bytes(base64.b64decode(part["inlineData"]["data"]))
            out.with_suffix(".json").write_text(json.dumps({"provider": "gemini", "prompt": prompt_for(card_n, industry)}, ensure_ascii=False), encoding="utf-8")
            return
    raise RuntimeError("gemini 응답에 이미지가 없음")


def gen_openai(card_n: int, industry: str, out: Path) -> None:
    key = os.environ["OPENAI_API_KEY"]
    r = requests.post("https://api.openai.com/v1/images/generations", headers={"Authorization": f"Bearer {key}"},
                      json={"model": "gpt-image-1", "prompt": prompt_for(card_n, industry), "size": "1024x1536", "n": 1}, timeout=180)
    r.raise_for_status()
    out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
    out.with_suffix(".json").write_text(json.dumps({"provider": "openai", "prompt": prompt_for(card_n, industry)}, ensure_ascii=False), encoding="utf-8")


def gen_procedural(card_n: int, industry: str, out: Path) -> None:
    """사진을 못 구했을 때의 폴백: 도형 없는 단색 그라데이션. 그래픽 일러스트는 쓰지 않는다."""
    out.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 800" width="1080" height="800">'
        '<defs><radialGradient id="g" cx="0.7" cy="0.3" r="0.7"><stop offset="0" stop-color="#1e3a8a"/>'
        '<stop offset="1" stop-color="#0F1115"/></radialGradient></defs><rect width="1080" height="800" fill="url(#g)"/></svg>',
        encoding="utf-8")


PROVIDERS = {
    "pexels": (gen_pexels, "jpg"),
    "pixabay": (gen_pixabay, "jpg"),
    "openverse": (gen_openverse, "jpg"),
    "gemini": (gen_gemini, "png"),
    "openai": (gen_openai, "png"),
    "none": (gen_procedural, "svg"),
}


# --- entry -------------------------------------------------------------------
def illustration(card_n: int, industry: str) -> tuple[Path, str]:
    """(파일 경로, 생성 경로 설명) 을 돌려줍니다. 제공자 목록을 순서대로 시도합니다."""
    chain = [p.strip().lower() for p in os.getenv("IMAGE_PROVIDER", "none").split(",") if p.strip()]
    if "none" not in chain:
        chain.append("none")
    notes = []
    for provider in chain:
        fn, ext = PROVIDERS[provider]
        path = _cache_path(card_n, industry, provider, ext)
        if provider != "none" and path.exists():
            return path, f"{provider}(cache)"
        try:
            fn(card_n, industry, path)
            return path, provider + (f" (이전 실패: {'; '.join(notes)})" if notes else "")
        except Exception as e:
            notes.append(f"{provider}: {type(e).__name__}: {str(e)[:80]}")
    raise RuntimeError("모든 이미지 제공자 실패: " + "; ".join(notes))
