---
name: gnn-policy-implementation-support
description: "GAT 예측 모델·Deep Ensemble·Policy Engine·Actuator를 Python(PyTorch Geometric)으로 구현 착수할 때 설계·스켈레톤을 지원한다. 'GAT 모델 구현 시작', 'Policy Engine 코드 짜줘', 'code/model 스켈레톤 만들어줘', 'CostFunction 구현', 'Actuator 인터페이스 설계' 요청 시 반드시 사용. code/ 가 아직 비어있는 스캐폴드 상태에서도 설계 논의 단계부터 사용한다."
---

# GNN + Policy Engine Implementation Support

`code/model`(GAT+Deep Ensemble), `code/backend`(Policy Engine), `code/simulator`(트래픽/장애주입)는 2026-07 기준 `.gitkeep`만 있다. 이 스킬은 착수 시점부터 온톨로지·연구계획서의 개념을 코드로 정확히 번역하도록 돕는다.

## 왜 온톨로지를 따라야 하는가

`devkit/ontology.yaml`에 정의된 클래스·규칙은 이미 심사 방어 논리(Concern6~13, DesignDecision)와 묶여 있다. 구현이 온톨로지와 어긋나면 "설계는 이렇게 방어했는데 코드는 다르게 동작한다"는 간극이 생긴다 — 특히 아래 항목은 **구현 버그가 아니라 논문 전체의 방어 논리가 무너지는 지점**이다.

## 구현 시 반드시 지킬 불변식

1. **SafetyGuard는 별도 규칙이 아니라 CostFunction의 파생물**이다. `if uncertainty > threshold: withhold` 같은 하드코딩 분기를 짜지 않는다 — `θₐ = Dₐ·Rₐ/(mₐ·L−Dₐ(1−Rₐ))`를 계산하고 `p_eff > θₐ`로 판단하는 한 경로만 존재해야 하며, 저신뢰·저위험이 자연히 `Withhold`로 귀결되게 한다(G4).
2. **`p_eff = max(0, p̄_v − κ√u_v)`를 정확히 구현**한다. `p̄_v`(Deep Ensemble 평균)와 `u_v`(앙상블 분산)를 먼저 각각 계산한 뒤 이 식으로 결합한다 — "1−분산" 등 변형 금지.
3. **Readout은 `SharedPerNodeHead`**(노드 수와 무관한 파라미터, 모든 노드에 동일 MLP)로 구현한다. `FlattenReadout`(GRAF 방식) 재도입 금지.
4. **`isProvisioning`은 `K8sScaleUp`에만 true.** Actuator 인터페이스에 이 플래그를 노출하고, Policy Engine이 조치 선택 시 이 축을 근거로 쓸 수 있게 한다.
5. **2계층 분리**: `LocalReflexTier`(자기 통계만 참조, ms 반응)와 `GNNProactiveTier`(위상 전체, N초 주기, lease TTL)를 별도 모듈/프로세스로 설계한다. 충돌 중재는 `ORSemantics`(OR 의미론) + `EscalateOnly`(GNN이 로컬 정상을 되돌리지 않음) + `Lease`(TTL 미갱신 시 자동 만료)로 구현한다.
6. **feature 스키마에서 라벨 누수 원천 차단**: self-SLO 위반 이력 컬럼을 feature 스키마에 아예 넣지 않는다. `TemporalStatisticFeature`는 `t` 이전 윈도우만 사용하도록 슬라이딩 윈도우 인덱싱을 구현한다.

## 절차

1. **설계 먼저, 코드는 승인 후**: 모듈 구조·클래스 인터페이스·데이터 스키마를 먼저 제시하고, 사용자 승인을 받은 뒤 `code/` 하위에 파일을 생성한다(스캐폴드 단계이므로 매번 새로 설계 논의가 필요할 가능성이 높다).
2. **심볼명 추적성 유지**: 코드 클래스/함수명을 `devkit/ontology.yaml`의 개체명(GAT, DeepEnsemble, PolicyEngine, CostFunction, EffectiveRisk, ActionThreshold 등)과 최대한 일치시킨다 — 추후 온톨로지에 `source_refs`로 코드 경로를 역참조할 수 있게 하기 위함이다.
3. **미확정 수치는 TODO로 명시**: `L`, `Dₐ`, `Rₐ`, `mₐ`, `κ`는 "실험 이월 항목"(tunedInExperiment=true)이다. 하드코딩이 불가피하면 코드에 출처가 "실험 미확정 placeholder"임을 명확히 표시한다.
4. **구현 중 온톨로지와 충돌 발견 시**: 임의로 진행하지 말고 사용자에게 "온톨로지를 갱신할지, 구현을 조정할지" 확인한다(`research-writer` 에이전트와 교차 확인 권장).

## 하지 않는 것

- 실험 실행·결과 분석(실험 미착수).
- 벤치마크/baseline 선택 자체의 타당성 판단(`experiment-design-review`의 영역).
