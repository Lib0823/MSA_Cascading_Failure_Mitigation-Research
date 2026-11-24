# 연구 결과 및 산출물 (Outputs)

> 최종 갱신: 2026-11-25. 지금까지 만든 산출물과 앞으로 만들 산출물을 나눠 적는다. 주요 결과 요약은
> [README](../README.md) "Results" 와 원고 초안 §5 에, 현재 위치와 다음 할 일은 [timeline.md](timeline.md) 맨 위에
> 있다. *(2026-09-24 판은 "프로포절 준비 단계, 실험 미착수" 였다.)*

## 지금까지의 산출물

### 논문·문서

| 산출물 | 위치 | 상태 |
|---|---|---|
| 2027 춘계 학회 원고 초안 (2~3쪽) | [docs/paper/draft-spring2027.md](../docs/paper/draft-spring2027.md) | 초안·투고 전 검토 완료 (2026-11-24). 투고 2027-03 무렵 |
| 그림 1 (제안 구조) | [docs/paper/figure1-architecture.svg](../docs/paper/figure1-architecture.svg) | 완료 (단 폭 6.5pt) |
| 원고 골격 · 분량 추정기 | [outline-spring2027.md](../docs/paper/outline-spring2027.md) · [measurePages.py](../docs/paper/measurePages.py) | 완료 |
| 결론 결정표 초안 | [docs/decision-table.md](../docs/decision-table.md) | 초안 (2026-11-24, J37). 확정은 학위논문 |
| 연구계획서 | [docs/proposal.md](../docs/proposal.md) | 지속 갱신 |
| 의사결정·팩트체크 로그 | [challenges.md](challenges.md) | J1~J38 |
| 일정 · 현재 위치 | [timeline.md](timeline.md) | 지속 갱신 |
| 실험 환경 사양·제약·프로토콜 | [environment.md](environment.md) | 지속 갱신 |
| 피드백·검수 기록 | [docs/feedback/](../docs/feedback/) | 6건 (0802 · 0906 ×2 · 0908 · 0923 범위 재설계 · 0927 vLLM 복귀) |
| 참고문헌 | proposal "참고문헌" 절 · 원고 [1]~[7] | 원고 서지 확인 완료 (2026-11-24) |
| 개인 학습 커리큘럼·노트 | [study_notes/](study_notes/) | 진행 중 |

### 실험 시스템·코드

| 산출물 | 위치 | 비고 |
|---|---|---|
| 자작 서비스 — 단일 이미지 + 프로파일, Resilience4j 조치 4종, 앱 레벨 장애 주입 | [code/services/](../code/services/) | Java 14 파일 |
| 사슬 실행 · 역라벨링(전파 라벨) 하네스 | [code/chain/](../code/chain/) | `gateway → orders → payment → PostgreSQL` |
| 폐루프 부하 생성기 · 게이트 ①.5 | [code/calibration/](../code/calibration/) | |
| 예측기(규칙 · GBM 앙상블) · Policy Engine · 분석 | [code/model/](../code/model/) | |
| 폐루프 제어기 · 예측기 학습 | [code/control/](../code/control/) | |
| 배치 실행 · 정책 비교 · 조치별 측정 | [code/experiment/](../code/experiment/) | |
| K8s 매니페스트 | [deploy/k8s/](../deploy/k8s/) | 8개, 맥북 minikube 에 실적용 |
| 합계 | | Python 42 파일 · 11,183 줄 |

### 실험 데이터·결과

| 결과 | 위치 | 규모 |
|---|---|---|
| 모델 코퍼스 (장애·대조군 에피소드 13 + 강제 조치 셀 21) | `code/chain/results/` — 규칙은 [corpus.py](../code/model/corpus.py) | 34 에피소드 · 10,886 표본. ⚠️ 기록 공백 5개 (J38) |
| 폐루프 정책 원장 (A2 · A4 · A5 · A6) | `code/experiment/results/ledger.jsonl` | 48 셀 |
| 강제 조치 원장 | `code/chain/results/**/remediation-ledger.jsonl` | Compose 4 셀 + K8s 76 셀 |
| 분석 산출물 — 최소 관측(H7) · 위상 사다리(H1) · Δ 표 · 결정표 등 | `code/model/*.json` | 원고 메모의 "수치와 출처" 표가 파일별로 대응시킨다 |

## 예정된 산출물

- **2027 춘계 학회 투고** — 2027-03 무렵. CFP(보통 1~2월)에서 분량·양식·AI 활용 고지를 확인한다
- **학위논문** — 서비스 6~7개(의존 깊이 4단)·LLM 노드(S2)·실험 머신(게이트 ③), 예측기 3단(규칙/GBM/GAT),
  조치 인지 예측기(J37 선택지 c), 결정표 확정
- **KCI 등재지 논문** — 학위논문과 함께 확장 (2027 하반기~2028). 졸업 요건에 따라 앞당길 수 있다
- **발표자료** — 프로포절·본심사 발표
- **벤치마크 시스템 공개** — 자작 서비스 구성. 자작 벤치마크의 재현성을 확보하는 산출물이다
