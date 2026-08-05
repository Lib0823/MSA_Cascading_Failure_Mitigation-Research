# 속성 (Properties) — 관계 · 데이터 속성

> 객체 속성(`owl:ObjectProperty`)은 개념 간 관계, 데이터 속성(`owl:DatatypeProperty`)은 개념의 값이다.
> `domain → range` 로 표기. 형식 정의는 [ontology.ttl](ontology.ttl).

## A. 객체 속성 (Object Properties)

### A.1 시스템 · 위상

| 속성 | domain → range | 의미 | 특성 |
|---|---|---|---|
| `calls` | Microservice → Microservice | A가 B를 호출(방향 의존). `CallDependency` 엣지. | irreflexive |
| `hasNode` | ServiceCallGraph → Microservice | 그래프가 노드를 포함. | |
| `hasEdge` | ServiceCallGraph → CallDependency | 그래프가 엣지를 포함. | |
| `dependsOnResource` | Microservice → Resource | 서비스가 리소스에 의존. | |
| `hasBottleneck` | Microservice → Resource | 병목 리소스. 상태성이면 스케일아웃 역효과. | |

### A.2 장애 · 전파

| 속성 | domain → range | 의미 |
|---|---|---|
| `propagatesTo` | FailureEvent → Microservice | 장애가 하류 서비스로 전파. `PropagationPath` 형성. |
| `triggers` | FaultInjection → FailureEvent | 주입이 장애를 유발. |
| `causesViolationIn` | PropagationPath → Microservice | 전파가 특정 노드의 SLO 위반을 야기(positive 라벨 조건). |
| `inducedBy` | ThunderingHerd → Actuator | 특정 조치(K8sScaleUp)가 Thundering Herd를 유발. |

### A.3 예측

| 속성 | domain → range | 의미 |
|---|---|---|
| `predictsRiskOf` | PredictionModel → Microservice | 모델이 노드 위험을 예측. |
| `hasFeature` | Microservice → NodeFeature | 노드가 입력 feature를 가짐. |
| `usesReadout` | GNN → Readout | 모델이 readout 방식을 사용. |
| `quantifiesUncertaintyVia` | PredictionModel → UncertaintyMethod | 불확실성 산출 기법. (GAT → DeepEnsemble) |
| `producesOutput` | PredictionModel → PredictionOutput | 모델이 출력을 생성. |
| `aggregatesTo` | PredictionOutput → EffectiveRisk | p̄·u가 p_eff로 집계. |
| `excludesFeature` | GroundTruthLabel → NodeFeature | 라벨과의 누수 방지를 위해 self-SLO feature 제외. |

### A.4 의사결정

| 속성 | domain → range | 의미 |
|---|---|---|
| `selects` | PolicyEngine → Actuator | 엔진이 조치를 선택(argmin). |
| `consumes` | PolicyEngine → EffectiveRisk | 엔진 입력으로 p_eff 사용. |
| `evaluatedBy` | Actuator → CostFunction | 조치가 비용함수로 평가됨. |
| `hasThreshold` | Actuator → ActionThreshold | 조치별 유도 임계값 θₐ. |
| `hasCostVariable` | CostFunction → CostVariable | 비용함수가 변수를 가짐. |
| `derivedFrom` | SafetyGuard → CostFunction | Safety Guard는 비용함수에서 유도(하드코딩 아님). |
| `derivesTier` | CostFunction → ConfidenceTier | 신뢰도 구간이 θₐ에서 유도됨. |
| `mitigates` | Actuator → FailureEvent | 조치가 장애를 완화. |
| `targetsResource` | Actuator → Resource | 조치 대상 리소스. (ReadRedirection → ConnectionPool) |

### A.5 제어 계층

| 속성 | domain → range | 의미 |
|---|---|---|
| `triggeredBy` | Actuator → ControlTier | 조치의 트리거 경로(Tier1/Tier2). 다대다. |
| `resolvesConflictVia` | ControlTier → ConflictResolution | 계층 충돌 중재 방식. |
| `hasLease` | GNNProactiveTier → Lease | GNN 명령의 시한부 TTL. |

### A.6 실험 · 검증

| 속성 | domain → range | 의미 |
|---|---|---|
| `runsOn` | PredictionModel → Benchmark | 모델이 벤치마크 위에서 검증됨. |
| `labeledBy` | Benchmark → LabelingStrategy | 자동 역라벨링으로 라벨 생성. |
| `validates` | EvaluationMetric → PredictionOutput | 지표가 출력을 검증. (ECE→FailureProbability, DropRate→Uncertainty) |
| `comparedAgainst` | PolicyEngine → Baseline | 본 방식이 baseline과 비교됨. |
| `measures` | MitigationEffectMeasurement → CostVariable | mₐ 측정. |
| `usesTool` | ExperimentConcept → Tool | 실험이 도구를 사용. |
| `concretizesAt` | StatefulResource → Microservice | 상태성 병목이 cartservice→Redis에 구체화. |

