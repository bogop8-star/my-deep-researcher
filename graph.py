"""딥리서처 — 코디네이터 + 서브에이전트(리서처) 팬아웃 그래프.

  ① plan ─▶ ② dispatch ─Send×N─▶ researcher ─▶ ③ review ─(부족 & 바퀴 남음)─▶ ② dispatch
                                                     └─(충분 / 상한)─▶ ④ synthesize ─▶ ⑤ evaluate

원문은 researcher 안의 read_one()에서만 쓰이고, 위로는 원고·자기신고만 올라간다.
도메인에 묶인 값(절수·예산·역할 명단)은 config.json에 있다.
"""
import argparse
import json
import operator
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

import metrics

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CORPUS = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
QUESTIONS = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
DOCS = {d["title"]: d for d in CORPUS}
ROSTER = CONFIG["역할"]


def default_settings():
    return {k: CONFIG[k] for k in ("절수", "절예산", "절예산_범위", "카드길이", "최대바퀴")} \
        | dict(CONFIG["스위치"])


# 실행 중 설정 — run()이 잠깐 바꾸고 끝나면 되돌린다. 끄는 스위치는 모두 여기 있다.
설정 = default_settings()

llm = ChatOpenAI(model=CONFIG["model"], temperature=CONFIG["temperature"])

# ---------- 실행별 장부: 호출 수 · 누가 몇 자를 읽었나 · 이번 실행에서 선점된 문서 ----------
RUNS = {}


def _ledger(run_id):
    return RUNS.setdefault(run_id, {"lock": threading.Lock(), "calls": 0, "coord_chars": 0,
                                    "sub_chars": 0, "claimed": {}})


def ask(run_id, who, prompt, schema=None):
    """모든 LLM 호출은 여기를 지난다. who='coord'|'sub'로 격리를 숫자로 센다."""
    led = _ledger(run_id)
    with led["lock"]:
        led["calls"] += 1
        led[f"{who}_chars"] += len(prompt)
    if schema:
        return llm.with_structured_output(schema).invoke(prompt)
    return llm.invoke(prompt).content.strip()


def role_desc(task):
    if not 설정["역할"]:
        return CONFIG["범용역할"]
    return f"{task['role']} 담당: {ROSTER.get(task['role'], CONFIG['범용역할'])}"


def card(title):
    return DOCS[title]["text"][:설정["카드길이"]].replace("\n", " ")


class State(TypedDict, total=False):
    run_id: str
    question: str
    report_title: str
    sections: list                               # ① 목차: [{"title", "role", "start", "budget"}]
    plan_notes: list                             # 코드가 바로잡은 것들(배정 검사 결과)
    drafts: Annotated[list, operator.add]        # 리서처 원고가 쌓인다(리듀서) — 버려진 원고 포함
    round: int
    retry: list                                  # ③ 다시 보낼 절: [{"section", "prev"}]
    종료: str
    report: str
    finals: list                                 # 보고서에 들어간 절별 원고
    metrics: dict


# ---------- 원고 고르기: 같은 절의 두 번째 원고를 어떻게 할까 ----------

def _score(d):
    fake = set(metrics.CITE.findall(d["text"])) - set(d["read"])
    return (not fake, len(metrics.CITE.findall(d["text"])))


def pick_finals(drafts, sections):
    """원고선택 켬: 허위 인용이 없고 인용이 더 많은 원고를 남긴다 (같으면 새 원고).
    원고선택 끔: 수업 교안처럼 마지막 원고가 무조건 덮어쓴다."""
    best = {}
    for d in drafts:
        t = d["section"]["title"]
        if t not in best or not 설정["원고선택"] or _score(d) >= _score(best[t]):
            best[t] = d
    return [best[s["title"]] for s in sections if s["title"] in best]


# ---------- ① 기획 ----------

