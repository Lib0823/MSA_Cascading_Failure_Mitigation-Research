# 개체 (Individuals) — 인스턴스

> 클래스의 실제 인스턴스. 조치 5종·선행연구 4편·벤치마크·비용변수·지표·도구·우려·비교축 등.
> 형식 정의는 [ontology.ttl](ontology.ttl).

## 1. 조치 5종 (Actuator) — §2-B 표 그대로

| 개체 | 클래스 | disruption | reversibility | mitigationEffect | 전파속도 | θₐ | 신뢰도구간 | isProvisioning |
|---|---|---|---|---|---|---|---|---|
| `CircuitBreaker_i` | CircuitBreaker | 낮음 | 낮음(auto half-open) | 높음 | 빠름 | 낮음 | 중간에서도 발동 | false |
| `DegradedPathRedirection_i` | DegradedPathRedirection | 낮음 | 낮음 | 중 | 중 | 낮음~중 | 중간에서도 발동 | false |
| `Brownout_i` | Brownout | 중 | 낮음(dimmer 원복) | 중~높음 | 중 | 낮음~중 | 중간에서도 발동 | false |
| `TrafficShedding_i` | TrafficShedding | 중 | 낮음~중 | 중~높음 | 빠름 | 중 | 중~고 | false |
| `K8sScaleUp_i` | K8sScaleUp | 높음 | 높음(스케일다운·상태복구 김) | 상황의존 | 느림 | 높음 | 고신뢰도만 | **true** |

**LLM 노드 구현체**(같은 조치, 다른 노드 타입 — `Dₐ`·`Rₐ`·`mₐ`가 달라 `θₐ`도 달라진다):

| 개체 | 상위 조치 | disruption | reversibility | mitigationEffect | θₐ | appliesAt |
|---|---|---|---|---|---|---|
| `ModelDowngrade_i` | DegradedPathRedirection | 중(품질 저하) | 낮음(즉시 원복) | 중~높음 | 낮음~중 | **v 자신** |
| `TokenBudgetCut_i` | Brownout | 중 | 낮음 | 중 | 낮음~중 | **v 자신** |
| `InferenceCapacityUp_i` | K8sScaleUp | 높음(GPU 점유) | 높음(재기동 리드타임) | 상황의존 | 높음 | **v 자신** |

> `CircuitBreaker`·`TrafficShedding`은 LLM 노드에서도 세 파라미터가 실질적으로 같아(요청을 막거나 버리는 비용은 노드 타입과 무관) 별도 개체를 두지 않는다.

**질적 이질성**(개체 주석):
- `DegradedPathRedirection_i`: **경로 변경**. 일반 서비스는 read-replica 우회(기능 유지 + 일관성 약화, stale read), LLM 노드는 소형 모델 라우팅. 1차 실증 대상은 **Postgres primary/replica** — Envoy `read_policy`가 Redis Cluster 전제라 원본 변형이 필요하기 때문(B6).
- `Brownout_i`: **작업량 축소**. 일반 서비스는 비핵심 기능 생략(`adservice`/`recommendationservice` dimmer), LLM 노드는 `max_tokens` 상한·RAG top-k 축소.
- `TrafficShedding_i`: 요청 통째 **거부**.
- `K8sScaleUp_i`: 상태성 병목(ConnectionPool)에서 Thundering Herd **유발** → `inducedBy` 역효과. LLM 노드에서는 KV 캐시 포화 시 `max_num_seqs` 증대가 preemption을 늘려 **같은 역효과**를 재현.
- `Withhold_i`(보류): 저위험·저신뢰 노드가 귀결. Safety Guard가 여기로 보냄.

**적용 지점**(`apply_point(a,v)` — 조치 종류 × 노드 타입의 함수, E9):

| 조치 | 일반 서비스 | LLM 노드 |
|---|---|---|
| CircuitBreaker | v의 호출자 | v의 호출자(frontend) |
| TrafficShedding | v의 인그레스 | v의 인그레스 |
| DegradedPathRedirection | 백엔드를 호출하는 서비스 | **v 자신**(요청 파라미터) |
| Brownout | frontend(optional 호출 생략) | **v 자신**(요청 파라미터) |
| K8sScaleUp | v 자신 | v 자신 |

