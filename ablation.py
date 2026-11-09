"""스위치를 하나씩 끄고 같은 지표로 재는 실험.

  python ablation.py --qid Q5 --repeats 2     # 한 질문의 모든 설정을 실행 → output/ablation/Q5.jsonl
  python ablation.py --summarize              # 질문별 결과를 모아 output/runs.jsonl · output/ablation.json

같은 설정도 시행마다 십몇 %p씩 움직이므로 반복(--repeats)한 평균과 범위를 함께 본다.
질문마다 프로세스를 따로 띄우면 병렬로 돌릴 수 있다(설정이 프로세스 전역이라 한 프로세스 안에서는 순차).
"""
import argparse
import json
import statistics

import graph
import metrics
from baseline import solo

OUT = graph.ROOT / "output"
CONFIGS = {                          # 한 번에 하나씩만 끈다
    "기본": {},
    "배정구역 끔": {"배정구역": False},
    "역할 끔": {"역할": False},
    "재위임 끔": {"재위임": False},
    "선점 끔": {"선점": False},
    "원고선택 끔": {"원고선택": False},
    "단일 에이전트": None,
}
SHOW = ["근거율", "편중", "중복률", "읽고안씀", "최소절인용", "글자수", "호출", "격리율", "시간"]


def run_question(qid, repeats, only=None):
    q = next(x for x in graph.QUESTIONS if x["id"] == qid)["question"]
    path = OUT / "ablation" / f"{qid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, repeats + 1):
        for label, override in CONFIGS.items():
            if only and label not in only:
                continue
            res = solo(q) if override is None else graph.run(q, **override)
            # 스위치가 실제로 꺼졌는지 기록에서 확인할 수 있게 실행 당시 설정을 같이 남긴다
            rec = graph.save_run(res, label, qid, path=path)
            (OUT / "reports" / f"{qid}_{label.replace(' ', '')}_{i}.md").write_text(
                res["report"], encoding="utf-8")
            m = rec["metrics"]
            print(f"{qid} #{i} {label:<8} " + " · ".join(
                f"{k} {m[k]:.1%}" if isinstance(m.get(k), float) and k != "시간" else f"{k} {m.get(k)}"
                for k in SHOW) + (f" · ⚠ {metrics.alarms(m)}" if metrics.alarms(m) else ""), flush=True)


def summarize():
    recs = []
    for p in sorted((OUT / "ablation").glob("*.jsonl")):
        recs += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    with (OUT / "runs.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    table = {}
    for r in recs:
        # 지표는 저장값이 아니라 지금의 metrics.compute로 다시 계산한다 — 문장 분리 규칙이 바뀌면 전부 같이 바뀐다
        finals = [d for d in r["drafts"] if d["section"]["title"] in r["finals"]]
        finals = list({d["section"]["title"]: d for d in finals}.values())
        m = metrics.compute(r["report"], finals, r["drafts"]) | \
            {k: r["metrics"].get(k) for k in ("호출", "격리율", "시간")}
        cell = table.setdefault(r["qid"], {}).setdefault(r["label"], {"runs": [], "경보": []})
        if metrics.alarms(m):
            cell["경보"].append(metrics.alarms(m))      # 경보가 난 보고서는 평균에서 뺀다
        else:
            cell["runs"].append(m)

    out = {}
    for qid, by_label in table.items():
        print(f"\n== {qid}: {next(x for x in graph.QUESTIONS if x['id'] == qid)['question']}")
        print(f"{'설정':<10}" + "".join(f"{k:>16}" for k in SHOW) + "  n / 경보")
        for label in CONFIGS:
            cell = by_label.get(label)
            if not cell:
                continue
            agg = {}
            for k in SHOW:
                vals = [m[k] for m in cell["runs"] if isinstance(m.get(k), (int, float))]
                if vals:
                    agg[k] = {"mean": round(statistics.mean(vals), 3), "min": min(vals), "max": max(vals)}
            out.setdefault(qid, {})[label] = {"n": len(cell["runs"]), "경보": cell["경보"], **agg}

            def fmt(k):
                a = agg.get(k)
                if not a:
                    return f"{'-':>16}"
                pct = k in ("근거율", "편중", "중복률", "격리율")
                f = (lambda v: f"{v:.0%}") if pct else (lambda v: f"{v:.0f}")
                return f"{f(a['mean']) + ' (' + f(a['min']) + '~' + f(a['max']) + ')':>16}"
            print(f"{label:<10}" + "".join(fmt(k) for k in SHOW) + f"  {len(cell['runs'])} / {len(cell['경보'])}")
    (OUT / "ablation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ output/runs.jsonl ({len(recs)}건) · output/ablation.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qid")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--only", nargs="*", help="이 설정들만 실행 (예: --only 기본 \"역할 끔\")")
    ap.add_argument("--summarize", action="store_true")
    a = ap.parse_args()
    summarize() if a.summarize else run_question(a.qid, a.repeats, a.only)
