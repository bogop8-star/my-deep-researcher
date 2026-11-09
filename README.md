# 📉 몰락한 기업 딥리서처

한때 업계 1위였던 기업들이 왜 무너졌는지, **영어 위키백과 48건(344,731토큰, gpt-4o-mini 윈도우의 2.7배)** 을
코디네이터 1명 + 리서처 N명이 나눠 읽고 **한국어 장문 보고서**로 정리하는 멀티 에이전트입니다.

```
질문 → ① 기획(목차·역할·시작 문서·예산) → ② 배치(Send ×N, 남의 구역 알림)
     → 리서처 N명이 동시에 읽고 자기 절을 씀 → ③ 점검(자기신고 '부족'만 재위임, 바퀴 상한)
     → ④ 종합(본문은 고치지 않고 이어 붙임) → ⑤ 측정(정답표 없는 지표)
```

설계와 실험 결과는 [REPORT.md](REPORT.md)에 있습니다.

## 실행

```bash
pip install "langgraph~=1.2.0" "langchain~=1.4.0" "langchain-openai~=1.6.0" python-dotenv tiktoken streamlit
```

프로젝트 폴더에 `.env` 파일을 만들고 OpenAI 키를 넣습니다(저장소에는 올라가지 않습니다).

```
OPENAI_API_KEY=sk-...
```

| 하고 싶은 것 | 명령 |
|---|---|
| 데모 화면 | `streamlit run app.py` |
| 질문 하나 실행 | `python graph.py --qid Q5` 또는 `python graph.py "싸이월드는 왜 밀려났나?"` |
| 단일 에이전트 대조군 | `python baseline.py --qid Q5` |
| 소거 실험 (질문 하나) | `python ablation.py --qid Q5 --repeats 2` |
| 소거 실험 결과 모으기 | `python ablation.py --summarize` |
| 코퍼스 다시 받기 | `python scripts/build_corpus.py` |
| 코퍼스 크기·링크 확인 | `python scripts/corpus_stats.py` |

데모의 **"저장된 실행 보기"** 모드는 `output/runs.jsonl`을 읽으므로 API 키 없이도 볼 수 있습니다.

## 폴더

```
data/        corpus.json (문서 48건 + 링크) · questions.json (질문 10개와 "왜 나눌 만한가")
config.json  절수 · 절예산 · 바퀴 상한 · 역할 명단 · 장치 스위치
graph.py     기획 → 배치 → 서브에이전트 → 점검 → 종합 → 측정 (LangGraph)
metrics.py   정답표 없는 지표 — 신호와 경보
baseline.py  단일 에이전트 대조군 (같은 모델·도구·예산)
ablation.py  스위치를 하나씩 끄고 반복 측정
app.py       Streamlit 데모
output/      runs.jsonl · ablation.json · reports/
scripts/     코퍼스 수집·통계
```

자료 출처: 영어 위키백과 (CC BY-SA 4.0).
