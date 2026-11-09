"""딥리서처 데모 — streamlit run app.py

보고서만이 아니라 '절마다 누가 무엇을 읽고 무엇을 썼는지'를 보여 준다.
저장된 실행(output/runs.jsonl)은 API 키 없이도 열어 볼 수 있다.
"""
import json
import re

import streamlit as st

import graph
import metrics

st.set_page_config(page_title="몰락한 기업 딥리서처", page_icon="📉", layout="wide")

SWITCH_HELP = {
    "배정구역": "코디네이터가 절마다 시작 문서를 배정하고, 다른 절의 구역을 알려 준다",
    "역할": "리서처마다 다른 관점(기술전환·경영판단·경쟁구도…)을 준다",
    "재위임": "'부족'을 신고한 절만 한 바퀴 더 보낸다",
    "선점": "같은 바퀴에 동시에 출발한 리서처끼리 같은 문서를 고르지 않게 한다",
    "원고선택": "두 번째 원고가 인용이 더 충실할 때만 첫 원고를 대체한다",
}


def load_saved():
    p = graph.ROOT / "output" / "runs.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------- 사이드바: 질문과 스위치 ----------------
st.sidebar.title("📉 몰락한 기업 딥리서처")
mode = st.sidebar.radio("모드", ["새로 질문하기", "저장된 실행 보기 (키 불필요)"])

if mode == "새로 질문하기":
    qs = {f"{q['id']} [{q['유형']}] {q['question']}": q for q in graph.QUESTIONS}
    pick = st.sidebar.selectbox("질문 세트", ["직접 입력"] + list(qs))
    if pick == "직접 입력":
        question = st.sidebar.text_area("질문", "싸이월드는 왜 페이스북에 밀려났는가?")
    else:
        question = qs[pick]["question"]
        st.sidebar.caption(f"왜 나눌 만한가: {qs[pick]['왜_나눌_만한가']}")
    st.sidebar.subheader("폭 × 깊이")
    n_sec = st.sidebar.slider("절수 (리서처 수)", 2, 5, graph.CONFIG["절수"])
    per = st.sidebar.slider("절당 평균 예산 (문서 수)", 2, 4, graph.CONFIG["절예산"])
    st.sidebar.subheader("장치 스위치")
    switches = {k: st.sidebar.checkbox(k, v, help=SWITCH_HELP[k])
                for k, v in graph.CONFIG["스위치"].items()}
    go = st.sidebar.button("🔎 리서치 시작", type="primary", use_container_width=True)
else:
    saved = load_saved()
    if not saved:
        st.info("저장된 실행이 없습니다. `python ablation.py --summarize` 를 먼저 실행하세요.")
        st.stop()
    labels = [f"{i}. {r['qid']} · {r['label']} · {r['question'][:30]}…" for i, r in enumerate(saved)]
    sel = st.sidebar.selectbox("실행 기록", labels, index=len(labels) - 1)
    st.session_state["result"] = saved[labels.index(sel)]
    go = False


# ---------------- 실행: 노드별 진행을 실시간으로 ----------------
if go:
    st.session_state.pop("result", None)
    with st.status("코디네이터가 목차를 짜는 중…", expanded=True) as status:
        result = None
        for node, upd in graph.stream_run(question, 절수=n_sec, 절예산=per, **switches):
            if node == "plan":
                st.markdown(f"**① 기획** 《{upd['report_title']}》")
                for s in upd["sections"]:
                    st.markdown(f"- {s['title']} — *{s['role']}* · 시작 《{s['start'] or '리서처가 고름'}》 · 예산 {s['budget']}")
                for n in upd.get("plan_notes", []):
                    st.warning(n)
                status.update(label="리서처들이 동시에 문서를 읽는 중…")
            elif node == "researcher":
                d = upd["drafts"][0]
                st.markdown(f"**{d['round']}바퀴 · {d['section']['title']}** — "
                            f"{' → '.join(d['new_read']) or '새 문서 없음'} · {len(d['text'])}자 · "
                            f"인용 {len(metrics.CITE.findall(d['text']))} · {d['verdict']}")
            elif node == "review":
                if upd.get("retry"):
                    st.markdown(f"**③ 점검** → {len(upd['retry'])}절 재위임")
                    status.update(label="부족한 절을 다시 조사하는 중…")
                else:
                    st.markdown(f"**③ 점검** → 종료 ({upd.get('종료')})")
            elif node == "synthesize":
                status.update(label="보고서를 이어 붙이는 중…")
            elif node == "done":
                result = upd
        status.update(label="완료", state="complete", expanded=False)
    if result:
        rec = graph.save_run(result, "데모")
        st.session_state["result"] = rec

r = st.session_state.get("result")
if not r:
    st.title("📉 몰락한 기업 딥리서처")
    st.markdown("한때 1위였던 기업들이 왜 무너졌는지, 영어 위키백과 48건을 리서처 여러 명이 나눠 읽고 "
                "한국어 장문 보고서로 정리합니다. 왼쪽에서 질문을 고르고 **리서치 시작**을 누르세요.")
    st.stop()

# 저장된 기록과 방금 실행한 결과를 같은 모양으로 다룬다
finals_titles = r["finals"] if isinstance(r["finals"][0], str) else [d["section"]["title"] for d in r["finals"]]
m = r["metrics"]
al = metrics.alarms(m)