# 문서 제목은 enum으로 묶지 않는다(《Toys "R" Us》의 따옴표를 OpenAI 구조화 출력이 거부한다).
# 대신 모델이 쓴 제목을 코드가 실제 문서로 맞춰 본다 — 못 맞추면 지어낸 제목이다.
DocTitle = str
Role = Literal[tuple(ROSTER)]
_NORM = {re.sub(r"[^a-z0-9]", "", t.lower()): t for t in DOCS}


def resolve(name):
    """모델이 쓴 문서 이름 → 실제 문서 제목. 없으면 None."""
    name = (name or "").strip().strip("《》\"' ")
    if name in DOCS:
        return name
    return _NORM.get(re.sub(r"[^a-z0-9]", "", name.lower()))


class Section(BaseModel):
    title: str = Field(description="절 제목(한국어 소제목). 기업·사건·원인 갈래 같은 내용 단위")
    role: Role = Field(description="담당 리서처 역할")
    start: DocTitle = Field(description="이 절의 리서처가 처음 읽을 문서")
    backup: list[DocTitle] = Field(description="start가 다른 절과 겹칠 때 대신 쓸 문서 2개")
    budget: int = Field(description="이 절에서 읽을 문서 수")


class Outline(BaseModel):
    report_title: str = Field(description="보고서 제목(한국어)")
    sections: list[Section]


PLAN_PROMPT = """당신은 리서치 팀의 코디네이터입니다. 질문에 답하는 한국어 보고서의 목차를 짜고,
절마다 리서처 한 명에게 역할·시작 문서·읽기 예산을 배정합니다.

[질문]
{question}

[목차 규칙]
- 절은 정확히 {n}개. **내용 단위**로 나눕니다(예: 기업별, 원인 갈래별, 시기별 사건).
- "개요 / 배경 / 분석 / 결론 / 연표"처럼 **형식 단위로 나누지 않습니다.** 그러면 모든 리서처가 같은 문서를 읽습니다.
- 절끼리 다루는 대상이 겹치지 않게 합니다.

[배정 규칙]
- 역할: {roles}
- 시작 문서는 아래 카드의 제목 중에서만, 절마다 서로 다르게 고릅니다. 카드 내용을 보고 고르며 제목만 보고 고르지 않습니다.
- 예산은 절마다 {lo}~{hi}건, 합계는 정확히 {total}건. 자료가 흩어진 절에 더 줍니다.

[문서 카드]
{cards}"""

FORMAT_WORDS = re.compile(r"개요|배경|결론|요약|연표|분석|서론|소개")


def _balance(budgets, total, lo, hi):
    """코디네이터가 준 예산을 범위 안으로 자르고 합계를 total에 맞춘다 — 대조군과 같은 총예산."""
    b = [min(max(x, lo), hi) for x in budgets]
    i = 0
    while sum(b) != total and i < 100:
        j = i % len(b)
        if sum(b) < total and b[j] < hi:
            b[j] += 1
        elif sum(b) > total and b[j] > lo:
            b[j] -= 1
        i += 1
    return b


def plan(state: State):
    rid = state["run_id"]
    cards = "\n".join(f"《{t}》 {card(t)}" for t in DOCS)
    lo, hi = 설정["절예산_범위"]
    total = 설정["절수"] * 설정["절예산"]
    outline = ask(rid, "coord", PLAN_PROMPT.format(
        question=state["question"], n=설정["절수"], roles=", ".join(ROSTER), cards=cards,
        lo=lo, hi=hi, total=total), Outline)

    # 배정 검사: 실제 문서인지, 겹치지 않는지 — 모델은 그럴듯한 제목을 지어낸다
    raw = outline.sections[:설정["절수"]]
    budgets = _balance([s.budget for s in raw], total, lo, hi) if 설정["배정구역"] \
        else [설정["절예산"]] * len(raw)
    used, sections, notes = set(), [], []
    for s, b in zip(raw, budgets):
        if resolve(s.start) is None:
            notes.append(f"지어낸 문서 이름 《{s.start}》")
        cands = [t for t in map(resolve, [s.start, *s.backup]) if t and t not in used]
        start = cands[0] if cands else next(t for t in DOCS if t not in used)
        if start != resolve(s.start):
            notes.append(f"시작 문서 교정 《{s.start}》→《{start}》")
        if FORMAT_WORDS.search(s.title):
            notes.append(f"형식 단위 의심: '{s.title}'")
        used.add(start)
        sections.append({"title": s.title, "role": s.role, "budget": b,
                         "start": start if 설정["배정구역"] else None})
    if len(raw) < 설정["절수"]:
        notes.append(f"절 수 부족: {len(raw)}/{설정['절수']}")

    print(f"① 기획: 《{outline.report_title}》 절 {len(sections)}개")
    for i, s in enumerate(sections, 1):
        print(f"    {i}. {s['title']} — {s['role']} · 시작 《{s['start'] or '리서처가 고름'}》 · 예산 {s['budget']}")
    for n in notes:
        print(f"    ⚠ {n}")
    return {"sections": sections, "round": 1, "report_title": outline.report_title,
            "plan_notes": notes}


