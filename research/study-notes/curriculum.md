# MSA 연쇄 장애 완화 연구 — 전체 학습 커리큘럼 (개요)

> 이 문서는 연구에 필요한 학습을 3개 트랙으로 분류한 상위 개요다. 트랙별 세부 학습 자료(강의/문서/실습 노트)는 각 하위 폴더에 순차적으로 채워 넣는다. **이 문서는 "무엇을 공부해야 하는가"의 목록이며, 학습 내용(자료·설명) 자체는 아직 채워지지 않았다.**
>
> 배경: [research/overview.md](../overview.md) · 근거: [docs/proposal.md](../../docs/proposal.md), [research/challenges.md](../challenges.md), [research/timeline.md](../timeline.md)

## 트랙 구조

| 트랙 | 폴더 | 성격 | 필요 시점 |
|---|---|---|---|
| ① GNN/ML | [1-gnn/](1-gnn/) | 예측 레이어 이론·구현 | timeline.md ①모델 스모크 테스트 |
| ② K8s/인프라 | [2-infra-k8s/](2-infra-k8s/) | 실험 환경 구축·데이터 수집 | timeline.md ②데이터 파이프라인 검증 — **다음 단계, 가장 급함** |
| ③ Policy Engine/Actuator | [3-policy-actuator/](3-policy-actuator/) | 의사결정·조치 구현 | timeline.md ③전체 실험(구현 착수, 프로포절 심사 이후) |

①과 ②는 서로 독립적이라 병행 가능. ③은 ②를 어느 정도 익힌 뒤, 실제 구현 착수 시점에 맞춰 진행하는 것이 효율적이다.

---

## ① GNN/ML — [1-gnn/curriculum.md](1-gnn/curriculum.md)

STEP 1~8로 구성된 세부 커리큘럼이 이미 있음(위 링크 참고). 다음 항목은 기존 STEP에 없어 추가로 필요하다.

- [ ] **Calibration 평가** (ECE, reliability diagram) — [proposal.md §4-2·§4-4](../../docs/proposal.md)에서 실패확률 p̄_v의 보정 검증에 직접 쓰임. STEP 8(신뢰도 산출 방법론)에 보강 항목으로 추가 예정.

## ② K8s/인프라 — [2-infra-k8s/](2-infra-k8s/)

timeline.md ②단계(Online Boutique를 로컬 K8s에 올려 실측 데이터를 수집하는 단계)에 필요한 항목. 자료는 추후 채운다.

- [ ] K8s 심화 — HPA 내부 동작, resource requests/limits, cgroups, Minikube/K3s 운영
- [ ] Istio — Sidecar/Envoy 구조, VirtualService/DestinationRule, EnvoyFilter, fault injection(delay/abort)
- [ ] Chaos Mesh — NetworkChaos/StressChaos/PodChaos 등 CRD
- [ ] Locust/k6 — 트래픽 프로파일(정상/버스트/점진증가) 스크립팅
- [ ] Observability — Prometheus/PromQL, Istio 내장 RED metrics(`istio_requests_total` 등), kube-state-metrics/cAdvisor
- [ ] Redis 운영 — primary/replica 구성, `maxclients` 튜닝 (커넥션풀 병목 시나리오·Read Redirection 조치의 전제)

## ③ Policy Engine/Actuator — [3-policy-actuator/](3-policy-actuator/)

timeline.md ③단계(Policy Engine·Actuator 구현) 착수 시점에 필요한 항목. 자료는 추후 채운다.

- [ ] 회복성 패턴 & Resilience4j — Circuit Breaker(`FORCED_OPEN` 상태), Bulkhead
- [ ] Envoy `redis_proxy` read_policy — Read Redirection 조치의 실제 메커니즘
- [ ] 분산시스템 신뢰성 기초 — SLO/SLI, P50/P90/P99, Thundering Herd
- [ ] Brownout(Graceful Degradation) · Traffic Shedding 패턴

---

## 참고 (정식 학습 트랙 아님, 필요할 때만 조회)

- Online Boutique 소스 구조(서비스별 backing store 등)
- FIRM의 cgroups/Intel MBA·CAT/HTB — 관련연구 서술용 개념 이해로 충분
- 채택하지 않은 방법(SVM/FIRM, ChebConv/AGQ, DTW/GraphGRU, Q-learning) — 논문만 읽으면 충분, 구현 학습 불필요
