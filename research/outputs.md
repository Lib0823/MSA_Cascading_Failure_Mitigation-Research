# 연구 결과 및 산출물 (Outputs)

> 현재(2026년 9월 기준) 연구는 프로포절 준비 단계이며, 실험은 착수하지 않았다. 아래는 지금까지 만들어진 산출물과 앞으로 예정된 산출물을 구분해 정리한 것이다.
> 최종 갱신: 2026-09-24 — 범위 재설계 반영.

## 현재까지의 산출물 (프로포절 준비 단계)

| 산출물 | 위치 | 상태 |
|---|---|---|
| 연구계획서(프로포절) 초안 | [docs/proposal.md](../docs/proposal.md) | 작성 중 (v1) |
| 관련연구 6자 비교표 (GRAF/FIRM/DeepScaler/AGQ/GraphGRU/**Safe Remediation**) + 3분할 구조 | [docs/proposal.md](../docs/proposal.md) §2-B·§3 | 완성 (Safe Remediation 원문 확인 완료) |
| 의사결정·팩트체크 로그 | [research/challenges.md](challenges.md) | 지속 갱신 |
| 일정/리스크 관리 계획 | [research/timeline.md](timeline.md) | 지속 갱신 |
| 실험 환경 사양·제약·프로토콜 | [research/environment.md](environment.md) | 완성 (하드웨어 확정 후 갱신) |
| 피드백·검수 기록 | [docs/feedback/](../docs/feedback/) | 5건 (0802 최적화 / 0906 설계이슈 / 0906 LLM 확장 / 0908 자체 검수 백로그 / **0923 범위 재설계**) |
| **범위 재설계 제안서** | [docs/feedback/0923_scope_redesign_proposal.md](../docs/feedback/0923_scope_redesign_proposal.md) | **반영 완료 (2026-09-24)** |
| 개인 학습 커리큘럼 및 노트 (ANN → GNN → 신뢰도 추정) | [research/study_notes/](study_notes/) | 진행 중 |
| 참고문헌 정리 | [docs/proposal.md](../docs/proposal.md) "참고문헌" 절 | 완성 (원문 PDF는 저작권상 리포지토리에 미포함) |

## 예정된 산출물 (미착수 — 폴더 구조만 미리 준비됨)

- **논문 PDF** — 최종 학위논문 (`docs/paper.pdf`)
- **발표자료** — 프로포절/본심사 발표 슬라이드 (`docs/presentation.pdf`)
- **벤치마크 시스템** — 자작 Spring 구성 6~7개 서비스(의존 깊이 4단) + LLM 노드. **공개 대상**이며 자작 벤치마크의 재현성을 확보하는 핵심 산출물이다
- **코드** — 예측기 3단(규칙/GBM/GAT), Policy Engine, Actuator 4종, 앱 레벨 장애 주입 필터 (`code/model`, `code/backend`, `code/simulator`, `code/utils`)
- **실험 데이터/결과** — S1·S2·S3 시나리오 실측 데이터, 조치 정책 비교(A1~A6), 예측기 3단 비교(B1~B3), **오탐 비용·조치 리드타임 `ℓₐ` 측정 결과** (`data/raw`, `data/processed`, `data/sample`)
- **학회 논문** — 2027 춘계 학술대회 4~6p (S1 중심)
- **다이어그램/그림** — 아키텍처 다이어그램, 실험 결과 그래프 (`assets/diagrams`, `assets/figures`, `assets/images`)

위 항목들은 실험 착수 후 순차적으로 채워질 예정이며, 진행 경과는 [research/timeline.md](timeline.md)의 마일스톤을 참고.