# ---------- ② 배치 ----------

def 남의구역(state: State, me: dict) -> list:
    """다른 절의 시작 문서 + 다른 절이 이미 읽은 문서. 배정구역을 끄면 빈 목록."""
    if not 설정["배정구역"]:
        return []
    avoid = {s["start"] for s in state["sections"] if s["title"] != me["title"] and s["start"]}
    avoid |= {t for d in state.get("drafts", []) if d["section"]["title"] != me["title"]
              for t in d["read"]}
    return sorted(avoid)


def _jobs(state: State):
    if state["round"] == 1:
        return [(s, None) for s in state["sections"]]
    return [(r["section"], r["prev"]) for r in state["retry"]]


def dispatch(state: State):
    print(f"② 배치: {state['round']}바퀴 · 서브에이전트 {len(_jobs(state))}개 동시 파견")
    return {}


def fan_out(state: State):
    return [Send("researcher", {"run_id": state["run_id"], "question": state["question"],
                                "task": s, "prev": prev, "round": state["round"],
                                "avoid": 남의구역(state, s)})
            for s, prev in _jobs(state)]


# ---------- 서브에이전트 (노드 안의 에이전트) ----------

NOT_RELEVANT = "관련 없음"

READ_PROMPT = """다음 영어 문서를 읽고, 아래 절을 쓰는 데 필요한 사실만 한국어로 요약하세요.

[전체 질문] {question}
[내가 맡은 절] {section} ({role})
{hint}
- 연도·인물·결정·숫자·원인·결과 등 구체적 사실 위주로 8문장 이내.
- 이 절의 대상과 직접 관련된 사실뿐 아니라 경쟁 제품·경쟁사·시장 변화처럼 절을 설명하는 데 쓸 수 있는 사실도 담습니다.
- 문서에 없는 내용은 쓰지 않습니다.
- 그런 사실이 정말 하나도 없을 때만 다른 말 없이 "{nr}"이라고만 답하세요.

[문서 《{title}》]
{text}"""


def read_one(rid, title, question, task, hint=""):
    """격리의 경계: 원문(수만 자)은 여기서만 쓰이고 밖으로는 요약만 나간다."""
    text = DOCS[title]["text"]
    summary = ask(rid, "sub", READ_PROMPT.format(
        question=question, section=task["title"], role=role_desc(task),
        hint=f"[이번에 특히 찾을 것] {hint}\n" if hint else "", nr=NOT_RELEVANT,
        title=title, text=text))
    return summary, len(text)


def 후보목록(read, avoid):
    """읽은 문서의 링크 중 안 읽었고 남의 구역도 아닌 문서. 막히면(예: 《Theranos》) 코퍼스 전체로 넓힌다."""
    blocked = set(read) | set(avoid)
    linked = {t for r in read for t in DOCS[r]["links"]} - blocked
    return sorted(linked) or sorted(set(DOCS) - blocked)