st.title(r["question"])
c = st.columns(6)
c[0].metric("보고서", f"{m['글자수']:,}자")
c[1].metric("근거율", f"{m['근거율']:.0%}")
c[2].metric("편중", f"{m['편중']:.0%}", help=f"최다 인용: {m.get('최다문서')}")
c[3].metric("읽은 문서", m["읽은문서"])
c[4].metric("LLM 호출", m.get("호출"))
c[5].metric("격리율", f"{m['격리율']:.1%}" if m.get("격리율") is not None else "단일")
if al:
    st.error(f"⚠ 경보: {al} — 이 보고서는 비교에서 제외 대상입니다.")

tab_report, tab_team, tab_cite, tab_metric = st.tabs(
    ["📄 보고서", "👥 누가 무엇을 읽고 썼나", "🔍 인용 대조", "📊 지표"])

# ---------------- 보고서 ----------------
with tab_report:
    st.markdown(re.sub(r"《(.+?)》", r" `\1`", r["report"]))

# ---------------- 분업: 절마다 읽은 경로와 원고 ----------------
with tab_team:
    for s in r["sections"]:
        drafts = [d for d in r["drafts"] if d["section"]["title"] == s["title"]]
        with st.expander(f"**{s['title']}** — {s.get('role', '')} · 시작 《{s.get('start') or '스스로 고름'}》 "
                         f"· 예산 {s.get('budget', '-')} · 원고 {len(drafts)}개", expanded=False):
            for d in drafts:
                kept = s["title"] in finals_titles and d is max(
                    drafts, key=lambda x: (x["text"][:40] in r["report"], x["round"]))
                st.markdown(f"#### {d['round']}바퀴 {'✅ 보고서에 들어감' if kept else '🗑 버려짐'}"
                            f"{' (새 문서 없어 지난 원고 유지)' if d.get('kept_prev') else ''}")
                left, right = st.columns([2, 3])
                with left:
                    st.caption("탐색 경로")
                    for t in d.get("trace", []):
                        if t["type"] == "pick":
                            st.markdown(f"➡️ **고름** 《{t['doc']}》 (후보 {t['from']}개) — {t['reason']}")
                        elif t["type"] == "stop":
                            st.markdown(f"⏹ **멈춤** — {t['reason']}")
                        else:
                            mark = "📖" if t["relevant"] else "🚫 관련 없음"
                            with st.popover(f"{mark} 읽음 《{t['doc']}》 {t['chars']:,}자"):
                                st.write(t["summary"])
                with right:
                    st.caption(f"원고 {len(d['text'])}자 · 인용 {len(metrics.CITE.findall(d['text']))} · "
                               f"자기신고 {d['verdict']}" + (f" — {d['missing']}" if d.get("missing") else ""))
                    st.markdown(re.sub(r"《(.+?)》", r" `\1`", d["text"]))

# ---------------- 인용 대조: 문장 ↔ 리서처가 그 문서에서 본 요약 ----------------
with tab_cite:
    st.caption("읽지 않은 문서 인용은 코드가 잡지만, 읽은 문서를 엉뚱한 문장에 붙인 오귀속은 사람만 잡습니다. "
               "문장과, 리서처가 그 문서에서 실제로 뽑은 요약을 나란히 보세요.")
    seen = {}
    for d in r["drafts"]:
        for t in d.get("trace", []):
            if t["type"] == "read":
                seen.setdefault(t["doc"], t["summary"])
    sents = [s for s in metrics.문장들(r["report"]) if metrics.CITE.search(s)]
    doc_filter = st.selectbox("문서로 거르기", ["전체"] + sorted({t for s in sents for t in metrics.CITE.findall(s)}))
    for s in sents:
        docs = metrics.CITE.findall(s)
        if doc_filter != "전체" and doc_filter not in docs:
            continue
        a, b = st.columns(2)
        a.markdown(metrics.CITE.sub("", s))
        for t in dict.fromkeys(docs):
            b.markdown(f"**《{t}》** {seen.get(t, '(요약 기록 없음)')[:300]}")
        st.divider()

# ---------------- 지표: 신호와 경보, 격리 ----------------
with tab_metric:
    st.subheader("신호 — 높낮이를 견준다")
    st.table([{"지표": k, "값": (f"{m[k]:.1%}" if isinstance(m[k], float) else m[k]),
               "뜻": desc, "보는 장치": dev} for k, (desc, dev) in metrics.SIGNALS.items()])
    st.subheader("경보 — 0이어야 한다")
    st.table([{"경보": k, "값": m.get(k) or 0, "뜻": desc} for k, desc in metrics.ALARMS.items()])
    if m.get("코디네이터_입력자"):
        st.subheader("격리 — 누가 몇 자를 읽었나")
        st.bar_chart({"코디네이터": m["코디네이터_입력자"], "서브에이전트": m["서브에이전트_입력자"]})
        st.caption(f"코디네이터가 본 것은 문서 카드·자기신고·절 첫머리뿐입니다. "
                   f"격리율 {m['격리율']:.1%} = 코디네이터 입력 ÷ 전체 입력.")
    with st.expander("파이프라인 구조 (Mermaid)"):
        st.code(graph.GRAPH.get_graph().draw_mermaid(), language="mermaid")
