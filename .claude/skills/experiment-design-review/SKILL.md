---
name: experiment-design-review
description: "MSA 연쇄 장애 완화 연구의 실험 설계(벤치마크·baseline·ablation·평가지표·라벨링·트래픽/장애 시나리오)를 검토한다. '실험 설계 봐줘', 'baseline 목록 검토', 'ablation 설계 확인', 'ECE·Drop Rate 지표 맞는지', '라벨 누수 있는지 점검', '벤치마크 선택 타당한지' 요청 시 반드시 사용. 아직 실험을 실행하지 않으므로 실행 결과 분석에는 사용하지 않는다."
---

# Experiment Design Review

실험은 2026-07 기준 미착수다. 이 스킬은 **설계 문서**(`docs/proposal.md` §4, `research/challenges.md`)를 검토해 착수 전에 결함을 잡는다.

## 점검 체크리스트

1. **벤치마크 대표성** — `OnlineBoutique`(11~12노드, 메인, cartservice→Redis 상태성 병목)가 `GRAF`와 동일 규모라 직접 비교 근거가 되는지, `SockShop`(AGQ 규모 참고)·`TrainTicket`(보조, 서브셋 15~25)·`DeathStarBench`(FIRM 참고)의 역할이 명확한지 확인한다.
2. **baseline의 목적 분리** — 각 baseline이 정확히 무엇을 검증하는지:
   - `BL_ReactiveHPA`: 반응형 HPA만(사후반응성 비교)
   - `BL_RuleBased`: 규칙 기반 임계치(위상 미반영 비교)
   - `BL_LSTM`: LSTM+동일 Policy Engine — **위상 인지 기여 검증의 핵심**. 이 baseline이 빠지면 "GAT가 정말 필요한가"에 답할 수 없다.
   - `BL_GRAFlike`: 자원할당/스케일업만 — 커넥션풀서 역효과(Thundering Herd) 실증용.
   - `BL_FIRMlike`: 조치공간 동일+위상 미반영 예측기(2×2 설계 완성, 선택).
   목적이 문서에 없거나 다른 baseline과 혼동되게 서술되어 있으면 지적한다.
3. **라벨 누수 점검** — 온톨로지 불변식(`devkit/ontology.yaml` rules):
   - `GroundTruthLabel`이 self-SLO 위반 이력을 feature로 포함하지 않는지(누수/지름길, H5)
   - `TemporalStatisticFeature`의 슬라이딩 윈도우가 `t` 이전만 사용하는지(라벨 구간 [t,t+Δ]과 겹치면 누수)
   실험 설계 문서에 이 제약이 명시적으로 기술되어 있는지 확인하고, 없으면 추가를 제안한다.
4. **평가지표 분리** — `ECE`는 `FailureProbability`(p̄) 보정 검증, `DropRate`는 `Uncertainty`(u) 대응 효과(불필요 Shedding 감소율) 검증 — 두 지표를 하나로 뭉뚱그리는 서술이 있으면 지적한다(H3).
5. **Ablation 통제** — `Abl_SnapshotVsTAGAT`(스냅샷 전용 GAT vs TA-GAT)가 다른 변수(readout 방식, 앙상블 크기 등)를 고정한 채 "시계열 통계 기여"만 분리하는지 확인한다.
6. **트래픽/장애 시나리오** — `TrafficProfile`(정상/버스트/점진증가, Locust/k6)과 `FaultInjection`(Istio/Chaos Mesh)이 `PredictionHorizon`(예측 지평 Δ, 폐루프 응답시간이 하한)과 정합적인 시나리오로 설계되어 있는지 확인한다.
7. **`FirstOrderFault` 처리** — 랜덤 주입된 1차 장애(선행신호 없음)가 positive 라벨에서 제외/유예되는 규칙이 라벨링 전략(`LabelingStrategy`, 자동 역라벨링)에 반영되어 있는지 확인한다(E6-3).

## 결과 보고

체크리스트 항목별로 "설계됨/미흡/누락"을 표시하고, 미흡·누락 항목에는 구체적 보강 문구를 제안한다. 수치(임계값, 노드 수 등)가 "실험 이월 항목"으로 명시된 것은 확정값처럼 요구하지 않는다.
