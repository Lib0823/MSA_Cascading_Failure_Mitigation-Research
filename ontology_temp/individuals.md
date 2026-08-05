# 개체 (Individuals) — 인스턴스

> 클래스의 실제 인스턴스. 조치 5종·선행연구 4편·벤치마크·비용변수·지표·도구·우려·비교축 등.
> 형식 정의는 [ontology.ttl](ontology.ttl).

## 1. 조치 5종 (Actuator) — §2-B 표 그대로

| 개체 | 클래스 | disruption | reversibility | mitigationEffect | 전파속도 | θₐ | 신뢰도구간 | isProvisioning |
|---|---|---|---|---|---|---|---|---|
| `CircuitBreaker_i` | CircuitBreaker | 낮음 | 낮음(auto half-open) | 높음 | 빠름 | 낮음 | 중간에서도 발동 | false |
| `ReadRedirection_i` | ReadRedirection | 낮음 | 낮음 | 중 | 중 | 낮음~중 | 중간에서도 발동 | false |
| `Brownout_i` | Brownout | 중 | 낮음(dimmer 원복) | 중~높음 | 중 | 낮음~중 | 중간에서도 발동 | false |
| `TrafficShedding_i` | TrafficShedding | 중 | 낮음~중 | 중~높음 | 빠름 | 중 | 중~고 | false |
| `K8sScaleUp_i` | K8sScaleUp | 높음 | 높음(스케일다운·상태복구 김) | 상황의존 | 느림 | 높음 | 고신뢰도만 | **true** |

**질적 이질성**(개체 주석):
- `ReadRedirection_i`: 기능 **유지** + 일관성 일시 약화(stale read). Envoy `read_policy`(EnvoyFilter). 대상 `cartservice→Redis`.
- `Brownout_i`: 비핵심 기능 **생략**. `adservice`/`recommendationservice` dimmer.
- `TrafficShedding_i`: 요청 통째 **거부**.
- `K8sScaleUp_i`: 상태성 병목(ConnectionPool)에서 Thundering Herd **유발** → `inducedBy` 역효과.
- `Withhold_i`(보류): 저위험·저신뢰 노드가 귀결. Safety Guard가 여기로 보냄.

**컷 우선순위**(timeline): 시간 부족 시 `CircuitBreaker`+`ReadRedirection` 2종만 실증, 나머지는 "확장 가능 설계".
`Brownout`이 앱 계측 필요로 컷 1순위.

---

## 2. 선행연구 4편 (RelatedWork) — §3 비교표

| 개체 | usesModel | actionSpaceKind | coversAxis | lacksAxis | venue | verifiedGraphSize |
|---|---|---|---|---|---|---|
| `GRAF` | MPNN | 자원할당(스칼라) | 위상인지예측 | 조치이질성, **신뢰도구간** | CoNEXT'21/ToN'24 | 6~10노드 |
| `FIRM` | SVM(+RL) | 자원재할당 5종(전부 프로비저닝) | 조치적응선택 | 위상인지, 조치이질성, **신뢰도구간** | OSDI'20 | 15~41(비GNN) |
| `AGQ` | STGNN(ChebConv)+Q-learning | 자원할당(스칼라) | 위상인지, 조치적응 | 조치이질성, **신뢰도구간** | FGCS'26 | ~13노드 |
| `GraphGRU` | GAT(DTW 동적그래프) | 없음(예측만) | 위상인지예측 | 조치, **신뢰도구간** | ICPADS'22 | 알리바바 프로덕션 |

**공통 빈자리**: 네 개체 모두 `lacksAxis ConfidenceTieredResponse` → 본 연구가 채우는 유일 축.

**개체 주석**:
- `FIRM`: 조치공간=CPU/Mem/LLC/IO/Net 재할당+수평스케일, **전부 프로비저닝**(브라운아웃·CB 없음, D14 정정). 마이크로서비스별 RL 에이전트 **전이학습** 사용 → 단 GNN 아님(SVM+RL), `precedentFor` 위상변경 대응(우려13)으로만 인용.
- `GRAF`: readout에서 노드 임베딩 flatten → 확장성 한계 자인(ToN'24 Discussion, D13). GNN 결정지연 아님 — configuration solver 수렴 90%tile ≈ **6.7초**(우려12 기준선).
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
| `OnlineBoutique` | 11~12 | **메인**. GRAF와 동일(직접 비교 근거). cartservice→Redis 상태성 병목. |
| `SockShop` | 11~15 | 참고(AGQ 메인 규모). |
| `TrainTicket` | 40~64 | 보조 실험(서브셋 15~25, VPS 단기 대여). |
| `DeathStarBench` | 15~41 | FIRM이 사용(참고). |

`OnlineBoutique` 개체 주석: `cartservice hasBackingStore Redis`(replica 성립),
`productcatalogservice hasBackingStore 로컬JSON`(복제본 불가 → Read Redirection 타깃 아님, H2 교정).

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
| `Envoy` | Redis proxy `read_policy`(Read Redirection, read/write 분리 재확인 대상). |
| `Resilience4j` | Tier1 로컬 CB(`FORCED_OPEN`)·Shedding. |
| `PyTorchGeometric` / `DGL` | GAT+Deep Ensemble 구현. |

---

## 10. 심사 우려 (Concern) — §6

| 개체 | 우려 | addressedBy |
|---|---|---|
| `Concern6` | 왜 작은 벤치마크? | 선행 전례 + 의도적 스코프 + 공유 per-node head 확장성(D13) |
| `Concern7` | FIRM과 뭐가 다른가? | 위상 미반영 + 신뢰도구간 없음 + 비프로비저닝 조치(D14) |
| `Concern8` | 왜 정적 GAT(시계열 결합 아님)? | 문제정의 다름(분류/신뢰도) + TA-GAT feature 보강 + ablation |
| `Concern9` | 왜 Deep Ensemble(MC Dropout 아님)? | Lakshminarayanan 2017 + 병렬추론 지연 |
| `Concern10` | 그래프 커지면 학습 오래? | GAT sparse O(N+E) + 공유head + 진짜병목=실측수집 |
| `Concern11` | 즉각조치를 느린 GNN에 묶으면? | 2계층(Tier1 반사) |
| `Concern12` | N=5 앙상블이 골든타임 안에? | 골든타임에 GNN 없음(Tier2) + GRAF 6.7s 기준선 + 병렬 |
| `Concern13` | 정적 위상이 변하는 MSA에 유효? | 논리위상 안정 + feature 흡수 + fine-tune(FIRM 선례) |
