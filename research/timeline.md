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

## 구현·실험 단계별 접근 (①→②→③)

이론(설계)이 확정된 뒤, 실체화는 리스크가 낮은 것부터 3단계로 쌓아 올린다. 각 단계는 다음 단계의 전제를 검증하는 게이트 역할을 하며, 앞 단계가 통과되기 전에 뒤 단계에 자원을 투입하지 않는다.

### ① 모델 스모크 테스트 — 코드 실현 가능성
- **목적**: 설계(노드 레벨 GAT + Deep Ensemble + 공유 per-node head, [docs/proposal.md](../docs/proposal.md) §2-A·§4-2)가 코드로 성립하는지 확인.
- **방법**: 가짜 11노드 그래프 + 랜덤 feature로 forward pass. Online Boutique·K8s 불필요.
- **확인 항목**: 텐서 shape 정합 / 노드별 위험도 + 신뢰도(앙상블 분산) 출력 / 11→20노드로 바꿔도 파라미터 불변(확장성 주장 검증, 우려 6·10).
- **비용**: 몇 시간, 로컬 CPU. **성능·정확도는 검증 대상 아님**(랜덤 데이터).
- **게이트**: 통과해야 ②의 GAT 입력 스펙(feature 차원·그래프 포맷)이 확정됨.

### ② 데이터 파이프라인 검증 — 수집 실현 가능성
- **목적**: §4-2에서 설계한 라벨·feature·그래프를 running Online Boutique에서 실제로 뽑을 수 있는지 확인 — A3·F3이 지목한 "진짜 병목(실측 수집)"의 조기 de-risk.
- **방법**: OB를 로컬 K8s(Minikube/K3s)에 소규모 배포 → 소량 트래픽(Locust) + 간단한 장애주입(Istio) → 지표 시계열·그래프 스냅샷 수집 → 자동 역라벨링([docs/proposal.md](../docs/proposal.md) §4-2)이 도는지 확인.
- **확인 항목**: 서비스별 feature(CPU/메모리/지연/에러율/스레드풀·커넥션풀) 수집 가능 여부 / 호출관계 그래프 구성 가능 여부 / [t, t+Δ] 역라벨링 동작 / 클래스 불균형 실제 비율 관찰.
- **비용**: 며칠, 로컬 K8s 필요(A3 경량화 전제: 레플리카 1, 리소스 요청 최소).
- **게이트**: 통과해야 ③의 대규모 수집이 의미를 가짐.

### ③ 전체 실험 — 연구 주장 검증
- **목적**: §4-3 baseline 비교로 연구 주장(위상 인지 > LSTM / 신뢰도 구간별 대응의 효과 / 커넥션풀 시나리오에서 자원할당 역효과) 실증.
- **방법**: 장애 유형 × 주입 지점 시나리오(커넥션풀 포함, §4-5)로 대규모 수집 → 라벨링 → GAT/LSTM 학습 → baseline 5종 비교 → 반복 실행 통계(§4-4). 학습-실험은 시간적으로 분리(A2·A3).
- **비용**: 수 주. 보조 실험(Train Ticket 서브셋) 필요 시에만 VPS 단기 대여(아래 예산 계획).
- **산출**: 논문 실험 결과 및 그림.

> **현재 위치**: 설계(이론) 확정 완료, ①~③ 미착수. 심사 전에는 ①(선택)까지만 하고, ②·③ 본격 착수는 심사 후로 둔다.

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

## 오픈 이슈 현황

상세 배경과 팩트체크 근거는 [challenges.md](challenges.md) "G. 오픈 이슈 및 해소 기록" 참고.

**프로포절 심사 대비 미결정 설계 이슈: 없음.**

- [해결] 신뢰도 산출 방식 — Deep Ensemble(N=5) ([challenges.md](challenges.md) F1)
- [해결] 비용함수 수식화(G1) — 기대비용 최소화형 ([challenges.md](challenges.md) G1, [docs/proposal.md](../docs/proposal.md) §2-B)
- [해결] 추론 레이턴시 vs 즉각반응(G2) — 2계층 제어(로컬 반사 + GNN 선제) ([challenges.md](challenges.md) G2, [docs/proposal.md](../docs/proposal.md) §2-D)
- [이관] 트래픽 프로파일 파라미터 + mₐ 측정(G3) — 실험 설계 [docs/proposal.md](../docs/proposal.md) §4-5로 이동(심사 필수 아님, 실측 기반 확정)
