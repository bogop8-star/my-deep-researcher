"""코퍼스가 컨텍스트 윈도우를 넘는지 실측 토큰으로 확인하고, 링크 구조를 요약한다."""
import json
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parent.parent
WINDOW = 128_000  # gpt-4o-mini

corpus = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
enc = tiktoken.get_encoding("o200k_base")  # gpt-4o 계열 토크나이저
tokens = {d["title"]: len(enc.encode(d["text"])) for d in corpus}
total = sum(tokens.values())
chars = sum(len(d["text"]) for d in corpus)

inbound = {d["title"]: 0 for d in corpus}
for d in corpus:
    for l in d["links"]:
        inbound[l] += 1

print(f"문서 {len(corpus)}건 · {chars:,}자 · 실측 {total:,}토큰 → 윈도우({WINDOW:,})의 {total / WINDOW:.1f}배")
print(f"문서당 평균 {total // len(corpus):,}토큰 · 가장 긴 문서 {max(tokens, key=tokens.get)} "
      f"{max(tokens.values()):,}토큰 · 내부 링크 {sum(len(d['links']) for d in corpus)}개")
print("들어오는 링크 상위:", sorted(inbound.items(), key=lambda x: -x[1])[:6])
print("나가는 링크 0개:", [d["title"] for d in corpus if not d["links"]])
print("들어오는 링크 0개(배정이 유일한 경로):", [t for t, n in inbound.items() if n == 0])
