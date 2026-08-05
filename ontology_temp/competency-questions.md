# 역량 질문 (Competency Questions)

> 온톨로지의 **범위를 정의**하고 완성도를 검증하는 질문 목록이다. 각 질문은 classes/properties/individuals의
> 특정 요소로 답할 수 있어야 하며, 답할 수 없으면 그 요소가 누락된 것이다(온톨로지 엔지니어링 표준 절차).

## CQ1. 시스템 · 위상

- CQ1.1 어떤 마이크로서비스가 어떤 마이크로서비스를 호출하는가? → `msacf:calls`
- CQ1.2 한 서비스의 병목 리소스가 상태성(stateful)인가 무상태(stateless)인가? → `msacf:StatefulResource` / `dependsOnResource`
- CQ1.3 상태성 리소스 병목에서 단순 스케일아웃이 왜 역효과(Thundering Herd)를 내는가? → `msacf:ThunderingHerd`, `K8sScaleUp` 의 `mitigationEffect`
- CQ1.4 위상은 정적인가? 인스턴스 변화는 무엇으로 흡수되는가? → `ServiceCallGraph`(static), `NodeFeature`

## CQ2. 장애 · 전파

- CQ2.1 노드가 언제 positive(위험)로 라벨되는가? → `SLOViolation` + `PropagationPath` 조건
- CQ2.2 전파에 기인한 위반과 1차 주입 장애를 어떻게 구분하는가? → `propagatesTo`, `FaultInjection`
- CQ2.3 연쇄 장애는 어떤 경로로 퍼지는가? → `PropagationPath`, `calls`

## CQ3. 예측 모델 · 불확실성

- CQ3.1 왜 GCN/MPNN/ChebConv이 아니라 GAT인가? → `GAT` vs `GCN`/`MPNN`, `attentionWeighting`
- CQ3.2 신뢰도(불확실성)를 어떻게 산출하는가? 왜 MC Dropout이 아니라 Deep Ensemble인가? → `DeepEnsemble` vs `MCDropout`, `quantifiesUncertaintyVia`
- CQ3.3 모델의 출력 두 축(p̄_v, u_v)은 무엇이며 어떻게 집계되는가? → `FailureProbability`, `Uncertainty`
- CQ3.4 왜 flatten이 아니라 공유 per-node head인가(확장성)? → `SharedPerNodeHead` vs `FlattenReadout`
- CQ3.5 시계열을 어떻게 반영하는가(STGNN 없이)? → `TemporalStatisticFeature`(TA-GAT)
- CQ3.6 self-SLO 위반 이력을 왜 feature에서 제외하는가(누수/지름길)? → `NodeFeature` 주석

## CQ4. 의사결정 · 비용함수

- CQ4.1 신뢰도 반영 위험도 p_eff는 어떻게 계산되는가? → `EffectiveRisk`, `p_eff = max(0, p̄ − κ√u)`
- CQ4.2 조치 a가 '보류'를 이기는 조건(임계값 θₐ)은 무엇인가? → `ActionThreshold`, `CostFunction`
- CQ4.3 신뢰도 구간(고/중/저)이 왜 손으로 긋지 않고 유도되는가? → `θₐ` 유도, `ConfidenceTier`
- CQ4.4 Safety Guard는 어디서 유도되는가(하드코딩 아님)? → `SafetyGuard` `derivedFrom` `CostFunction`
- CQ4.5 비용함수 변수(L, Dₐ, Rₐ, mₐ, κ)는 각각 무엇을 뜻하는가? → `CostVariable` 개체들

## CQ5. 조치(Actuator)

- CQ5.1 조치 5종은 무엇이며 각각 프로비저닝인가 비프로비저닝인가? → `Actuator` 하위, `isProvisioning`
- CQ5.2 각 조치의 disruption·가역성·완화효과·대상 전파속도·신뢰도 구간은? → `Actuator` 데이터 속성
- CQ5.3 Read Redirection과 Brownout·Traffic Shedding의 질적 차이는? → 개체 주석(기능유지+일관성약화 vs 기능생략 vs 요청거부)
- CQ5.4 어떤 조치가 상태성 병목(cartservice→Redis)에서 효과적이고 어떤 것이 역효과인가? → `mitigationEffect` on `StatefulResource`

## CQ6. 제어 구조(2계층)

- CQ6.1 어떤 조치가 Tier1(로컬 반사)이고 어떤 것이 Tier2(GNN 선제)인가? → `triggeredBy`, `ControlTier`
- CQ6.2 두 계층이 같은 액추에이터를 건드릴 때 충돌을 어떻게 막는가? → `ConflictResolution`(OR/escalate-only/lease)
- CQ6.3 GNN 명령이 시한부(lease TTL)인 이유는? → `Lease`

## CQ7. 실험 · 검증

- CQ7.1 벤치마크는 무엇이고 노드 수는? 왜 Online Boutique인가? → `OnlineBoutique`, `nodeCount`
- CQ7.2 라벨은 어떻게 생성되는가(자동 역라벨링)? → `LabelingStrategy`
- CQ7.3 p̄(실패확률)와 u(불확실성)는 각각 무엇으로 검증되는가? → `ECE`(p̄) / `DropRate`(u)
- CQ7.4 baseline 비교군과 ablation은 무엇인가? → `Baseline`, `Ablation` 개체
- CQ7.5 어떤 조치를 시간 부족 시 먼저 컷하는가? → `Actuator` 개체 주석(cut priority)

## CQ8. 선행연구 · 차별점

- CQ8.1 GRAF/FIRM/AGQ/GraphGRU 각각이 세 비교축 중 무엇을 비우고 있는가? → `coversAxis` / `lacksAxis`
- CQ8.2 본 연구가 채우는 빈자리는 무엇인가? → `ConfidenceTieredResponse` = 유일하게 모두가 비운 축
- CQ8.3 FIRM의 조치 공간은 왜 전부 프로비저닝인가(비프로비저닝 조치가 신규 기여)? → `FIRM` 개체 주석 D14
- CQ8.4 각 선행연구의 예측 모델·조치공간·venue·검증 그래프 규모는? → `RelatedWork` 데이터 속성

## CQ9. 심사 방어

- CQ9.1 각 심사 우려(6~13)는 어떤 설계 요소로 방어되는가? → `Concern` `addressedBy` `DesignDecision`
- CQ9.2 "작은 벤치마크" 우려는 무엇으로 방어되는가? → 우려6 → 선례+스코프+공유head 확장성