**컷 우선순위**(timeline): 시간 부족 시 `DegradedPathRedirection`+`CircuitBreaker`+`Brownout` **3종**만 실증, 나머지(`K8sScaleUp`/`TrafficShedding`)는 "확장 가능 설계". 3종을 남기는 이유는 **성격이 서로 다르기 때문**(경로 변경 / 차단 / 품질 저하) — 축소 후에도 이질성의 최소 실증이 성립한다.

---

## 2. 선행연구 (RelatedWork) — 3분할 (D20)

### 2-1. 비교 대상 5편 (ComparisonTarget) — §3-1 비교표

| 개체 | usesModel | actionSpaceKind | coversAxis | lacksAxis | venue | verifiedGraphSize |
|---|---|---|---|---|---|---|
| `GRAF` | MPNN | 자원할당(스칼라) | 위상인지예측 | 조치이질성, **신뢰도구간** | CoNEXT'21/ToN'24 | 6~10노드 |
| `FIRM` | SVM(+RL) | 자원재할당 5종(전부 프로비저닝) | 조치적응선택 | 위상인지, 조치이질성, **신뢰도구간** | OSDI'20 | 15~41(비GNN) |
| `DeepScaler` | STGNN(attention GCN + adaptive graph learning) | 자원 프로비저닝(상호작용 서비스 동시 재구성) | 위상인지예측 | 조치이질성, **신뢰도구간** | ASE'23 | Bookinfo/**Online Boutique(10)**/Train-Ticket |
| `AGQ` | STGNN(ChebConv)+Q-learning | 자원할당(스칼라) | 위상인지, 조치적응 | 조치이질성, **신뢰도구간** | FGCS'26 | ~13노드 |
| `GraphGRU` | GAT(DTW 동적그래프) | 없음(예측만) | 위상인지예측 | 조치, **신뢰도구간** | ICPADS'22 | 알리바바 프로덕션 |

**공통 빈자리**: 다섯 개체 모두 `lacksAxis ConfidenceTieredResponse` → 본 연구가 채우는 유일 축.

### 2-2. 활용 기법 (UtilizedTechnique) — §3-2, D17

| 개체 | venue | 역할 |
|---|---|---|
| `FrugalGPT` | **TMLR 2024** | 응답 신뢰도 미달 시 상위 모델로 escalate하는 캐스케이드. `DegradedPathRedirection` 구현 근거. **프리프린트 아님** |
| `RouteLLM` | ICLR 2025 | 선호도 학습 라우터가 강/약 모델 이진 선택. 같은 근거 |

> 두 논문 모두 판단 근거가 **질의 난이도·응답 품질**이며 시스템 부하를 쓰지 않는다 — "언제 발동할 것인가" 축이 비어 있다. 경쟁 논문이 아니라 활용 기법으로 처리해 라우팅 효과를 직접 실증할 부담을 제거한다.

### 2-3. 인접 연구 (AdjacentWork) — §3-3

| 개체 | venue | 겹치는 지점 | 구분선 |
|---|---|---|---|
| `HW_Router` | DAC | 시스템 상태를 보고 라우팅 | 관측범위(단일 LLM 서비스 내부) / 반응형 / 조치 라우팅 단일 / 신뢰도 없음 (D18) |
| `GraphRouter` | ICLR 2025 | 그래프 + 라우팅 | 그래프 정의가 **질의–모델 적합도**이지 서비스 호출 토폴로지가 아님 (D19) |
| `ORACL` | IEEE TSC 2026 | **LLM으로 오토스케일링 의사결정** | 의사결정 주체(LLM 추론 vs 비용함수 argmin) / 조치 자원할당 단일 / 신뢰도 없음 / 재현성 / **LLM의 위치(의사결정 도구 vs 검증 대상 노드)** (D21) |
| `MicroRemed` | 벤치마크 | LLM remediation 능력 평가 | 경쟁 아님. 최저 난이도에서도 단독 LLM 50% 미만 — 방향 (A) 기각의 **외부 근거** (D21) |

