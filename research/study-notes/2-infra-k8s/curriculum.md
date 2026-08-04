# K8s/인프라 학습 커리큘럼 (Track ②)

> 이 문서는 [research/study-notes/curriculum.md](../curriculum.md)(전체 개요)의 트랙 ② 세부 학습 자료다. timeline.md ②단계(Online Boutique를 로컬 K8s에 올려 GNN 학습 라벨용 실측 데이터를 수집하는 단계)에 필요한 항목을 다룬다.
>
> 대상: Java/Spring 백엔드 개발자, Docker/Docker Compose 경험 있음, K8s/Istio/카오스 엔지니어링/observability는 "대략은 알지만 확실치 않은" 수준. **링크는 검색으로 실제 존재를 확인(제목·채널·URL 일치)했으나, 아직 직접 시청·실습하며 학습 노트를 정리하지는 않은 상태다** — 1-gnn/curriculum.md처럼 학습 노트(PDF)가 아직 없다.

## 1. K8s 심화 — HPA 내부 동작, resource requests/limits, cgroups, Minikube/K3s 운영

- **[Must Have]** TechWorld with Nana — Kubernetes Tutorial for Beginners [FULL COURSE in 4 Hours]: https://www.youtube.com/watch?v=X48VuDVv0do
- **[Must Have]** Learn Kubernetes with KodeKloud (재생목록): https://www.youtube.com/playlist?list=PL2We04F3Y_43dAehLMT5GxJhtk3mJtkl5

TechWorld with Nana는 "가장 많이 시청된 무료 쿠버네티스 강의"(크래시 코스 기준 조회수 600만+)로 알려진 채널로, Minikube 설치부터 리소스 요청/제한, YAML 구성까지 실습 중심으로 다룬다. KodeKloud 재생목록은 CKA(Certified Kubernetes Administrator) 자격증 준비용 무료 자료 중 가장 인기 있는 자료로 꼽히며, KodeKloud 노트 사이트(notes.kodekloud.com)의 Resource Limits·Scheduling·Autoscaling 챕터와 짝을 이뤄 HPA/리소스 관리를 스케줄링 관점에서 보강해준다.

한국어로 HPA 내부 동작 원리(15초 주기 컨트롤 루프)나 cgroups와의 관계를 전문적으로 다루는 한국어 발표 영상은 검색으로 찾지 못했다(스캐터랩·제니퍼소프트 등 기술 블로그 글은 있으나 영상은 없음). 위 두 영상은 모두 영어이며, 필요 시 유튜브 자동 번역 자막(설정 → 자막 → 자동 번역 → 한국어)으로 보완할 수 있다.

목표: HPA가 15초 주기로 메트릭을 체크해 replica 수를 조정하는 컨트롤 루프 구조, requests 값이 HPA의 CPU 사용률(%) 계산 기준이 된다는 점, cgroups가 실제로 limits를 강제하는 방식을 이해하고, 로컬에서 Minikube(또는 K3s) 클러스터를 직접 띄워 Online Boutique를 배포할 수 있는 수준.

## 2. Istio 서비스 메시 — Sidecar/Envoy, VirtualService/DestinationRule, EnvoyFilter, fault injection

- **[Must Have]** Service Mesh: Crash Course on ISTIO (Part 1), KodeKloud: https://www.youtube.com/watch?v=-Ib5_4VaWWs
- **[Must Have]** Service Mesh: Crash Course on ISTIO (Part 2), KodeKloud: https://www.youtube.com/watch?v=Cn2LHqdHwXM

KodeKloud의 Mumshad Mannambeth가 진행하는 2부작으로, Part 1은 마이크로서비스의 문제와 Istio 아키텍처(Istiod 컨트롤 플레인 + Envoy 사이드카)를, Part 2는 Gateway·Virtual Service·Destination Rule·Fault Injection·Timeout·Retry를 Kiali/Prometheus/Grafana/Jaeger로 시각화하며 다룬다. KodeKloud는 CKA/CKAD 준비 커뮤니티에서 가장 널리 인용되는 실습 플랫폼이다.