def canon_cites(text, read):
    """인용 이름을 실제로 읽은 문서 제목으로 맞춘다. 《Blockbuster》 → 《Blockbuster (retailer)》.
    읽은 문서 중 하나로만 맞춰지는 경우에만 고치고, 그 외(진짜 허위 인용)는 그대로 둬서 경보에 걸리게 한다."""
    plain = {}
    for t in read:
        plain.setdefault(re.sub(r"\s*\(.*?\)$", "", t).lower(), []).append(t)
    fixed = 0

    def fix(m):
        nonlocal fixed
        name = m.group(1)
        if name in read:
            return m.group(0)
        r = resolve(name)
        cands = [r] if r in read else plain.get(name.strip().lower(), [])
        if len(cands) == 1:
            fixed += 1
            return f"《{cands[0]}》"
        return m.group(0)

    return metrics.CITE.sub(fix, text), fixed


def claim(rid, title, owner):
    """선점: 같은 바퀴에 동시에 출발한 리서처끼리 같은 문서를 고르지 않게 먼저 고른 쪽이 갖는다."""
    if not (설정["선점"] and 설정["배정구역"]):
        return True
    led = _ledger(rid)
    with led["lock"]:
        holder = led["claimed"].setdefault(title, owner)
    return holder == owner


STOP = "없음"


class Pick(BaseModel):
    title: DocTitle = Field(description=f"다음에 읽을 문서 제목. 읽을 만한 후보가 없으면 '{STOP}'")
    reason: str = Field(description="고른 이유 한 문장(한국어)")


PICK_PROMPT = """당신은 [{section}] 절을 맡은 리서처입니다 ({role}). 지금까지 읽은 요약과
후보 문서 카드를 보고, 이 절을 쓰는 데 가장 도움이 될 문서 하나를 고르세요.
{stop_rule}

[전체 질문] {question}
{hint}[지금까지 읽은 요약]
{notes}

[후보 문서 카드]
{cards}"""


def pick_next(rid, question, task, notes, cands, hint="", owner=None, allow_stop=True):
    """allow_stop=False는 단일 에이전트 대조군용 — 예산을 끝까지 쓰게 해 상대를 묶지 않는다."""
    stop_rule = (f'이 절과 상관없는 문서밖에 없으면 억지로 고르지 말고 "{STOP}"이라고 답하세요 — '
                 "예산을 남겨도 됩니다." if allow_stop else "반드시 후보 중 하나를 고르세요.")
    choice = ask(rid, "sub", PICK_PROMPT.format(
        section=task["title"], role=role_desc(task), question=question, stop_rule=stop_rule,
        hint=f"[이번에 찾아야 할 것] {hint}\n" if hint else "",
        notes="\n".join(f"《{t}》 {s}" for t, s in notes) or "(아직 없음)",
        cards="\n".join(f"《{t}》 {card(t)}" for t in cands)), Pick)
    if allow_stop and choice.title.strip() == STOP:
        return None, f"리서처가 멈춤: {choice.reason}"
    # 후보 밖이면 첫 후보로, 다른 리서처가 선점했으면 다음 후보로 — 추가 호출 없이 코드가 고친다
    chosen = resolve(choice.title)
    order = [chosen] + [t for t in cands if t != chosen] if chosen in cands else cands
    for t in order:
        if owner is None or claim(rid, t, owner):
            return t, choice.reason if t == chosen else f"(코드 교정: {choice.title}) {choice.reason}"
    return None, "후보 없음"


class Draft(BaseModel):
    text: str = Field(description="절 원고(한국어). 문장마다 끝에 근거 문서를 《문서명》으로 표시")
    verdict: Literal["충분", "부족"] = Field(description="이 절의 근거가 충분한가")
    missing: str = Field(description="부족하면 어떤 사실·자료가 더 필요한지 한 문장. 충분하면 빈 문자열")


