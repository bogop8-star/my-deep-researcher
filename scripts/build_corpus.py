"""영어 위키백과에서 '몰락한 기업들' 코퍼스를 받아 data/corpus.json으로 저장한다.

각 문서: {"title", "group", "text", "links"} — links는 코퍼스 안의 다른 문서로 가는 내부 링크만 남긴다.
group은 분석용 라벨일 뿐 에이전트에게 정답으로 주지 않는다.
"""
import json
import time
from pathlib import Path

import requests

SEEDS = {
    "fallen": [
        "Nokia", "Kodak", "Blockbuster (retailer)", "BlackBerry Limited", "Yahoo", "Myspace",
        "Netscape", "Polaroid Corporation", "Sears", "Toys \"R\" Us", "Enron", "Lehman Brothers",
        "RadioShack", "Borders (retailer)", "Circuit City", "Palm, Inc.", "Compaq",
        "Sun Microsystems", "Atari, Inc.", "Motorola", "WeWork", "Theranos", "FTX", "Quibi",
        "Friendster", "Vine (service)", "Cyworld", "Pets.com", "Webvan", "AltaVista", "Xerox",
    ],
    "winner": [
        "Netflix", "Apple Inc.", "IPhone", "Amazon (company)", "Android (operating system)",
        "Microsoft", "Fujifilm", "Google",
    ],
    "concept": [
        "Disruptive innovation", "The Innovator's Dilemma", "Clayton Christensen",
        "Dot-com bubble", "Browser wars", "Digital camera", "Symbian", "Windows Phone",
        "PARC (company)",
    ],
}
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "aiffel-deep-research-lab/0.1 (educational)"}
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "corpus.json"
CACHE = ROOT / "data" / ".wiki_cache"


def api(**params):
    params |= {"format": "json", "formatversion": 2}
    for attempt in range(6):
        r = requests.get(API, params=params, headers=HEADERS, timeout=60)
        if r.ok:
            time.sleep(1)  # 위키백과 요청 속도 제한(429) 회피
            return r.json()
        wait = int(r.headers.get("Retry-After", 0)) or 5 * (attempt + 1)
        print(f"    {r.status_code} → {wait}초 대기 후 재시도")
        time.sleep(wait)
    r.raise_for_status()


def fetch(title):
    """본문(평문), 모든 내부 링크, 이 문서로 오는 넘겨주기(별칭) 목록."""
    page = api(action="query", titles=title, redirects=1, prop="extracts|redirects",
               explaintext=1, exsectionformat="plain", rdlimit="max")["query"]["pages"][0]
    links, cont = [], {}
    while True:
        q = api(action="query", titles=page["title"], prop="links",
                plnamespace=0, pllimit="max", **cont)
        links += [l["title"] for l in q["query"]["pages"][0].get("links", [])]
        if "continue" not in q:
            break
        cont = q["continue"]
    aliases = [r["title"] for r in page.get("redirects", [])]
    return page["title"], page.get("extract", ""), links, aliases


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    raw, group_of = {}, {}
    for group, titles in SEEDS.items():
        for t in titles:
            cache = CACHE / f"{t.replace('/', '_').replace(chr(34), '')}.json"
            if cache.exists():
                title, text, links, aliases = json.loads(cache.read_text(encoding="utf-8"))
            else:
                title, text, links, aliases = fetch(t)
                cache.write_text(json.dumps([title, text, links, aliases], ensure_ascii=False),
                                 encoding="utf-8")
            raw[title] = (text, links, aliases)
            group_of[title] = group
            print(f"  [{group:<7}] {title:<28} {len(text):>8,}자  링크 {len(links)}", flush=True)

    alias = {}
    for title, (_, _, aliases) in raw.items():
        alias[title] = title
        for a in aliases:
            alias[a] = title

    corpus = []
    for title, (text, links, _) in raw.items():
        inner = sorted({alias[l] for l in links if l in alias and alias[l] != title})
        corpus.append({"title": title, "group": group_of[title], "text": text, "links": inner})
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")

    chars = sum(len(d["text"]) for d in corpus)
    n_links = sum(len(d["links"]) for d in corpus)
    inbound = {d["title"]: 0 for d in corpus}
    for d in corpus:
        for l in d["links"]:
            inbound[l] += 1
    est_tokens = chars // 4  # 영어는 대략 4자 = 1토큰
    print(f"\n문서 {len(corpus)}건 · {chars:,}자 · 추정 {est_tokens:,}토큰 "
          f"(gpt-4o-mini 윈도우 128,000토큰의 {est_tokens / 128_000:.1f}배) · 내부 링크 {n_links}개")
    print("나가는 링크 0개:", [d["title"] for d in corpus if not d["links"]] or "없음")
    print("들어오는 링크 0개(탐색으로 못 닿음, 배정이 유일한 경로):",
          [t for t, n in inbound.items() if n == 0] or "없음")


if __name__ == "__main__":
    main()
