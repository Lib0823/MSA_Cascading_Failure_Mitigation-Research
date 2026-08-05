# 클래스 (Classes) — 개념 분류 체계

> `msacf:` 네임스페이스. 계층은 `⊑`(rdfs:subClassOf)로 표기. 각 클래스는 정의와 근거 절을 붙였다.
> 형식 정의는 [ontology.ttl](ontology.ttl) 참고.

## 0. 최상위 구분

```
msacf:Entity
├── SystemConcept        (시스템·위상·리소스)
├── FailureConcept       (장애·전파)
├── PredictionConcept    (예측 모델·불확실성·출력)
├── DecisionConcept      (Policy Engine·비용함수·신뢰도 구간)
├── ActuationConcept     (조치·제어 계층)
├── ExperimentConcept    (벤치마크·라벨·지표·baseline)
├── RelatedWorkConcept   (선행연구·비교축·기여)
└── DefenseConcept       (심사 우려·설계 결정)
```

---

## 1. SystemConcept — 시스템 · 위상

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `Microservice` | SystemConcept | 서비스 호출 그래프의 노드 `v ∈ V`. 리소스 지표를 feature로 갖는다. | §2-0 |
| `ServiceCallGraph` | SystemConcept | 시점 t의 방향 그래프 `G_t=(V,E)`. **위상은 정적**. | §2-0, 우려13 |
| `CallDependency` | SystemConcept | 방향성 호출 관계 엣지 `E ⊆ V×V`. | §2-0 |
| `Resource` | SystemConcept | 서비스가 의존하는 자원. | §1 |
| `StatefulResource` | Resource | 상태성 리소스(DB 커넥션풀·Redis 등). 스케일아웃이 역효과를 낼 수 있음. | §1-2, D2 |
| `StatelessResource` | Resource | 무상태 리소스(CPU·메모리 등). 프로비저닝으로 완화 가능. | §2-B |
| `ConnectionPool` | StatefulResource | 대표 상태성 병목. `cartservice→Redis` 경로에 구체화. | D2, §4-5 |
| `SLO` | SystemConcept | 서비스 수준 목표(P99 지연·에러율 임계). 위반이 라벨 기준. | §4-2 |
| `Topology` | SystemConcept | 서비스 간 논리적 호출 위상. 예측의 핵심 신호. | §1-3 |

> **핵심 제약**: `ServiceCallGraph` 는 정적. Pod 증감 등 물리적 변화는 그래프 구조가 아니라
> `NodeFeature` 로 흡수된다(우려13). 위상 변경은 fine-tune으로 대응(실험 밖 논거).

---

## 2. FailureConcept — 장애 · 전파

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `FailureEvent` | FailureConcept | 장애 사건 일반. | §1 |
| `CascadingFailure` | FailureEvent | 의존관계를 타고 번지는 연쇄 장애. 본 연구의 대상. | §1 |
| `SLOViolation` | FailureEvent | P99 지연>임계 또는 에러율>임계 상태. positive 라벨 기준. | §4-2 |
| `ThunderingHerd` | FailureEvent | 상태성 병목에서 스케일아웃이 상태를 악화시키는 현상. | §1-2, D2 |
| `PropagationPath` | FailureConcept | 상류 서비스 열화가 하류로 전파되는 경로. 위상으로만 예측 가능. | §4-2 refinement |
| `FirstOrderFault` | FailureConcept | 랜덤 주입된 1차 장애 onset. 입력에 선행신호 없어 positive에서 제외/유예. | §4-2, E6-3 |
| `FaultInjection` | FailureConcept | 장애주입 행위(지연/에러/리소스 stress). Istio/Chaos Mesh로 수행. | §4-2, A4 |

---

## 3. PredictionConcept — 예측 모델 · 불확실성 · 출력

### 3.1 모델 계열