WRITE_PROMPT = """당신은 보고서의 [{section}] 절을 맡은 리서처입니다 ({role}).
아래 읽기 노트만 근거로 이 절의 한국어 원고를 쓰세요.

[전체 질문] {question}
[규칙]
- 600자 이상. 소제목 없이 본문만.
- **모든 문장**은 마침표 바로 앞에 근거 문서를 《문서명》으로 붙입니다. 문서명은 노트 제목(영문) 그대로.
  예) 노키아는 2007년 세계 휴대폰 시장의 40%를 차지했다《Nokia》. 그러나 아이폰 출시 후 점유율이 급락했다《Nokia》《IPhone》.
- 근거를 붙일 수 없는 문장(일반론·평가·맺음말)은 쓰지 않습니다. 노트에 없는 내용도 쓰지 않습니다.
- verdict는 '근거 자료'가 모자랄 때만 "부족"이고, 그때는 missing에 어떤 사실이 더 필요한지 반드시 적습니다.
  글자 수·문장 수 같은 형식은 사유가 아닙니다.
{prev}
[읽기 노트]
{notes}"""


def researcher(p: dict):
    rid, q, task, avoid, prev = p["run_id"], p["question"], p["task"], p["avoid"], p["prev"]
    owner = task["title"]
    prev_read = prev["read"] if prev else []
    hint = prev["missing"] if prev else ""
    budget = task["budget"]
    new_read, notes, trace, read_chars = [], [], [], 0

    if prev or not task["start"]:      # 재위임이거나 배정이 꺼져 있으면 첫 문서도 스스로 고른다
        cands = 후보목록(prev_read, avoid)
        title, why = pick_next(rid, q, task, notes, cands, hint, owner) if cands else (None, "")
        if title:
            trace.append({"type": "pick", "doc": title, "reason": why, "from": len(cands)})
    else:
        title = task["start"]
        claim(rid, title, owner)
        trace.append({"type": "pick", "doc": title, "reason": "코디네이터 배정", "from": 0})

    for i in range(budget):                       # 노드 안 루프 — 반드시 상한(예산)
        if title is None:
            break
        summary, n = read_one(rid, title, q, task, hint)
        new_read.append(title)
        read_chars += n
        relevant = summary != NOT_RELEVANT
        if relevant:
            notes.append((title, summary))
        trace.append({"type": "read", "doc": title, "chars": n, "relevant": relevant,
                      "summary": summary[:400]})
        if i == budget - 1:
            break
        cands = 후보목록(prev_read + new_read, avoid)
        title, why = pick_next(rid, q, task, notes, cands, hint, owner) if cands else (None, "후보 없음")
        trace.append({"type": "pick" if title else "stop", "doc": title, "reason": why,
                      "from": len(cands)})

    if prev and not new_read:   # 새로 읽은 게 없으면 다시 쓰지 않는다 — 재작성은 인용만 잃는다
        print(f"   researcher {p['round']}바퀴 [{task['title']}] 새 문서 0건 → 지난 원고 유지")
        return {"drafts": [prev | {"round": p["round"], "new_read": [], "read_chars": 0,
                                   "trace": trace, "kept_prev": True}]}

    prev_block = (f"\n[지난 원고 — 근거를 지키면서 보완해 다시 씁니다]\n{prev['text']}\n"
                  f"[지난번 부족했던 점] {hint}\n" if prev else "")
    if prev:   # 지난 바퀴에 읽은 문서도 인용할 수 있게 노트 제목만 넘긴다(원문은 다시 안 읽는다)
        notes = [(t, "(지난 바퀴에 읽음 — 지난 원고의 해당 인용 참고)") for t in prev_read] + notes
    draft = ask(rid, "sub", WRITE_PROMPT.format(
        section=task["title"], role=role_desc(task), question=q, prev=prev_block,
        notes="\n\n".join(f"《{t}》\n{s}" for t, s in notes) or "(관련 있는 문서 없음)"), Draft)

    read = prev_read + new_read
    text, cite_fixed = canon_cites(draft.text, read)
    cites = metrics.CITE.findall(text)
    fake = sorted(set(cites) - set(read))
    verdict, missing = draft.verdict, draft.missing.strip()
    if verdict == "부족" and not missing:      # 무엇이 부족한지 못 대면 재위임 지시를 만들 수 없다 → 무효
        verdict, missing = "충분", "(사유 없는 '부족' 신고 — 무효 처리)"
    print(f"   researcher {p['round']}바퀴 [{task['title']}] {len(new_read)}/{budget}건 "
          f"({' → '.join(new_read) or '후보 없음'}) · {len(text)}자 · 인용 {len(cites)} · "
          f"허위 {len(fake)} · {verdict}" + (f" · 인용명 교정 {cite_fixed}" if cite_fixed else ""))
    return {"drafts": [{"section": task, "round": p["round"], "text": text, "read": read,
                        "new_read": new_read, "read_chars": read_chars, "verdict": verdict,
                        "missing": missing, "trace": trace, "인용교정": cite_fixed}]}