> `Graph_PHPA`(CloudNet 2022, LSTM+GNN 선제적 HPA): 비교 대상 5편과 같은 위치이나 venue·검증 범위가 좁아 **비교표 미편입**(D22). LSTM과 GNN을 결합했다는 점이 본 연구가 LSTM을 독립 baseline으로 두는 설계의 대비 근거.

**개체 주석**:
- `FIRM`: 조치공간=CPU/Mem/LLC/IO/Net 재할당+수평스케일, **전부 프로비저닝**(브라운아웃·CB 없음, D14 정정). 마이크로서비스별 RL 에이전트 **전이학습** 사용 → 단 GNN 아님(SVM+RL), `precedentFor` 위상변경 대응(우려8)으로만 인용.
- `GRAF`: readout에서 노드 임베딩 flatten → 확장성 한계 자인(ToN'24 Discussion, D13). GNN 결정지연 아님 — configuration solver 수렴 90%tile ≈ **6.7초**(우려7 기준선).
- `AGQ`: "수백 노드" 실험은 비공개·비재현 시뮬레이션(LinkedIn 참고), 메인 결과 아님(B5).
- `GraphGRU`: MSA+GAT 최직접 선례이나 DTW 동적그래프 + 예측에서 그침.

---

## 3. 비교축 (ComparisonAxis) — §3

| 개체 | 정의 | 채우는 연구 |
|---|---|---|
| `Axis_TopologyAwarePrediction` | 예측 모델이 위상을 반영하는가 | GRAF, AGQ, GraphGRU |
| `Axis_ActionSpaceHeterogeneity` | 조치가 질적으로 이질적인가(프로비저닝+비프로비저닝) | 본 연구 단독 |
| `Axis_ConfidenceTieredResponse` | 신뢰도 구간별 조치 강도 차등 | **본 연구 단독(신규)** |

`Contribution_Main` `fills` 세 축 교집합 = FIRM류(적응조치) ∩ GRAF류(위상예측) + `Axis_ConfidenceTieredResponse`.

---

## 4. 벤치마크 (Benchmark)

| 개체 | nodeCount | 용도 |
|---|---|---|
| `OnlineBoutique` | 11~12 (확장 구성 **13~14**) | **메인**. GRAF·DeepScaler와 동일(직접 비교 근거). 원본 위상 보존 + Spring/Postgres 노드 1개 + LLM 노드(`assistantservice`) 1개 추가. |
| `SockShop` | 11~15 | 참고(AGQ 메인 규모). |
| `TrainTicket` | 40~64 | 보조 실험(서브셋 15~25, VPS 단기 대여). |
| `DeathStarBench` | 15~41 | FIRM이 사용(참고). |

`OnlineBoutique` 개체 주석:
- `cartservice hasBackingStore Redis` — replica 개념은 성립하나 Envoy `read_policy`가 **Redis Cluster 전제**라 원본 변형이 필요. 1차 상태성 병목으로 유지하되 Redirection 실증 대상에서는 2순위(B6).
- `productcatalogservice hasBackingStore 로컬JSON` — 복제본 불가 → Redirection 타깃 아님(H2 교정). **Go 원본 유지**.
- `orderservice(가칭) hasBackingStore PostgreSQL` — Spring + HikariCP. **Redirection 1차 실증 대상**이자 §5 "Thread-per-request·Java·Spring 한정" 스코프를 실제로 충족시키는 노드(B6). 명칭·배치·API 미확정.
- `assistantservice hasNodeType LLMInferenceNode` — frontend 하위 + productcatalog 상위. 2티어 캐스케이드(주/폴백). 하위 호출은 결정적 파이프라인으로 구현(B7).

---

## 5. 비용함수 변수 (CostVariable) — §2-B

| 개체 | symbol | 의미 | tunedInExperiment |
|---|---|---|---|
| `Var_L` | L | 가용성 손실(미조치 시 연쇄장애 피해). 전역 1개. | true |
| `Var_D` | Dₐ | 조치 disruption(즉시 비용). 조치별. | true |
| `Var_R` | Rₐ | 가역성/되돌리기 난이도(0~1). 조치별. | true |
| `Var_m` | mₐ | 완화효과(0~1, L 막는 비율). 장애유형 의존. | true(§4-5 대조실험) |
| `Var_kappa` | κ | 불확실성 회피 강도. κ=0이면 순수 기대비용. | true |
| `Var_theta` | θₐ | 유도 임계값 `Dₐ·Rₐ/(mₐ·L−Dₐ(1−Rₐ))`. | 유도값 |

**커넥션풀 차별점**: 상태성 병목에서 `Var_m`(K8sScaleUp) 급락 → θ_scaleup 분모 음수 → 비용함수가 ScaleUp 자동 배제, CB/Redirection 선택.

---

## 6. 평가 지표 (EvaluationMetric) — §4-4

| 개체 | validates | 역할 |
|---|---|---|
| `ECE_metric` | FailureProbability(p̄) | 보정 검증(모델이 "p"라 할 때 실제 그 비율로 위반?). reliability diagram 병기, 클래스 불균형 주의. |
| `DropRate_metric` | Uncertainty(u) | 신뢰도 대응 ON/OFF 시 불필요 Shedding 감소율. **핵심 기여 정량화**. |
| `TPS_retention` | — | TPS 유지율(평균±표준편차). |
| `P99_latency` | — | 테일 레이턴시(P50/P90/P99). |

---

## 7. Baseline · Ablation — §4-3

| 개체 | 클래스 | 검증 대상 |
|---|---|---|
| `BL_ReactiveHPA` | Baseline | 반응형 HPA만. |
| `BL_RuleBased` | Baseline | 규칙 기반 임계치 Policy. |
| `BL_LSTM` | Baseline | LSTM+동일 Policy Engine. **위상 인지 기여 검증**(핵심). |
| `BL_GRAFlike` | Baseline | 자원할당/스케일업만. 커넥션풀서 역효과 실증. |
| `BL_FIRMlike` | Baseline | 조치공간 동일+위상 미반영 예측기. 2×2 설계 완성(선택). |
| `Abl_NoConfidence` | Ablation | 동일 GNN 예측 + `θₐ` 게이팅 없이 `p̄` argmin. **신뢰도 축 단독 기여 분리** — 유일한 신규 축이라 컷라인 최후순위. |
| `Abl_VanillaCoverage` | Ablation | LLM 노드 없는 원본 구성. **성능 비교가 아니라 시나리오 커버리지 대조** — 확장 구성 학습 모델을 양쪽에 적용해 "S2가 vanilla에서 재현되지 않음"을 보인다. |
| `Abl_SnapshotVsTAGAT` | Ablation | 스냅샷 전용 GAT vs TA-GAT. 시계열 통계 기여 분리(LSTM 시퀀스 초과 안 하게 통제). |

---

## 8. 제어 계층 개체 — §2-D

| 개체 | 클래스 | 소속 조치 |
|---|---|---|
| `Tier1_LocalReflex` | LocalReflexTier | CircuitBreaker, TrafficShedding(상시 floor, ms). |
| `Tier2_GNNProactive` | GNNProactiveTier | 5종 전부(N초 주기, escalate+lease). |
| `CR_OR` | ORSemantics | 둘 중 하나라도 조치면 조치. |
| `CR_EscalateOnly` | EscalateOnly | GNN은 로컬 '정상' 안 되돌림. |
| `CR_Lease` | Lease | TTL 1~2주기, 미갱신 자동 만료. |

---

## 9. 도구 (Tool)

| 개체 | 용도 |
|---|---|
| `Locust` / `k6` | 트래픽 프로파일 생성(정상/버스트/점진증가). |
| `Istio` | 장애주입(HTTP/gRPC) + Envoy 사이드카. |
| `ChaosMesh` | 장애주입(지연/에러/리소스 stress). |
| `Envoy` | Redis proxy `read_policy`. **원문 확인 결과 Redis Cluster 전제**라 단일 인스턴스 `redis-cart`에는 원본 변형이 필요 — Redirection 1차 대상은 Postgres replica로 이동(B6). |
| `Resilience4j` | Tier1 로컬 CB(`FORCED_OPEN`)·Shedding. |
| `vLLM` | LLM 2티어 서빙. KV 캐시 점유율·TTFT/TPOT 메트릭 노출(노드 피처 f1·f4 소스). Blackwell sm_120은 소스 빌드 필요. |
| `PyTorchGeometric` / `DGL` | GAT+Deep Ensemble 구현. |

---

## 10. 심사 우려 (Concern) — §6

| 개체 | 우려 | addressedBy |
|---|---|---|
| `Concern1` | 왜 작은 벤치마크? | 선행 전례 + 의도적 스코프 + 공유 per-node head 확장성(D13) |
| `Concern2` | FIRM과 뭐가 다른가? | 위상 미반영 + 신뢰도구간 없음 + 비프로비저닝 조치(D14) |
| `Concern3` | 왜 정적 GAT(시계열 결합 아님)? | 문제정의 다름(분류/신뢰도) + TA-GAT feature 보강 + ablation |
| `Concern4` | 왜 Deep Ensemble(MC Dropout 아님)? | Lakshminarayanan 2017 + 병렬추론 지연 |
| `Concern5` | 그래프 커지면 학습 오래? | GAT sparse O(N+E) + 공유head + 진짜병목=실측수집 |
| `Concern6` | 즉각조치를 느린 GNN에 묶으면? | 2계층(Tier1 반사) |
| `Concern7` | N=5 앙상블이 골든타임 안에? | 골든타임에 GNN 없음(Tier2) + GRAF 6.7s 기준선 + 병렬 |
| `Concern8` | 정적 위상이 변하는 MSA에 유효? | 논리위상 안정 + feature 흡수 + fine-tune(FIRM 선례) |

**LLM 노드 확장 관련 (9~14)** — B7 채택으로 신설:

| 개체 | 우려 | addressedBy |
|---|---|---|
| `Concern9` | LLM을 넣은 게 유행 편승 아닌가? | LLM은 **검증 대상**이지 기여 아님 + 세 가정 위반(§1) + `Abl_VanillaCoverage` 커버리지 대조 |
| `Concern10` | 모델 라우팅은 이미 있는 기법 아닌가? | 맞음 — `UtilizedTechnique`로 인용, 라우팅 자체를 기여로 주장 안 함(D17). 기여는 발동 시점 결정 |
| `Concern11` | HW-Router와 뭐가 다른가? | `HW_Router` 4축 구분(D18) + S3가 원리적으로 다룰 수 없는 영역 |
| `Concern12` | GraphRouter와 겹치지 않나? | 그래프 정의가 질의–모델 적합도 vs 서비스 호출 토폴로지(D19) |
| `Concern13` | 소형 모델 실험이 일반화되나? | 검증 대상은 티어 간 **상대적 격차**, 절대 규모는 환경 제약(§5 한정) |
| `Concern14` | LLM 응답 품질을 어떻게 측정했나? | 측정 안 함 — `Var_D`(D_downgrade)를 정책 파라미터로 두고 `θₐ` 교차점 제시(F4) |
| `Concern15` | LLM으로 오토스케일링을 결정하는 연구(ORACL)가 있는데 왜 GNN인가? | `distinguishedFrom` 5축 — 의사결정 주체·조치 공간·신뢰도 축·재현성·**LLM의 위치**(D21) |

> `Concern15`는 `ORACL`(D21)이 직접 제기하는 질문이다. 저자 인지도와 venue를 고려할 때 LLM 확장 관련 우려 중 **가장 제기 가능성이 높다**.
