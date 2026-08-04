# Policy Engine/Actuator 구현을 위한 학습 커리큘럼

> 이 문서는 [상위 개요](../curriculum.md)의 트랙 ③(Policy Engine/Actuator) 세부 커리큘럼이다. 트랙 ①(GNN/ML)·②(K8s/인프라)와 달리, 이 트랙은 **프로포절 심사 이후 실제 구현에 착수하는 시점**에 맞춰 공부하는 것이 효율적이다. 트랙 ②(Istio/Envoy/Redis 운영 등 인프라 기초)를 어느 정도 익힌 뒤 진행할 것을 권장한다.
>
> 링크 검증 원칙: 아래 모든 영상은 실제 검색으로 존재를 확인했다(제목·채널·URL 일치를 YouTube oEmbed 응답으로 재확인). 신뢰할 수 있는 전용 영상을 찾지 못한 항목은 추측 링크 대신 그 사실을 명시하고 공식 문서로 대체했다.

## 1. 회복성 패턴 & Resilience4j (Circuit Breaker · `FORCED_OPEN` · Bulkhead)

- **[Must Have]** 서킷브레이커 사용 방식 개선하기 | 당근 SERVER 밋업 2회: https://www.youtube.com/watch?v=ThLfHtoEe1I
- 당근(구 당근마켓) 팀 공식 채널(@daangnteam)에 올라온 실제 사내 밋업 발표. 실무에서 서킷브레이커를 host 단위/method 단위로 어떻게 세분화해 적용했는지, `HALF_OPEN`에서 언제 `CLOSED`로 되돌릴지 등 상태 운영 경험을 다룬다 — 개념 설명이 아니라 실제 프로덕션에서 겪은 튜닝 이슈 중심이라 실무 감각을 잡기에 좋다.
- **주의(투명성 고지)**: 이 영상이 `FORCED_OPEN` 상태 자체를 명시적으로 다루는지는 확인하지 못했고, Resilience4j `Bulkhead` 모듈을 전용으로 다루는 신뢰도 있는 한국어/영어 영상도 검색으로 찾지 못했다. `FORCED_OPEN`(항상 거부)·`DISABLED`(항상 허용) 같은 특수 상태와 상태 전이표는 영상 대신 **Resilience4j 공식 문서**로 보완할 것: [CircuitBreaker 문서](https://resilience4j.readme.io/docs/circuitbreaker), [Bulkhead 문서](https://resilience4j.readme.io/docs/bulkhead).
- 목표: `docs/proposal.md`의 Tier 1(로컬 ms-레벨 리플렉스)이 통상적인 실패율 기반 OPEN/HALF_OPEN 전이를 담당하고, GNN 기반 Tier 2가 `transitionToForcedOpenState()`로 그 위에 escalate-only(OR-semantics)로 개입하는 이중 계층 구조를 Resilience4j 상태머신 수준에서 정확히 설명할 수 있는 정도. 이후 lease TTL로 두 계층 간 충돌(Tier 2가 강제 OPEN한 뒤 Tier 1이 자체적으로 CLOSED로 되돌리는 경쟁 상태)을 어떻게 막을지도 문서의 상태 전이 API(`transitionToClosedState()` 등)를 참고해 설계.

## 2. Envoy `redis_proxy` `read_policy`

- **검증 결과: 이 주제만을 전용으로 다루는 신뢰도 있는 영상을 찾지 못했다.** `redis_proxy` 필터의 `read_policy`(MASTER/PREFER_MASTER/REPLICA/PREFER_REPLICA/ANY)는 Redis Cluster 대상의 매우 좁은 Envoy 확장 기능이라, 개념 설명 영상이 사실상 존재하지 않는다(검색 결과는 전부 Envoy 공식 proto 문서로 귀결됨).
- **대체 방안 1 (1차, 필수)**: Envoy 공식 문서를 1차 자료로 사용 — [Redis Proxy proto 문서](https://www.envoyproxy.io/docs/envoy/latest/api-v3/extensions/filters/network/redis_proxy/v3/redis_proxy.proto)(`read_policy` 필드 정의), [Redis 아키텍처 개요](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/other_protocols/redis)(전체 라우팅/미러링 구조).
- **[Must Have] (차선책, Envoy 필터 아키텍처 전반)** Envoy Internals Deep Dive — Matt Klein, Lyft (Advanced Skill Level): https://www.youtube.com/watch?v=gQF23Vw0keg
- CNCF 공식 채널에 올라온 KubeCon EU 2018 발표. 연사 Matt Klein은 Envoy의 원작자(Lyft에서 만듦)로, 이 분야에서 가장 권위 있는 소스다. `read_policy` 자체는 다루지 않지만, Envoy가 필터 체인(L4/L7 network/HTTP filter)으로 확장되는 구조를 이해하면 `redis_proxy`가 왜 "필터"로 구현되고 EnvoyFilter CRD로 Istio에 주입 가능한지 원리를 알 수 있다.
- 목표: `read_policy: REPLICA`(또는 `PREFER_REPLICA`) 설정이 Istio `EnvoyFilter`로 어떻게 cartservice → Redis 커넥션 경로에 주입되는지, 그리고 이 조치가 왜 "리소스를 추가 프로비저닝하지 않고" 커넥션 풀 병목을 완화하는 Read Redirection인지(K8s Scale-up이 이 시나리오에서 오히려 thundering herd를 유발할 수 있는 것과 대비해서) 설명할 수 있는 정도.

## 3. 분산시스템 신뢰성 기초 (SLO/SLI/에러버짓 · P50/P90/P99 · Thundering Herd)

이 트랙에서 가장 중요한 항목이다 — 논문의 핵심 차별점(GNN 기반 선제적 완화 vs. 사후 반응형 조치, thundering herd 회피 논리)이 여기 걸려 있으므로 "대략 안다" 수준이 아니라 확실히 설명할 수 있어야 한다.

- **[Must Have] SLO/SLI/에러버짓**: Getting started with SLOs (Google Cloud Tech 공식 채널): https://www.youtube.com/watch?v=U53wC2A75Is
- Google Cloud의 공식 유튜브 채널(Google Cloud Tech)에서 제공하는 SRE 시리즈 영상으로, Google SRE Book/Workbook의 SLO 챕터를 실무 관점에서 풀어 설명한다. Google SRE가 SLO/SLI/에러버짓 개념의 원조이므로 가장 권위 있는 소스.
- **[Must Have] P50/P90/P99 Tail Latency**: "How NOT to Measure Latency" by Gil Tene (Strange Loop Conference 공식 채널): https://www.youtube.com/watch?v=lJ8ydIuPFeU
- Strange Loop는 업계에서 신뢰도가 높은 소프트웨어 컨퍼런스이고, 연사 Gil Tene은 Azul Systems CTO이자 지연시간 측정 표준 도구인 HdrHistogram의 제작자다. 단순 평균이 아니라 백분위(P50/P90/P99, 특히 P99.9)로 봐야 하는 이유와 흔한 측정 오류(coordinated omission)를 다루는 이 분야의 정석 강의로 널리 인용된다.
- **추가 참고 (Thundering Herd 실전 사례)**: SREcon25 Americas — Case Study: A Thundering Herd in the Wild (USENIX 공식 채널): https://www.youtube.com/watch?v=bKam-KtUC3M
- USENIX SREcon은 SRE 분야 최고 권위 컨퍼런스 중 하나. Bloomberg 엔지니어가 실제로 겪은 thundering herd 장애를 다루는 케이스 스터디라, "여러 요청/스레드가 동시에 같은 자원을 두드려 병목을 무너뜨리는" 패턴을 실제 사고 서사로 이해하기 좋다.
- 목표: (1) 논문의 confidence-tiered 조치가 왜 "SLO를 지키기 위한 선제적 에러버짓 소비 결정"으로 서술될 수 있는지, (2) 실험 결과를 평균이 아니라 P50/P90/P99로 리포트해야 하는 이유를 방법론적으로 방어할 수 있는지, (3) K8s Scale-up이 Redis 같은 상태 유지(stateful) 병목에서 오히려 thundering herd를 유발할 수 있다는 논문의 핵심 주장을 구체적 메커니즘(신규 파드의 동시 재연결 폭주 등)으로 설명할 수 있는 정도.

## 4. Brownout(Graceful Degradation) · Traffic Shedding 패턴

- **[Must Have] Traffic Shedding**: AWS re:Invent 2021 — Keeping Netflix reliable using prioritized load shedding (AWS Events 공식 채널): https://www.youtube.com/watch?v=TmNiHbh-6Wg
- AWS 공식 컨퍼런스(re:Invent) 발표이자 발표자가 Netflix 엔지니어인 1차 소스. Netflix의 API 게이트웨이 Zuul이 요청에 우선순위 점수를 매겨 과부하 시 낮은 우선순위 요청부터 "거부"하는 방식—즉 Traffic Shedding을 프로덕션 규모에서 실제로 어떻게 설계·검증했는지 다룬다.
- **[Must Have] Brownout/Graceful Degradation**: Reliability in microservices environments using graceful degradation | CodiLime x FluxNinja: https://www.youtube.com/watch?v=nGLHuIJgAm8
- CodiLime(네트워킹/클라우드 엔지니어링 전문 기업) 공식 채널(@CodiLime_)에 올라온 웨비나. FluxNinja는 적응형 부하 관리(그레이스풀 디그레이드) 제품을 만드는 회사로, 이 주제를 실제로 구현해본 쪽의 발표라는 점에서 근거가 있다. Brownout이 왜 "선택적 기능(광고·추천 등)을 낮추는 dimmer switch"이고 Traffic Shedding(요청 자체 거부)과 구분되는지 실무 관점에서 다룬다.
- 목표: Traffic Shedding("요청을 통째로 거부")과 Brownout("옵션 기능만 꺼서 핵심 기능은 유지")과 Read Redirection("기능은 유지하되 일관성을 낮춤")이라는 세 조치의 경계를 각각의 구체적 실패 시나리오(예: 결제는 지키고 추천 위젯만 끄는 것 vs. 요청 자체를 429로 거부하는 것)로 구분해서 설명할 수 있는 정도 — Online Boutique의 ad/recommendation 서비스 호출을 dimmer switch로 끄는 조치를 실제로 구현할 수 있는 수준.
