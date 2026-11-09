"""정답표도 판정 모델도 쓰지 않는 지표. 만들어진 글과 실제로 읽은 자료만 본다.

두 종류로 나눈다.
  신호(SIGNALS) — 높낮이를 견준다. 각 신호가 어느 장치를 보는지 적어 둔다.
  경보(ALARMS)  — 0이어야 한다. 0이 아니면 그 보고서는 비교에서 뺀다.
"""
import re
from collections import Counter

CITE = re.compile(r"《(.+?)》")
ROLE_TAG = re.compile(r"\s*\([^()]*담당\)\s*$")

# 지표 이름 → (한 줄 설명, 보는 장치)
SIGNALS = {
    "근거율": ("인용이 붙은 문장 / 전체 문장", "조사 깊이 · 재위임 · 원고선택"),
    "편중": ("가장 많이 인용된 문서 하나의 인용 비율", "배정구역 · 선점"),
    "중복률": ("두 절 이상이 읽은 문서 / 읽은 문서", "배정구역 · 선점"),
    "읽고안씀": ("읽었지만 보고서에 한 번도 인용 안 된 문서 수", "배정(카드) 품질 · 질문이 나눌 만한가"),
    "최소절인용": ("인용이 가장 적은 절의 인용 수", "역할 · 원고선택"),
    "글자수": ("보고서 길이", "폭(절수) · 깊이(절예산)"),
}
ALARMS = {
    "허위인용": "아무도 읽지 않은 문서를 인용",
    "절밖인용": "그 절의 리서처가 읽지 않은 문서를 인용 — 다른 절이 읽었어도 이 절은 자기 지식으로 쓴 것",
    "인용0절": "인용이 하나도 없는 절 — 모델이 자기 지식으로 썼다는 신호",
    "누락절": "최종 원고가 보고서에 들어가지 않은 절",
}


def 문장들(report):
    """제목(#) 줄과 '(… 담당)' 꼬리표를 빼고 문장으로 나눈다.

    마침표 뒤에 붙은 인용(… 했다. 《X》)은 다음 문장 머리로 넘어가므로 앞 문장에 되돌려 붙인다.
    수업 교안에서 이 처리가 없어 근거율이 67.4%로 낮게 잡혔다(되돌리면 85.4%).
    """
    out = []
    for line in report.splitlines():
        line = ROLE_TAG.sub("", line.strip())
        if not line or line.startswith("#"):
            continue
        for piece in re.split(r"(?<=[.!?])\s+", line):
            lead = re.match(r"^((?:《[^》]+》\s*)+)", piece)
            if lead and out:
                out[-1] += " " + lead.group(1).strip()
                piece = piece[lead.end():]
            if piece.strip():
                out.append(piece.strip())
    return out


def compute(report, finals, all_drafts):
    """finals: 보고서에 들어간 절별 최종 원고, all_drafts: 버려진 원고까지 포함한 전부."""
    sents = 문장들(report)
    cites = CITE.findall(report)
    by_doc = Counter(cites)
    read_all = {t for d in all_drafts for t in d["read"]}      # 예산을 쓴 문서는 버려진 원고 것도 센다
    read_by = Counter(t for title in {d["section"]["title"] for d in all_drafts}
                      for t in {t for d in all_drafts if d["section"]["title"] == title
                                for t in d["read"]})
    per_section = [len(CITE.findall(d["text"])) for d in finals]
    return {
        # 신호
        "근거율": round(sum(bool(CITE.search(s)) for s in sents) / max(len(sents), 1), 3),
        "편중": round(max(by_doc.values()) / len(cites), 3) if cites else 0.0,
        "최다문서": by_doc.most_common(1)[0][0] if cites else "",
        "중복률": round(sum(v > 1 for v in read_by.values()) / max(len(read_by), 1), 3),
        "읽고안씀": len(read_all - set(cites)),
        "최소절인용": min(per_section) if per_section else 0,
        "글자수": len(report),
        "인용수": len(cites),
        "읽은문서": len(read_all),
        # 경보
        "허위인용": sorted(set(cites) - read_all),
        "절밖인용": [f"{d['section']['title']}: {t}" for d in finals
                    for t in sorted(set(CITE.findall(d["text"])) - set(d["read"]))],
        "인용0절": [d["section"]["title"] for d in finals if not CITE.search(d["text"])],
        "누락절": [d["section"]["title"] for d in finals
                  if d["text"] and d["text"][:40] not in report],
    }


def alarms(m):
    return {k: m[k] for k in ALARMS if m.get(k)}