### A.7 선행연구 · 방어

| 속성 | domain → range | 의미 |
|---|---|---|
| `usesModel` | RelatedWork → PredictionModel | 선행연구의 예측 모델. |
| `coversAxis` | RelatedWork → ComparisonAxis | 해당 축을 다룸. |
| `lacksAxis` | RelatedWork → ComparisonAxis | 해당 축을 비움(차별점). |
| `fills` | Contribution → ComparisonAxis | 본 연구가 채우는 축. |
| `precedentFor` | RelatedWork → DesignDecision | 선례로만 인용(FIRM 전이학습 → 위상변경 대응). |
| `addressedBy` | Concern → DesignDecision | 우려가 설계 결정으로 방어됨. |
| `defends` | DesignDecision → Contribution | 설계 결정이 기여를 방어. |

---

## B. 데이터 속성 (Datatype Properties)

### B.1 조치(Actuator) 특성 — §2-B 표

| 속성 | domain | range | 값 예 |
|---|---|---|---|
| `disruption` | Actuator | string(낮음/중/높음) | CB=낮음, ScaleUp=높음 |
| `reversibility` | Actuator | string | CB=낮음(auto half-open), ScaleUp=높음(원복 김) |
| `mitigationEffect` | Actuator | string | CB=높음, ScaleUp=상황의존 |
| `targetPropagationSpeed` | Actuator | string(빠름/중/느림) | CB=빠름, ScaleUp=느림 |
| `confidenceTierRequired` | Actuator | string | CB=중간에서도, ScaleUp=고신뢰도만 |
| `isProvisioning` | Actuator | boolean | K8sScaleUp=true, 나머지 4종=false |

### B.2 예측 모델

| 속성 | domain | range | 값 |
|---|---|---|---|
| `ensembleSize` | DeepEnsemble | integer | 5 |
| `attentionWeighting` | GNN | boolean | GAT=true, GCN/MPNN=false |
| `parameterScalesWithNodes` | Readout | boolean | FlattenReadout=true, SharedPerNodeHead=false |
| `temporalWindowBeforeT` | TemporalStatisticFeature | boolean | true (라벨 누수 방지) |

### B.3 선행연구 메타

| 속성 | domain | range | 값 예 |
|---|---|---|---|
| `venue` | RelatedWork | string | GRAF=CoNEXT'21/ToN'24, FIRM=OSDI'20 |
| `year` | RelatedWork | integer | |
| `actionSpaceKind` | RelatedWork | string | FIRM=자원 프로비저닝(5종), 본연구=이질(CB/Shed/Redirect/Scale/Brownout) |
| `verifiedGraphSize` | RelatedWork | string | GRAF=6~10노드, AGQ=~13, 본연구=11 |
| `credibility` | RelatedWork | string | FIRM>GRAF>AGQ>GraphGRU |
| `relevance` | RelatedWork | string | GraphGRU>AGQ>GRAF>FIRM |

### B.4 벤치마크 · 비용 변수

| 속성 | domain | range | 값 예 |
|---|---|---|---|
| `nodeCount` | Benchmark | integer | OnlineBoutique=11~12, SockShop=~13, TrainTicket=40~64 |
| `hasBackingStore` | Microservice | string | cartservice=Redis, productcatalog=로컬JSON(복제본 불가) |
| `symbol` | CostVariable | string | L, Dₐ, Rₐ, mₐ, κ, θₐ |
| `tunedInExperiment` | CostVariable | boolean | true(실측 이월) |

---

## C. 정합성 규칙(설계 불변식) — 온톨로지 검증용

온톨로지가 지켜야 하는 제약. SHACL/OWL 공리로 표현 가능하며, 위반 시 문서 불일치 신호.

1. `SafetyGuard derivedFrom CostFunction` 는 **필수** — 하드코딩 규칙으로 정의되면 위반(G4 철학).
2. `K8sScaleUp isProvisioning=true` 이고 나머지 Actuator는 `false` — FIRM 대비 신규 기여 근거.
3. 모든 `RelatedWork` 는 `ConfidenceTieredResponse` 축을 `lacksAxis` — 셋 다 비운 유일 축(신규 기여).
4. `TemporalStatisticFeature temporalWindowBeforeT=true` — 라벨창 [t,t+Δ]과 겹치면 누수(위반).
5. `GroundTruthLabel excludesFeature (self SLOViolation history)` — self-SLO feature 포함 시 위상 우회(위반).
6. `EffectiveRisk` = `max(0, p̄_v − κ√u_v)` 이고 비용식의 `p` 는 반드시 이 값 — p를 "1−분산"으로 두면 위반(G4 버그 정정).
7. `FailureProbability validates-by ECE`, `Uncertainty validates-by DropRate` — 두 축은 분리 검증(뭉뚱그리면 위반, H3).