```
PredictionModel
├── GNN                         (위상 반영)
│   ├── GAT        ← 본 연구 채택 (attention 차등 집계)
│   ├── GCN        ← 기각 (이웃 균등)
│   ├── GraphSAGE  ← 기각 (대규모 inductive, 본 벤치마크엔 불필요)
│   ├── MPNN       ← GRAF (이웃 균등 합산)
│   └── STGNN      ← AGQ (ChebConv+시간축, 추론지연으로 미채택)
├── LSTM                        (위상 미반영, baseline)
└── SVM                         (위상 미반영, FIRM)
```

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `PredictionModel` | PredictionConcept | 위험/자원을 예측하는 학습 모델 일반. | §2-A |
| `GNN` | PredictionModel | 그래프 신경망. 위상을 반영. | §2-A |
| `GAT` | GNN | Graph Attention Network. 이웃별 차등 가중(attention). **최종 채택**. | §2-A, E1 |
| `GCN` | GNN | 이웃 균등 집계. 기각 비교 대상. | E1 |
| `GraphSAGE` | GNN | 고정 크기 이웃 샘플링 inductive. 기각 비교 대상. | 참고문헌 |
| `MPNN` | GNN | Message Passing NN. GRAF가 사용(이웃 균등). | D6 |
| `STGNN` | GNN | Spatio-Temporal GNN. AGQ 사용. 추론지연으로 구조는 미채택. | E4 |
| `LSTM` | PredictionModel | 시계열 전용. 위상 부재 대표 모델, baseline 3. | §4-3, 참고문헌 |
| `SVM` | PredictionModel | FIRM의 감지/localize 분류기. 위상 미반영. | D4 |

### 3.2 불확실성 · 앙상블

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `UncertaintyMethod` | PredictionConcept | 예측 불확실성 정량화 기법. | F1 |
| `DeepEnsemble` | UncertaintyMethod | 서로 다르게 초기화한 N개 모델의 분산으로 불확실성 추정. **N=5 채택**(병렬추론). | F1, §2-A |
| `MCDropout` | UncertaintyMethod | 추론 시 Dropout 유지 반복 순전파. 반복추론 지연으로 기각. | F1, 참고문헌 |
| `SoftmaxEntropy` | UncertaintyMethod | softmax 엔트로피. OOD 과신 경향으로 기각. | F1 |

### 3.3 feature · readout · 출력

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `NodeFeature` | PredictionConcept | 노드 입력 벡터 `x_v(t) ∈ ℝ^d`. **self-SLO 위반 이력 제외**(누수/지름길). | §2-0, §4-2, H5 |
| `ResourceMetricFeature` | NodeFeature | 현재 CPU/메모리/지연/에러율/스레드풀·커넥션풀 지표. | §4-2 |
| `TemporalStatisticFeature` | NodeFeature | 슬라이딩 윈도우 통계(평균·slope·std). **t 이전만**(라벨 누수 방지). TA-GAT의 실체. | §2-A, §4-2 |
| `Readout` | PredictionConcept | 노드 임베딩 → 예측 출력 변환. | §2-A |
| `SharedPerNodeHead` | Readout | 모든 노드에 동일 MLP 적용. 파라미터가 노드 수와 무관. **채택**. | §2-A, E5 |
| `FlattenReadout` | Readout | 노드 임베딩 flatten → FC. 파라미터가 노드 수에 선형. GRAF, 확장성 한계. | D13 |
| `PredictionOutput` | PredictionConcept | 노드별 출력. 두 축으로 분리. | §2-0 |
| `FailureProbability` | PredictionOutput | `p̄_v = mean_i p_v^(i)`. P(위반) 점추정. ECE로 검증. | §2-0, §4-4 |
| `Uncertainty` | PredictionOutput | `u_v = Var_i p_v^(i)`. 앙상블 불일치(에피스테믹). **신뢰도=낮은 u**. Drop Rate로 검증. | §2-0, §4-4 |

