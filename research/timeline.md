# 연구 진행 과정 (Timeline)

## 현재 위치

- **1학기 종료 시점 (2026년 7월 기준)**
- 특수대학원 통상 구조: 4~5학기(2~2.5년) 과정, 프로포절 심사는 보통 3학기차, 본심사는 마지막 학기
- 남은 실질 작업 가능 시간: 약 7~10개월 추정(직장인 겸업, 학기 중 진도 저하 감안)

## 작업 항목별 예상 소요 (순차 기준, 일부는 병행 가능)

| 항목 | 예상 기간 | 병행 가능 여부 |
|---|---|---|
| GNN 데이터 파이프라인/그래프 구성 | 1~2개월 | - |
| GNN 모델 학습 (GAT, Deep Ensemble N=5) | 1~2개월 | - |
| LSTM baseline 학습 | +2~4주 | GNN과 병행 가능 |
| Policy Engine 비용함수/의사결정 로직 구현 | 1~2개월 | - |
| Actuator 통합 (2~5종) | 1~1.5개월 | 부분 병행 가능 |
| 실험 환경 구축 + 반복실험 + 임계치 튜닝 | 1~2개월 (보통 예상보다 오래 걸림) | - |
| 논문 작성 | 1~1.5개월 | - |
| **합계** | **약 7~10개월** | |

## 시간 부족 시 컷 우선순위 (위에서부터 먼저 제외)

1. Train Ticket 서브셋 보조 실험 (부하 스케일업 실험으로 대체 가능)
2. FIRM류 baseline 추가 (GRAF류 baseline + LSTM baseline만으로도 최소 방어 가능)
3. Actuator 5종 → 2종(Circuit Breaker + Read Redirection)으로 축소, 나머지(Scale-up/Shedding/Brownout)는 "확장 가능 설계"로만 서술. 특히 Brownout은 앱 계측(필수/선택 분리)이 필요해 컷 우선순위가 가장 높음.
4. 부하 스케일업 실험(동시 사용자 수 변화 실험) — 그래도 시간이 없으면 생략 가능하나 §4-4 방어력이 약해짐에 유의

## 마일스톤

**2학기 중반까지 (프로포절 심사 대비)**
- [x] 신뢰도(Confidence) 산출 방식 확정 — Deep Ensemble(N=5)로 확정 ([challenges.md](challenges.md) F1)
- [x] 비용함수 초안 수식화 — 기대비용 최소화형으로 확정 ([challenges.md](challenges.md) G1, [docs/proposal.md](../docs/proposal.md) §2-B)
- [x] 관련연구 비교표(GRAF/FIRM/AGQ/GraphGRU 4자) 완성 ([docs/proposal.md](../docs/proposal.md) §3)
- [x] 벤치마크 최종 확정 — Online Boutique 메인
- [x] GNN 추론 레이턴시 vs 반응속도 이슈 해법 방향 결정 — 2계층 제어(로컬 반사 + GNN 선제)로 확정 ([challenges.md](challenges.md) G2, [docs/proposal.md](../docs/proposal.md) §2-D)

**프로포절 심사** (약 3학기차)

**마지막 학기 시작 전**: 코어 구현(Policy Engine, 핵심 Actuator) + 메인 실험 완료

**마지막 학기**: 논문 작성 + 본심사

## 예산/인프라 계획

- **메인 실험**: 로컬(Minikube/K3s). 학습과 실험 실행을 시간적으로 분리해 리소스 관리.
- **보조 실험(Train Ticket 서브셋) 진행 시에만**: 시간단위 과금 VPS(Hetzner/DigitalOcean/Vultr 등, GPU 불필요)를 며칠 단기 대여 → 데이터 수집 후 즉시 종료. AWS 신규 크레딧(200달러/6개월)의 시계를 이 실험에 미리 소모하지 않는다.
- GPU가 필요해지는 예외 상황(현재는 낮은 확률)이 생기면 Vast.ai 등 GPU 마켓플레이스를 고려한다.

## 아직 해결되지 않은 오픈 이슈

상세 배경과 팩트체크 근거는 [challenges.md](challenges.md) "G. 오픈 이슈" 항목 참고.

| # | 이슈 | 심사 전 필요 수준 |
|---|---|---|
| G3 | 실험용 트래픽 프로파일의 구체적 파라미터(버스트 강도, 점진 증가 속도 등) 미정 | 프로포절 심사 필수 항목 아님 — 실험 단계로 미뤄도 무방. mₐ(완화효과) 측정 방식 정의도 이 이슈와 연계. |

> [해결됨] (1) 신뢰도 산출 방식은 Deep Ensemble(N=5)로 확정 ([challenges.md](challenges.md) F1). (2) 비용함수 수식화(G1)는 기대비용 최소화형으로 확정 ([challenges.md](challenges.md) G1, [docs/proposal.md](../docs/proposal.md) §2-B). (3) GNN 추론 레이턴시 vs 즉각반응(G2)은 2계층 제어(로컬 반사 + GNN 선제)로 확정 ([challenges.md](challenges.md) G2, [docs/proposal.md](../docs/proposal.md) §2-D). 위 세 항목은 목록에서 제외됨.
