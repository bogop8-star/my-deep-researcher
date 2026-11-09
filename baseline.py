"""단일 에이전트 대조군 — 팀과 같은 자료·모델·도구·읽기 예산. 나누지만 않는다.

공정성 점검:
  - 같은 모델·온도: graph.llm 을 그대로 쓴다.
  - 같은 도구: graph.read_one / 후보목록 / pick_next 를 그대로 쓴다.
  - 같은 읽기 예산: 절수 × 절예산 (기본 12건). allow_stop=False 로 끝까지 읽게 하고,
    다 쓰지 못하면 '예산미달'을 기록한다 — 중간에 멈춘 상대를 이기면 실험이 아니다.
  - 같은 카드 길이·같은 지표(metrics.compute).
"""
import argparse
import time
import uuid

import graph
import metrics

SOLO_PROMPT = """아래 읽기 노트만 근거로 질문에 답하는 한국어 리서치 보고서를 쓰세요.

[질문] {question}
[규칙]
- "# 제목" 한 줄, 머리말, "## 1. …" 형식의 절 {n}개, "## 맺음말" 순서. 2,000자 이상.
- **모든 문장**은 마침표 바로 앞에 근거 문서를 《문서명》으로 붙입니다. 문서명은 노트 제목(영문) 그대로.
  예) 노키아는 2007년 세계 휴대폰 시장의 40%를 차지했다《Nokia》.
- 노트에 없는 내용은 쓰지 않습니다.

[읽기 노트]
{notes}"""


def solo(question, **override):
    old = dict(graph.설정)
    graph.설정.update(override)
    rid = "solo-" + uuid.uuid4().hex[:6]
    t = time.time()
    try:
        budget = graph.설정["절수"] * graph.설정["절예산"]
        task = {"title": question, "role": "범용"}
        read, notes, trace, read_chars = [], [], [], 0
        for _ in range(budget):
            cands = graph.후보목록(read, [])
            if not cands:
                break
            title, why = graph.pick_next(rid, question, task, notes, cands, allow_stop=False)
            summary, n = graph.read_one(rid, title, question, task)
            read.append(title)
            read_chars += n
            relevant = summary != graph.NOT_RELEVANT
            if relevant:
                notes.append((title, summary))
            trace += [{"type": "pick", "doc": title, "reason": why, "from": len(cands)},
                      {"type": "read", "doc": title, "chars": n, "relevant": relevant,
                       "summary": summary[:400]}]
        report = graph.ask(rid, "sub", SOLO_PROMPT.format(
            question=question, n=graph.설정["절수"],
            notes="\n\n".join(f"《{t}》\n{s}" for t, s in notes) or "(관련 문서 없음)"))
        report, _ = graph.canon_cites(report, read)     # 팀과 같은 인용명 교정
        draft = {"section": {"title": "전체", "role": "단일"}, "round": 1, "text": report,
                 "read": read, "new_read": read, "read_chars": read_chars, "verdict": "",
                 "missing": "", "trace": trace}
        led = graph._ledger(rid)
        m = metrics.compute(report, [draft], [draft])
        m |= {"호출": led["calls"], "시간": round(time.time() - t), "예산": budget,
              "예산사용": len(read), "예산미달": len(read) < budget, "격리율": None}
        print(f"단일 에이전트: {len(read)}/{budget}건 읽음 · {len(report):,}자 · 인용 {m['인용수']} · "
              f"근거율 {m['근거율']:.1%} · 호출 {m['호출']}" + (" · ⚠ 예산미달" if m["예산미달"] else ""))
        return {"question": question, "report": report, "drafts": [draft], "finals": [draft],
                "sections": [draft["section"]], "metrics": m, "종료": "",
                "설정": dict(graph.설정) | {"구조": "단일"}}
    finally:
        graph.설정.clear()
        graph.설정.update(old)
        graph.RUNS.pop(rid, None)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qid", default="Q5")
    args = ap.parse_args()
    q = next(x for x in graph.QUESTIONS if x["id"] == args.qid)["question"]
    res = solo(q)
    graph.save_run(res, "단일", args.qid)
    (graph.ROOT / "output" / "reports" / f"{args.qid}_단일.md").write_text(res["report"], encoding="utf-8")