> **TA-GAT 주의**: 신규 모델이 아니라 **feature 설계**다(브랜딩 라벨). 핵심 기여는 어디까지나
> 신뢰도 구간별 대응. 온톨로지에서도 `GAT` 하위 클래스가 아니라 `TemporalStatisticFeature` 로 표현한다.

---

## 4. DecisionConcept — Policy Engine · 비용함수 · 신뢰도 구간

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `PolicyEngine` | DecisionConcept | 조치 집합 `A∪{보류}` 위에서 기대비용 최소 조치를 선택. **핵심 기여**. | §2-B, §2-E |
| `CostFunction` | DecisionConcept | 불확실성 하 기대비용. `E[비용\|a]=p·[(1−mₐ)L+Dₐ]+(1−p)·[Dₐ·Rₐ]`. | §2-B, G1 |
| `EffectiveRisk` | DecisionConcept | 신뢰도 반영 위험도 `p_eff=max(0, p̄_v−κ√u_v)`. 비용식의 p. | §2-0, G4 |
| `ActionThreshold` | DecisionConcept | 조치별 유도 임계값 `θₐ=Dₐ·Rₐ/(mₐ·L−Dₐ(1−Rₐ))`. `p_eff>θₐ`면 조치. | §2-B |
| `ConfidenceTieredResponse` | DecisionConcept | 신뢰도 구간별 조치 강도 차등. **문헌상 빈자리(신규 기여)**. | §2-B, §3 |
| `ConfidenceTier` | DecisionConcept | 고/중/저 구간. 손으로 긋지 않고 θₐ에서 **유도**. | §2-B |
| `SafetyGuard` | DecisionConcept | 저신뢰(u↑→p_eff↓) 시 극단조치 자동 보류. 비용함수에서 **유도**(하드코딩 아님). | §2-B, H3, G4 |
| `CostVariable` | DecisionConcept | 비용함수 변수(L·Dₐ·Rₐ·mₐ·κ·θₐ). 개체는 individuals.md. | §2-B |
| `Withhold` | DecisionConcept | 보류(조치 안 함). 저위험·저신뢰 노드가 귀결되는 곳. | §2-B, §2-E |

---

## 5. ActuationConcept — 조치 · 제어 계층

### 5.1 조치(Actuator)

```
Actuator
├── CircuitBreaker    (비프로비저닝, Tier1+Tier2)
├── TrafficShedding   (비프로비저닝, Tier1+Tier2)
├── ReadRedirection   (비프로비저닝, Tier2, 상태성 병목 완화)
├── K8sScaleUp        (프로비저닝, Tier2, 상태성 병목서 역효과)
└── Brownout          (비프로비저닝, Tier2, 품질 저하)
```

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `Actuator` | ActuationConcept | 실행 계층 조치. disruption·가역성·완화효과·전파속도·신뢰도구간 속성을 가짐. | §2-C |
| `CircuitBreaker` | Actuator | 호출 차단. 즉시성 높음. Resilience4j `FORCED_OPEN`. | §2-C, §2-D |
| `TrafficShedding` | Actuator | 요청 통째 거부. | §2-C |
| `ReadRedirection` | Actuator | 읽기를 read-replica로 우회. **기능 유지+일관성 약화(stale read)**. Envoy `read_policy`. | §2-C, H2 |
| `K8sScaleUp` | Actuator | Pod 증설(프로비저닝). 상태성 병목서 Thundering Herd 유발. | §2-C |
| `Brownout` | Actuator | 비핵심(optional) 기능을 dimmer로 생략. `adservice`/`recommendationservice`. | §2-C, D14 |

> **조치 이질성 축**: `ReadRedirection`(기능유지+일관성약화) ≠ `Brownout`(기능생략) ≠ `TrafficShedding`(요청거부).
> `isProvisioning` 으로 `K8sScaleUp`(프로비저닝) vs 나머지 4종(비프로비저닝) 구분 — FIRM 대비 신규 기여의 근거.

