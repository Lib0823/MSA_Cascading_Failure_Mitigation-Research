---
name: ontology-consistency-check
description: "MSA 연쇄 장애 완화 연구의 문서·온톨로지 간 용어·정의·수식 일관성을 점검한다. docs/proposal.md·research/challenges.md·README.md·ontology_temp/*·devkit/ontology.yaml 사이에 같은 개념이 다르게 정의되거나, 온톨로지(ontology.ttl)와 요약 문서(classes.md/properties.md/individuals.md)가 서로 어긋나는지 검증한다. '온톨로지 확인해줘', '개념 일관성 체크', '용어가 문서마다 다른지 봐줘', '온톨로지랑 문서랑 안 맞는 부분 찾아줘', 'p_eff 정의 다시 확인', 'ontology.yaml 갱신 후 재검증' 요청 시 반드시 사용."
---

# Ontology Consistency Check

이 스킬은 연구 문서 전반의 **개념 정합성**을 검증한다. 논문 심사에서 가장 흔한 실패 원인 중 하나가 "같은 용어를 문서마다 다르게 쓰는 것"이므로, 이 점검은 매 문서 수정 후 반복되어야 한다.

## 왜 이 점검이 필요한가

이 연구는 `ontology_temp/`에 이미 형식화된 OWL 온톨로지(`ontology.ttl`)와 그 요약 문서(`classes.md`/`properties.md`/`individuals.md`)를 갖고 있다. `devkit/ontology.yaml`은 이 둘을 통합 스캔한 결과다. 하지만 두 소스(형식 ttl vs 요약 md)가 항상 동기화되어 있다는 보장은 없다 — 실제로 최초 스캔 시점(devkit/ontology.yaml 생성 시)에 다음 격차가 발견되었다:

| 항목 | md에만 있음 | ttl에만 있음 |
|---|---|---|
| 객체 속성 | `causesViolationIn`, `concretizesAt` | — |
| 데이터 속성 | `credibility`, `relevance` | — |
| 개체 | Baseline 5종, Ablation 1종, Tool 8종, `Withhold_i`, `TPS_retention`, `P99_latency` | `DD_TransferLearning`, `DD_TwoTier`, `FailureProbability_out`, `Uncertainty_out` |

이 표는 스캔 시점의 스냅샷이다. **매번 새로 diff해서 현재도 유효한지 확인한다** — 문서가 갱신되면 격차가 좁혀지거나 새로 생길 수 있다.

## 절차

1. **재스캔 여부 결정**: `ontology_temp/` 자료가 마지막 스캔 이후 바뀌었으면 `devkit:ontology-view` 스킬의 `scan` 절차(또는 `update`)로 `devkit/ontology.yaml`을 최신화한다. 바뀌지 않았으면 기존 yaml을 그대로 쓴다.
2. **형식-요약 간 diff**: `ontology_temp/ontology.ttl`에서 `owl:Class`/`owl:ObjectProperty`/`owl:DatatypeProperty`/`owl:NamedIndividual` 선언을 추출하고, `properties.md`/`individuals.md`/`classes.md`가 서술하는 항목 집합과 비교한다(둘 다 있는 스킬 도구가 없으므로 grep/직접 읽기로 수행). 위 표를 갱신한다.
3. **문서 간 용어 대조**: `docs/proposal.md`, `research/challenges.md`, `README.md`가 같은 개념(예: PolicyEngine, EffectiveRisk, SafetyGuard, 5종 Actuator)을 설명하는 부분을 찾아 정의·수식·수치가 일치하는지 확인한다. 특히:
   - `EffectiveRisk` 수식이 `max(0, p̄_v − κ√u_v)`로 일관되게 쓰였는지 (과거 "1−분산" 오기 이력 있음, G4)
   - `SafetyGuard`가 어디서든 "비용함수에서 유도"로 설명되고 "하드코딩 규칙"으로 오설명된 곳이 없는지
   - 3축 비교(위상 인지 예측/조치 공간 이질성/신뢰도 구간별 대응) 문구가 논문 전체에서 같은 이름으로 쓰이는지
4. **결과 보고**: `research-writer` 에이전트의 출력 포맷(파일:라인 — 문제 — 근거 — 제안)을 따른다. 격차가 여러 문서에 걸쳐 있으면 어느 문서가 최신/정확한 소스인지 판단 근거를 함께 제시한다(임의로 통일하지 않음).

## 하지 않는 것

- 온톨로지 yaml/ttl을 이 스킬이 직접 고치지 않는다 — 사용자 승인 후 `devkit:ontology-view`의 `update` 절차로 반영한다.
- 실험 설계·구현 관련 불일치는 다루지 않는다(`experiment-design-review`, `gnn-policy-implementation-support`로 위임).