한국어 자료로는 SUSE Korea(수세코리아) 공식 채널의 "랜처톡톡(Rancher Talks): 쿠버네티스 무중단 운영을 위한 Istio, OPA Gatekeeper"(https://www.youtube.com/watch?v=2Vk9ShkPxh8, Istio 1.5 전후 아키텍처 변화 + Rancher 데모)와, 클라우드 클럽(Cloud Club, 벤더 중립 IT 연합 동아리)의 컨퍼런스 "클클콘"에서 발표된 "Istio와 함께 하는 Service Mesh 첫걸음"(양다연, https://www.youtube.com/watch?v=ZGUZVkbzOwg)을 참고용으로 추가 시청할 수 있다. 다만 두 영상 모두 개념 입문 위주라 VirtualService/DestinationRule 실습과 fault injection까지 깊게 다루지는 않아 Must Have로는 KodeKloud 2부작을 우선했다.

EnvoyFilter는 매우 니치한 고급 주제라 dedicated 한국어 영상은 찾지 못했다. Istio/Envoy 생태계에 깊이 관여하는 Solo.io의 Peter Jausovec가 발표한 "A Practical Guide to Understanding and Configuring Envoy Filters"(https://www.youtube.com/watch?v=9tITEHHW-J4)를 EnvoyFilter 전용 보충 자료로 참고할 것(Must Have 2개는 위 KodeKloud 영상에 이미 배정했으므로 이 영상은 필요할 때만 조회).

목표: bookinfo 예제로 VirtualService/DestinationRule을 직접 작성해 트래픽을 라우팅하고, Istio 내장 fault injection(delay/abort)으로 Online Boutique의 특정 서비스 호출에 지연·에러를 주입해 Circuit Breaker/Traffic Shedding 조치를 트리거할 수 있는 수준.

## 3. Chaos Mesh — NetworkChaos/StressChaos/PodChaos CRD 기반 장애주입

- **[Must Have]** Chaos Mesh 2.0: Make Chaos Engineering Easy — Cwen Yin, PingCAP (KubeCon + CloudNativeCon Europe 2022): https://www.youtube.com/watch?v=owJ1tqnD7pI
- **[Must Have]** Make Cloud Native Chaos Engineering Easier: Deep Dive into Chaos Mesh — Cwen Yin, PingCAP (KubeCon + CloudNativeCon North America 2022): https://www.youtube.com/watch?v=bZnI5omUKe4

Cwen Yin은 PingCAP(Chaos Mesh 원 개발사, TiDB 팀)의 메인테이너로 두 발표 모두 KubeCon+CloudNativeCon(CNCF 최상위 컨퍼런스)에서 진행됐다. Chaos Mesh는 CNCF Incubating 프로젝트이며, 두 영상은 PodChaos/NetworkChaos/StressChaos 등 CRD 종류와 실험 정의 방법을 프로젝트 메인테이너가 직접 설명한다는 점에서 가장 권위 있는 자료다. 한국어 발표 영상(데뷰/우아한형제들 등)은 검색으로 찾지 못했다 — Chaos Mesh는 국내 발표 사례가 아직 드문 것으로 보인다. 필요 시 자동 번역 자막을 활용할 것.

목표: Online Boutique의 특정 서비스(예: cartservice ↔ Redis)에 NetworkChaos(지연/패킷 손실)나 PodChaos(컨테이너 kill)를 CRD YAML로 정의해 실험을 재현 가능하게 실행하고, 이 장애 주입이 GNN 학습 라벨(정상/전조/장애)의 트리거가 되는 구조를 이해하는 수준.

## 4. Locust (or k6) — Python 기반 부하테스트 스크립팅, 트래픽 프로파일 구성

- **[Must Have]** Load testing with Python and Locust — Lars Holmberg: https://www.youtube.com/watch?v=G03B4ZM93bs
- **[Must Have]** Basics of load testing with k6 and Grafana in 20 minutes — Nicole van der Hoeven, Grafana Labs: https://www.youtube.com/watch?v=gvounvDSDGg

Lars Holmberg는 Locust(오픈소스 부하테스트 도구) 자체의 메인테이너로, Python 코드로 HttpUser를 작성해 부하를 생성하는 핵심 워크플로를 다룬다. 연구 대상이 Java/Spring 개발자이면서 이미 Python 경험이 있으므로(GNN 파트에서 PyTorch 사용) Locust 쪽이 진입장벽이 낮다. Nicole van der Hoeven은 k6(Grafana Labs가 인수한 부하테스트 도구)의 개발자 애드보킷으로, steady/spike/ramp-up 등 트래픽 프로파일(load pattern) 설계 개념을 k6 기준으로 설명하며 이 개념은 Locust의 커스텀 LoadTestShape로 그대로 옮겨 적용 가능하다.

한국어 자료(Yun Blog, DevOcean 등 기술 블로그)는 다수 있으나 검색으로 확인된 한국어 유튜브 영상은 찾지 못했다(PyCon Korea 2015에 Locust 발표가 있었으나 발표 언어는 영어였다). 자동 번역 자막으로 보완 가능.

목표: Locust(or k6)로 정상 트래픽(steady), 급증 트래픽(spike), 점진 증가(ramp-up) 세 가지 프로파일을 스크립트로 구성해 Online Boutique에 주입하고, 이 트래픽 부하가 Chaos Mesh 장애 주입과 결합되어 GNN 학습용 시계열 데이터를 만들어내는 실험 설계를 스스로 짤 수 있는 수준.

## 5. Observability — Prometheus/PromQL, Istio RED metrics, kube-state-metrics/cAdvisor

- **[Must Have]** Setup Prometheus Monitoring on Kubernetes using Helm and Prometheus Operator (Part 1) — TechWorld with Nana: https://www.youtube.com/watch?v=QoDqxm7ybLc
- **[Must Have]** Intro to PromQL with Julius Volz: https://www.youtube.com/watch?v=MPN0SLE5YWk

TechWorld with Nana의 영상은 Helm으로 Prometheus Operator를 K8s에 설치하고 실제 컴포넌트(node-exporter류, kube-state-metrics에 준하는 메트릭 소스 포함)를 구성하는 실습이다. Julius Volz는 Prometheus 공동 창시자이자 PromCon/PromLabs 설립자로, 이 영상은 그가 직접 PromQL 기초를 가르치는 입문 강의라 신뢰도가 가장 높다.

Istio가 자동 노출하는 RED metrics(`istio_requests_total`, `istio_request_duration_milliseconds_bucket`)를 전용으로 다루는 영상은 한국어든 영어든 검색으로 찾지 못했다 — 공식 문서(istio.io의 Querying Metrics from Prometheus, Istio Standard Metrics)로 대체 학습이 필요하다. kube-state-metrics·cAdvisor를 전문으로 다루는 한국어 영상도 찾지 못했으며, TechWorld with Nana의 실습 영상으로 개념을 실전 감각으로 익히는 것을 권장한다.

목표: Prometheus를 K8s에 설치하고 PromQL로 쿼리를 직접 짜서, GNN 노드 feature로 쓸 CPU/지연/에러율(RED metrics)을 istio_requests_total·istio_request_duration_milliseconds_bucket과 kube-state-metrics/cAdvisor 계열 메트릭에서 실제로 뽑아낼 수 있는 수준.

## 6. Redis 운영 — primary/replica 구성, maxclients/커넥션 제한 튜닝

- **[Must Have]** RedisConf 2019: Deep Dive into Redis Replication: https://www.youtube.com/watch?v=esbRryo0Ty8

RedisConf는 Redis(현 Redis Inc.)가 주최하는 공식 컨퍼런스이며, 이 발표는 primary-replica 복제 프로토콜(풀 싱크/부분 싱크)을 딥다이브 형식으로 다뤄 이 연구의 cartservice→Redis 시나리오에서 필요한 replica 구성 이해에 가장 적합하다.

`maxclients`·커넥션 풀 튜닝을 전문으로 다루는 dedicated 영상(한국어·영어 모두)은 검색으로 찾지 못했다 — RedisConf/Redis University 채널에도 이 주제만 단독으로 다루는 발표가 확인되지 않았다. 대신 우아한형제들 기술블로그의 "Redis New Connection 증가 이슈 돌아보기"(https://techblog.woowahan.com/23121/, 커넥션 풀 전략(LIFO→FIFO) 튜닝 실제 사례)와 공식 문서(redis.io의 Redis Clients, Pipelining, and Performance Tuning Guide)를 텍스트 자료로 병행 학습할 것을 권장한다. 영상 자료가 채워지면 이 섹션을 갱신한다.

목표: primary-replica 구성의 복제 지연(replication lag) 개념과 `maxclients` 기본값(10,000)이 왜 cartservice의 커넥션풀 병목 시나리오의 실측 전제가 되는지 이해하고, Read Redirection 조치(읽기 트래픽을 replica로 우회)를 로컬 Redis 클러스터에서 재현할 수 있는 수준.

---

> 검증 관련 참고: 위 링크는 모두 WebSearch로 제목·채널·URL이 일치하는 것을 확인한 뒤 기재했다. 한국어 자료가 있는 항목(1-K8s 심화 일부, 2-Istio)은 국내 벤더/커뮤니티 채널(SUSE Korea, Cloud Club)을 우선했고, 나머지는 프로젝트 메인테이너·공식 컨퍼런스(KubeCon, RedisConf) 위주의 영어 자료로 채웠다 — 필요 시 유튜브 자동 번역 자막(설정 → 자막 → 자동 번역 → 한국어)을 활용할 수 있다. Istio RED metrics 전용 영상과 Redis maxclients/커넥션 풀 튜닝 전용 영상은 검색으로 찾지 못해 각 섹션에 그 사실과 대체 텍스트 자료를 명시했다.