### 5.2 제어 계층

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `ControlTier` | ActuationConcept | 조치 트리거 경로. | §2-D |
| `LocalReflexTier` | ControlTier | Tier1. 자기 호출통계만 보고 ms 반응. CB·Shedding 상시 floor. | §2-D |
| `GNNProactiveTier` | ControlTier | Tier2. 위상 전체 예측, N초 주기, 신뢰도 구간별 5종 선택. | §2-D |
| `ConflictResolution` | ActuationConcept | 두 계층이 같은 액추에이터를 건드릴 때의 중재. | §2-D |
| `ORSemantics` | ConflictResolution | 둘 중 하나라도 '조치'면 조치(안전 방향). | §2-D |
| `EscalateOnly` | ConflictResolution | GNN은 로컬 '정상'을 되돌리지 않음(tug-of-war 방지). | §2-D |
| `Lease` | ConflictResolution | GNN 명령은 시한부(TTL 1~2주기). 미갱신 시 자동 만료. | §2-D |

---

## 6. ExperimentConcept — 벤치마크 · 라벨 · 지표

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `Benchmark` | ExperimentConcept | 실험 대상 MSA 벤치마크. | §4-1 |
| `GroundTruthLabel` | ExperimentConcept | 노드 레벨·SLO위반·전파성 refine된 정답 라벨 `y_v(t)∈{0,1}`. | §4-2 |
| `LabelingStrategy` | ExperimentConcept | 자동 역라벨링(미래 지표로 역산, 수작업 없음). | §4-2 |
| `PredictionHorizon` | ExperimentConcept | 예측 지평 Δ(단일 고정). 하한=폐루프 응답시간. | §4-2, E6 |
| `EvaluationMetric` | ExperimentConcept | 평가 지표. | §4-4 |
| `ECE` | EvaluationMetric | Expected Calibration Error. **p̄(실패확률) 보정** 검증. reliability diagram 병기. | §4-4, H3 |
| `DropRate` | EvaluationMetric | 불필요 Shedding으로 인한 정상요청 차단율. **u(신뢰도 대응) 효과** 정량화. | §4-4, H3 |
| `Baseline` | ExperimentConcept | 비교군(HPA·규칙·LSTM·GRAF류·FIRM류). | §4-3 |
| `Ablation` | ExperimentConcept | 스냅샷 전용 GAT vs TA-GAT 분리 측정. | §4-3 |
| `TrafficProfile` | ExperimentConcept | 정상/버스트/점진증가 부하. Locust/k6. | §4-5, A4 |
| `MitigationEffectMeasurement` | ExperimentConcept | mₐ를 (장애유형×조치) 대조로 추정, 조치 전후 SLO 회복분 대리지표. | §4-5 |
| `Tool` | ExperimentConcept | 실험 도구(Locust/Istio/Chaos Mesh/Envoy/Resilience4j/PyG). | §4, A4 |

---

## 7. RelatedWorkConcept — 선행연구 · 비교축

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `RelatedWork` | RelatedWorkConcept | 선행연구(GRAF/FIRM/AGQ/GraphGRU). 개체는 individuals.md. | §3 |
| `ComparisonAxis` | RelatedWorkConcept | 비교 3축(위상인지 예측/조치공간 이질성/신뢰도 구간 대응). | §3 |
| `Contribution` | RelatedWorkConcept | 본 연구 기여. 세 축 교집합 = 신뢰도 구간별 대응. | §3 |

---

## 8. DefenseConcept — 심사 우려 · 설계 결정

| 클래스 | ⊑ | 정의 | 근거 |
|---|---|---|---|
| `Concern` | DefenseConcept | 심사 예상 우려(6~13). 개체는 individuals.md. | §6 |
| `DesignDecision` | DefenseConcept | 우려를 방어하는 설계 결정. | challenges D~H |