# ---------- ③ 점검 (LLM 호출 없음: 자기신고만 모은다) ----------

def review(state: State):
    rid = state["run_id"]
    finals = pick_finals(state["drafts"], state["sections"])
    this_round = [d for d in state["drafts"] if d["round"] == state["round"]]
    weak = [d for d in this_round if d["verdict"] == "부족"]
    with _ledger(rid)["lock"]:                     # 코디네이터가 본 것은 신고뿐
        _ledger(rid)["coord_chars"] += sum(len(d["verdict"]) + len(d["missing"]) for d in this_round)

    if not weak:
        stop = "충분"
    elif not 설정["재위임"]:
        stop = "재위임 꺼짐"
    elif state["round"] >= 설정["최대바퀴"]:
        stop = "최대바퀴 도달"
    else:
        stop = None
    if stop:
        print(f"③ 점검: {state['round']}바퀴 · 부족 {len(weak)}절 → 종료({stop})")
        return {"retry": [], "종료": stop}

    keep = {d["section"]["title"]: d for d in finals}
    print(f"③ 점검: {state['round']}바퀴 · 부족 {len(weak)}절 → 재위임")
    for d in weak:
        print(f"    [{d['section']['title']}] 사유: {d['missing']}")
    return {"retry": [{"section": d["section"], "prev": keep[d["section"]["title"]]} for d in weak],
            "round": state["round"] + 1}


def after_review(state: State):
    return "dispatch" if state.get("retry") else "synthesize"


# ---------- ④ 종합 (본문은 다시 쓰지 않는다) ----------

class Frame(BaseModel):
    intro: str = Field(description="머리말 3~4문장(한국어)")
    outro: str = Field(description="맺음말 3~4문장(한국어)")


EDIT_PROMPT = """당신은 보고서 편집자입니다. 절 본문은 고치지 않습니다.
아래 절 제목과 첫머리만 보고 머리말과 맺음말을 한국어로 쓰세요. 새로운 사실은 덧붙이지 않습니다.

[질문] {question}
[보고서 제목] {title}
[절 목록]
{peek}"""


def synthesize(state: State):
    finals = pick_finals(state["drafts"], state["sections"])
    peek = "\n".join(f"{i}. {d['section']['title']}: {d['text'][:120]}…"
                     for i, d in enumerate(finals, 1))           # 절마다 제목 + 첫 120자만
    frame = ask(state["run_id"], "coord", EDIT_PROMPT.format(
        question=state["question"], title=state["report_title"], peek=peek), Frame)
    parts = [f"# {state['report_title']}", frame.intro]
    parts += [f"## {i}. {d['section']['title']}\n{d['text']} ({d['section']['role']} 담당)"
              for i, d in enumerate(finals, 1)]
    parts += ["## 맺음말", frame.outro]
    report = "\n\n".join(parts)
    print(f"④ 종합: 보고서 {len(report):,}자 (절 {len(finals)}개)")
    return {"report": report, "finals": finals}


# ---------- ⑤ 측정 ----------

def evaluate(state: State):
    led = _ledger(state["run_id"])
    m = metrics.compute(state["report"], state["finals"], state["drafts"])
    m |= {"호출": led["calls"], "코디네이터_입력자": led["coord_chars"],
          "서브에이전트_입력자": led["sub_chars"],
          "격리율": round(led["coord_chars"] / max(led["coord_chars"] + led["sub_chars"], 1), 3),
          "재위임절": len({d["section"]["title"] for d in state["drafts"] if d["round"] > 1}),
          "버린원고": len(state["drafts"]) - len(state["finals"]), "종료": state.get("종료", "")}
    al = metrics.alarms(m)
    print(f"⑤ 측정: 근거율 {m['근거율']:.1%} · 편중 {m['편중']:.1%}(《{m['최다문서']}》) · "
          f"중복률 {m['중복률']:.1%} · 읽고안씀 {m['읽고안씀']} · 호출 {m['호출']} · "
          f"격리율 {m['격리율']:.1%}" + (f" · ⚠ 경보 {al}" if al else " · 경보 없음"))
    return {"metrics": m}


def build():
    g = StateGraph(State)
    for name, fn in [("plan", plan), ("dispatch", dispatch), ("researcher", researcher),
                     ("review", review), ("synthesize", synthesize), ("evaluate", evaluate)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "dispatch")
    g.add_conditional_edges("dispatch", fan_out, ["researcher"])
    g.add_edge("researcher", "review")
    g.add_conditional_edges("review", after_review, ["dispatch", "synthesize"])
    g.add_edge("synthesize", "evaluate")
    g.add_edge("evaluate", END)
    return g.compile()


GRAPH = build()


def stream_run(question, **override):
    """설정을 잠깐 바꿔 실행하며 노드별 진행을 내보낸다. 마지막에 ("done", 결과)를 내보낸다.
    끝나면(중간에 멈춰도) 설정을 반드시 원래대로 되돌린다."""
    unknown = set(override) - set(설정)
    if unknown:
        raise KeyError(f"없는 설정: {unknown}")   # 오타로 스위치가 안 꺼지는 사고를 막는다
    old = dict(설정)
    설정.update(override)
    rid = uuid.uuid4().hex[:8]
    t = time.time()
    state = {"run_id": rid, "question": question, "drafts": []}
    try:
        for chunk in GRAPH.stream(state, {"recursion_limit": 60}, stream_mode="updates"):
            for node, update in chunk.items():
                if update:
                    for k, v in update.items():
                        state[k] = state.get(k, []) + v if k == "drafts" else v
                yield node, update
        state["metrics"]["시간"] = round(time.time() - t)
        state["설정"] = dict(설정)
        yield "done", state
    finally:
        설정.clear()
        설정.update(old)
        RUNS.pop(rid, None)


def run(question, **override):
    for node, payload in stream_run(question, **override):
        if node == "done":
            return payload


def save_run(result, label, qid="", path=ROOT / "output" / "runs.jsonl"):
    rec = {"label": label, "qid": qid, "question": result["question"], "설정": result["설정"],
           "sections": result.get("sections"), "plan_notes": result.get("plan_notes"),
           "drafts": result["drafts"], "finals": [d["section"]["title"] for d in result["finals"]],
           "report": result["report"], "metrics": result["metrics"], "종료": result.get("종료"),
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", help="질문 문장 (또는 --qid)")
    ap.add_argument("--qid", help="data/questions.json의 id, 예: Q5")
    args = ap.parse_args()
    q = next(x for x in QUESTIONS if x["id"] == args.qid)["question"] if args.qid else \
        (args.question or QUESTIONS[2]["question"])
    print(f"질문: {q}\n")
    res = run(q)
    save_run(res, "기본", args.qid or "")
    out = ROOT / "output" / "reports" / f"{args.qid or 'adhoc'}_기본.md"
    out.write_text(res["report"], encoding="utf-8")
    print(f"\n보고서 → {out.relative_to(ROOT)}")
