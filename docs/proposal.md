# 연구계획서 (Proposal)

## 신뢰도 기반 GNN 예측을 활용한 MSA 연쇄 장애 선제 대응 아키텍처

> 상태: 프로포절 준비 단계 (1학기 종료 시점, 2026년 7월 기준). 실험 미착수.
> 본 문서는 연구 작업본을 정리한 것이며, 진행 상황에 따라 계속 갱신된다.

---

## 1. 연구 배경 및 페인 포인트

- 모놀리식 → 쿠버네티스(K8s) 기반 분산 아키텍처 전환 과정에서 마이크로서비스(MSA) 간 연쇄 장애(cascading failure) 위험이 급증한다.
- 기존 K8s HPA(Horizontal Pod Autoscaler)는 다음 4가지 한계를 가진다.
  1. **사후반응성**: 장애가 이미 발생한 뒤에야 반응한다.
  2. **Thundering Herd 유발 가능성**: 병목이 DB 커넥션풀 등 상태성(stateful) 리소스일 때, 단순 스케일아웃이 오히려 상태를 악화시킬 수 있다.
  3. **위상(topology) 정보 부재**: 서비스 간 호출 관계를 반영하지 않는다.
  4. **시계열 전용 모델의 한계**: 시계열만 보는 모델은 장애 전파 경로(propagation path)를 포착하지 못한다.

---

## 2. 제안 아키텍처 및 핵심 메커니즘

### A. AI 예측 레이어 (GNN)

GNN은 사전학습된 범용 모델이 존재하지 않는 영역이다. 본 연구가 채택하는 것은 GNN "레이어 구조/구현체"(GCN·GraphSAGE·GAT 등, PyTorch Geometric/DGL)이며, 벤치마크 토폴로지 위에서 직접 학습시킨다. LSTM은 baseline 비교용으로만 학습한다(그래프 구조를 반영하지 않는 모델과의 정량 비교, §4-3).

**최종 모델 선정: 정적 GAT (Graph Attention Network)**

| 결합 요소 | 채택 여부 | 근거 |
|---|---|---|
| GCN vs GAT | **GAT 채택** | GRAF(MPNN, 이웃 균등 집계)와 AGQ(ChebConv, 스펙트럴 방식) 모두 이웃 노드 간 차등적 중요도를 학습하지 못한다. 마이크로서비스 의존관계는 서비스별 중요도가 균일하지 않으므로(예: 결제 서비스 대비 로깅 서비스), attention 기반 차등 집계가 구조적으로 더 적합하다. |
| 강화학습(RL) 결합 | 미채택 | Policy Engine이 이미 신뢰도 구간별 정책에 따라 조치를 선택하는 구조이므로, 액션 선택까지 RL로 재학습시킬 필요가 없다. RL 없이도 결정 로직이 해석 가능하다는 점이 오히려 설명 가능성 측면의 강점이다. |
| 시계열 결합 (STGNN) | 미채택(1차 모델), 확장 옵션으로 유보 | 본 연구의 목표는 "미래의 연속적 수치를 정확히 예측"(AGQ·GraphGRU의 목표)이 아니라 "현재 상태의 위험 여부와 확신도를 즉각 판단"하는 것이다. 시계열 결합은 버퍼링 시간과 시간축 attention 연산이 추가되어 추론 지연이 늘어나므로, 즉각 대응이 핵심 기여인 본 연구에서는 정확도보다 낮은 추론 지연을 우선하는 설계를 택한다. G2(추론 레이턴시 vs 즉각반응 타이밍)는 2계층 제어(§2-D)로 해소했으며, 최근 시점들의 흐름이 판단 정확도에 유의미하다면 최소 침습적 확장(입력 윈도우 확대)을 후속 실험으로 고려할 수 있다. |
| 신뢰도 산출 방식 | **Deep Ensemble(N=5) 채택** | 후보(MC Dropout/Deep Ensemble/Softmax Entropy) 중, Lakshminarayanan et al.(2017)이 MC Dropout 대비 더 신뢰할 수 있는 불확실성 추정치를 제공함을 실증한 결과를 1차 근거로 채택한다. MC Dropout은 추론 시 동일 모델에 대한 N회 반복 순전파가 필요해 지연 시간이 증가하는 반면, Deep Ensemble은 N개 모델을 병렬 배치하여 추론할 수 있어 즉각 대응이 핵심인 본 시스템에 구조적으로 더 적합하다. 그래프 규모가 작아(11개 노드) N=5 모델 학습에 따른 비용 부담도 낮다. |
| [신규] Readout 방식 | **Pooling 채택 (Flatten 미채택)** | GRAF는 readout 단계에서 각 노드 임베딩을 flatten해 완전연결(FC) 신경망에 통째로 입력하는 구조를 사용하며, 이로 인해 FC 입력 차원과 파라미터 수가 노드 수에 선형 비례한다. GRAF 스스로(ToN 2024판 Discussion) "readout phase's neural network input node dimension is linearly dependent on the number of microservices"이며 "performance may degrade when applied to applications composed of hundreds to thousands of microservices"라고 확장성 한계를 인정한다. 본 연구는 flatten 대신 mean/attention pooling으로 그래프 전체를 고정 크기 벡터로 압축하는 readout을 채택해, 그래프 크기(노드 수)가 달라져도 모델 파라미터·출력 차원이 그대로 유지되도록 설계한다. |

### B. 의사결정 계층 — Policy Engine (핵심 Contribution)

본 연구의 위치: GRAF(위상 인지형 GNN 예측이지만 조치는 자원할당 하나로 한정)와 FIRM(조치를 학습 기반으로 적응적 선택하지만 조치 공간이 저수준 자원 재할당에 한정되고 그래프 구조 미반영)의 교집합에서, **신뢰도 구간별 대응(Confidence-tiered Response)**이라는 세 번째 축을 추가한 것이 본 연구의 정확한 학술적 위치다. AGQ(GNN+RL 결합이나 조치 공간은 자원할당 단일 축)와 GraphGRU(GAT 기반이나 예측에서 그침)도 세 축 중 어느 하나 이상을 비워두고 있어, 본 연구의 위치는 여전히 비어 있는 자리다.

**관련 연구 4자 비교표**

| 축 | GRAF (CoNEXT'21/ToN'24) | FIRM (OSDI'20) | AGQ (FGCS'26) | GraphGRU (ICPADS'22) | 본 연구 |
|---|---|---|---|---|---|
| 예측 모델 | GNN(MPNN, 위상 반영) | SVM(위상 미반영) | STGNN(ChebConv, 시계열)+Q-learning | GAT(DTW 기반 동적 그래프) | GAT(정적, 실 서비스 호출관계 기반) |
| 조치 공간 | 자원 할당(스칼라) | 자원 재할당 다차원(CPU/Mem/LLC/IO/Net + 수평 스케일, 전부 프로비저닝) | 자원 할당(스칼라) | 없음(예측만) | 질적 이질(CB/Shedding/Scale/Redirect/Brownout, 프로비저닝+비프로비저닝) |
| 신뢰도 구간별 대응 | 없음 | 없음 | 없음 | 없음 | **있음 (신규 기여)** |

- 신뢰도 구간별 대응 전략: 고신뢰도 = 적극적 조치 / 중간신뢰도 = 저비용·가역적 조치 / 저신뢰도 = 보류.
- 런타임 독립성 주장 유지.

#### 비용함수(Cost Function) — 신뢰도 구간별 대응의 형식화

"가용성 손실/되돌리기 난이도/오탐 피해를 저울질한다"는 서술을 **불확실성 하의 기대비용 최소화** 형태로 형식화한다. 신뢰도 구간(고/중/저)을 손으로 긋지 않고, 비용함수에서 조치별 임계값이 유도되도록 설계하는 것이 핵심이다.

**변수 정의**

| 기호 | 의미 | 비고 |
|---|---|---|
| p | 신뢰도 — Deep Ensemble이 '위험 진짜'로 보는 확률(= 1 − 정규화 분산) | AI 레이어 출력 |
| L | 가용성 손실 — 미조치 시 연쇄장애 피해 | 전역 1개 |
| Dₐ | 조치 a의 disruption — 즉시 비용(정상 트래픽 손실·자원 소모) | 조치별 |
| Rₐ | 조치 a의 가역성/되돌리기 난이도 (0~1, 클수록 원복 어려움) | 조치별 |
| mₐ | 조치 a의 완화효과 (0~1, L을 실제로 막는 비율) — 장애 유형에 의존 | 조치별, 오탐 피해는 Dₐ·Rₐ로 파생 |

**기대비용 두 식과 조치 선택**

```
E[비용 | 조치 a] = p·[ (1−mₐ)·L + Dₐ ] + (1−p)·[ Dₐ·Rₐ ]
E[비용 | 보류   ] = p·L
```

Policy Engine은 `argmin`으로 조치를 선택하되, 보류보다 나은 조치가 없으면 보류한다. 조치 a가 보류를 이기는 조건 `E[비용|a] < p·L`을 p에 대해 정리하면 조치별 신뢰도 임계값이 유도된다.

```
p > θₐ ,   θₐ =  Dₐ·Rₐ / ( mₐ·L − Dₐ·(1−Rₐ) )
```

**→ 조치마다 필요한 신뢰도 임계값 θₐ가 다르게 유도되어, "고신뢰도=적극적 조치 / 중간=저비용·가역적 / 저=보류"가 비용함수에서 자동으로 생성된다.**

| 조치 | Dₐ | Rₐ | mₐ | 대상 전파속도 | θₐ | 신뢰도 구간 |
|---|---|---|---|---|---|---|
| Circuit Breaker | 낮음 | 낮음(auto half-open) | 높음 | 빠름 | 낮음 | 중간에서도 발동 |
| Read Redirection | 낮음 | 낮음 | 중 | 중 | 낮음~중 | 중간에서도 발동 |
| Brownout (품질 저하) | 중 | 낮음(dimmer 원복) | 중~높음(optional 경로 병목 시) | 중 | 낮음~중 | 중간에서도 발동 |
| Traffic Shedding | 중 | 낮음~중 | 중~높음 | 빠름 | 중 | 중~고 |
| K8s Scale-up | 높음 | 높음(스케일다운·상태복구 김) | 상황의존 | 느림 | 높음 | 고신뢰도만 |

**커넥션풀 차별점의 수식적 표현**: 병목이 DB 커넥션풀 등 상태성 리소스일 때 Scale-up의 mₐ가 급락(오히려 L 증가 = Thundering Herd)하여 θ_scale-up의 분모가 음수가 되고, 비용함수가 Scale-up을 자동 배제하고 CB/Redirection을 선택한다. mₐ(장애 유형 인지)를 변수로 둠으로써 GRAF류(자원할당 중심)와의 차별점(§4-3 baseline 비교)이 비용함수 내부에서 유도되며, 외부 하드코딩 규칙에 의존하지 않는다.

**초안 범위**: 본 프로포절 단계에서는 위 골격(변수 정의·기대비용 식·θₐ 유도 구조)까지를 확정한다. L·Dₐ·Rₐ·mₐ의 실제 수치는 실험 튜닝으로 이월하며, 특히 mₐ는 병목 유형(상태성/무상태)에 따른 정성적 구간 지정으로 두고 구체적 측정 방식은 오픈이슈 G3(장애/트래픽 프로파일)와 연계해 실험 단계에서 정의한다.

### C. 실행 계층 — Actuator

5종: **Circuit Breaker / Traffic Shedding / Read Redirection / K8s Scale-up / Brownout(Graceful Degradation)**

- Brownout은 요청의 비핵심(optional) 부분을 dimmer로 차단해 품질을 낮추는 방식으로 부하를 던다. Traffic Shedding(요청 통째 거부)·Read Redirection(경로 변경)과 질적으로 다른 레버로, 조치 공간의 이질성을 넓힌다. Online Boutique에서는 frontend가 `adservice`/`recommendationservice`(비핵심 기능) 호출을 조건부로 생략하는 형태로 구현 가능하다 — 벤치마크에 실제 optional 경로가 존재해 실증 가능성이 높다(FIRM은 브라운아웃을 쓰지 않으며, 이 채택은 FIRM 근거와 무관하다. [challenges.md](../research/challenges.md) D14).
- 시간이 부족할 경우 Circuit Breaker + Read Redirection 2종만 실증하고 나머지(Scale-up/Shedding/Brownout)는 "설계상 확장 가능"으로 남기는 옵션을 확보한다. 특히 Brownout은 앱 계측(필수/선택 분리)이 필요하므로 실증 우선순위는 코어 조치 뒤에 둔다.

### D. 제어 타이밍 — 2계층 제어 구조 (G2 대응)

즉각 반응이 필요한 조치(Circuit Breaker·Traffic Shedding)가 GNN 추론 주기(N초)에 묶이면 급속 전파 장애(스레드/커넥션풀 고갈, 초 단위)에 늦을 수 있다. 이를 조치를 제거하지 않고 **트리거 경로를 이원화**해 해결한다 — 5종 모두 GNN 조치 공간에 유지한다.

- **Tier 1 (로컬 반사, 상시 on)**: CB·Shedding에 로컬 반응 규칙(Resilience4j 등)을 상시 배치. 자기 호출 통계(실패율/느린호출율/큐 depth)만 보고 ms 단위로 반응하며 GNN 주기를 기다리지 않는다. GNN이 리드타임 없이 놓친 급속 장애의 안전망(floor).
- **Tier 2 (GNN 선제, N초 주기)**: 위상 전체를 예측해 리드타임이 있는 장애에 대해 신뢰도 구간별 비용함수(§2-B)로 5종을 선택. CB·Shedding은 선제적으로 force-open/force-shed하고, Read Redirection·K8s Scale-up·Brownout은 단독 트리거.

**조치별 소유 구조**

| 조치 | Tier 1 로컬 반사 | Tier 2 GNN 선제 | GNN 조치 공간 |
|---|---|---|---|
| Circuit Breaker | ✅ 상시 floor | ✅ force-open | ✅ 포함 |
| Traffic Shedding | ✅ 상시 floor | ✅ force-shed | ✅ 포함 |
| Read Redirection | — | ✅ 단독 | ✅ 포함 |
| K8s Scale-up | — | ✅ 단독 | ✅ 포함 |
| Brownout | — | ✅ 단독 | ✅ 포함 |

**충돌 방지** (Tier 1·2가 같은 액추에이터를 건드리는 CB·Shedding): (1) **OR 의미론** — 둘 중 하나라도 '조치'면 조치(열림/차단 = 안전 방향), (2) **GNN은 escalate만** — 로컬이 '정상'이라 판단한 상태를 GNN이 강제로 되돌리지(닫지) 않아 tug-of-war가 불가능, (3) **lease(TTL 1~2주기)** — GNN 명령은 시한부라 미갱신 시 자동 만료(GNN 지연/크래시 시 먹통 방지). 서로 다른 액추에이터를 함께 완화하는 경우(예: GNN Shedding + 로컬 CB)는 '더 안전한 쪽'으로 겹치는 무해한 중복이다.

이 구조로 (a) 급속 장애는 로컬층이 즉시 대응, (b) GNN은 위상 인지 선제·신뢰도 구간별 대응이라는 본연의 기여를 유지, (c) CB를 포함한 5종 이질적 조치가 그대로 유지된다. CB 임계값 실시간 미세조정(pre-arm)은 확장 옵션으로만 열어두며, GNN 추론 주기 파라미터의 실측 튜닝은 실험 단계 과제다(E4 시계열 미채택 결정과 정합).

---

## 3. 학술적 가치 및 독창성

**핵심 기여 재정의**: "GRAF류(위상 인지·예측)"와 "FIRM류(학습 기반 적응적 조치 선택)"의 교집합에 "신뢰도 구간별 대응"을 추가한 Policy Engine. 위 4자 비교표를 관련연구 섹션 서두에 배치해 차별점을 한눈에 제시한다. 다만 FIRM의 조치 공간은 원문 재확인 결과 저수준 자원 5종(CPU/Mem/LLC/IO/Net) 재할당 + 수평 스케일링으로 전부 자원 프로비저닝에 한정되므로([challenges.md](../research/challenges.md) D14), 본 연구의 비프로비저닝 조치(CB/Shedding/Redirection/Brownout)는 FIRM 대비로도 신규 기여다.

**신뢰도 축의 공백 재확인**: GRAF·FIRM·AGQ 원문을 직접 확인한 결과, 세 논문 모두 명시적인 신뢰도/불확실성 산출을 하지 않는다.
- GRAF는 GNN 예측값을 점 추정치(point estimate)로 그대로 자원 할당에 사용한다.
- FIRM은 SVM의 이분법적 분류 결과와 RL의 argmax 액션 선택을 그대로 실행한다.
- AGQ는 Q-learning의 ε-greedy 탐색 확률(ε)을 예측 신뢰도가 아닌 탐색-활용 균형을 위한 하이퍼파라미터로만 사용한다.

즉 세 논문 모두 "모델이 낸 답을 확신 정도와 무관하게 그대로 실행"하는 1단계 파이프라인이며, 이는 신뢰도 구간별 대응(축 ③)이 문헌상 완전히 비어 있는 자리임을 재확인시켜 준다.

**차별점의 구체적 논거**: 단순 자원할당/오토스케일링 접근은 병목이 DB 커넥션풀 등 상태성 리소스일 때 스케일아웃이 오히려 상태를 악화시킬 수 있다(Thundering Herd) — 이것이 자원할당 중심 접근과의 실질적 차별점이며, §4-3에서 baseline 비교로 실증할 계획이다. 이 한계는 GRAF·AGQ뿐 아니라 원문 재확인 결과 조치가 전부 저수준 자원 재할당에 한정된 FIRM에도 적용되므로(D14), 상태성 병목 시나리오는 세 선행연구가 공통으로 다루지 못하는 지점이다.

**선행연구 신뢰도 검증**

- **GRAF**: KAIST INA Lab(지도교수 Dongsu Han), ACM CoNEXT 2021(승인율 22.7%) → IEEE/ACM Transactions on Networking 2024 확장 게재. 산업 협업(Toyota) 포함.
- **FIRM**: UIUC(Qiu, Banerjee, Jha, Kalbarczyk, Iyer), USENIX OSDI 2020 — 시스템 분야 최상위 학회.
- **AGQ**: Taiyuan University of Science and Technology, Future Generation Computer Systems (Elsevier, Q1), 2026.
- **GraphGRU**: 중국과학원 선전첨단기술연구원(He, Su, Ye), IEEE ICPADS 2022(2023 게재) — 알리바바 실제 프로덕션 클러스터 데이터셋으로 검증, 기존 딥러닝 대비 최대 48.27% 정확도 개선.

네 논문 모두 신빙성 있는 정식 동료심사 venue의 연구이며, GRAF·FIRM은 최상위권, AGQ·GraphGRU는 그보다 한 단계 아래이나 정식 색인 저널/학회다.

**GRAF·FIRM·AGQ의 벤치마크 그래프 규모 재검증**: GRAF는 Online Boutique(11개 서비스) 중 실제 GNN 입력으로는 6개 노드 서브그래프만, Social Network(DeathStarBench)는 10개 노드 서브그래프만 사용(전체 벤치마크가 아닌 특정 요청 체인)한다. FIRM은 GNN이 아닌 SVM+RL 구조라 DeathStarBench·Train-Ticket 전체 그래프(15~41개)를 그대로 사용할 수 있었다. AGQ는 핵심 비교 실험(Table 3, 메인 베이스라인 대비)은 Sock Shop(~13개) 규모에서 수행했고, "수백 개 노드" 규모 실험은 비공개·비재현 시뮬레이션(LinkedIn 아키텍처 참고)으로 별도 진행된 보조 실험이다. 즉 GNN 기반 선행연구들의 실질적 검증 규모는 모두 본 연구의 11개 노드와 같은 자릿수이며, 본 연구가 유독 작은 것이 아니다.

---

## 4. 실험 설계

### 4-1. 실험 환경

- 벤치마크: **Online Boutique(11~12개 서비스)** 유지.
- 가장 직접적인 비교 대상인 GRAF도 Online Boutique와 Social Network 두 벤치마크를 메인 실험에 사용했으며, 그중 Online Boutique와 동일한 벤치마크를 채택했다는 점을 근거로 명시한다 — "왜 이렇게 작은 벤치마크를 썼냐"는 질문에 대한 선제 방어.
- **트래픽 생성 방식**: Chaos Mesh/Istio는 장애를 주입하는 도구이고 트래픽을 만들지는 않는다. Locust(또는 k6)로 정상/버스트/점진적 증가 트래픽 프로파일을 생성하고, 그 위에 Istio 내장 장애주입을 결합하는 2-레이어 구성으로 진행한다.
- **학습-실험 분리(로컬 리소스 관리용)**: K8s 클러스터+장애주입+트래픽 생성을 먼저 돌려 데이터를 수집·저장하고, 클러스터를 내린 뒤 별도로 GNN/LSTM 학습을 진행하는 방식으로 동시 부하를 줄인다.

### 4-2. Ground Truth 정의

(추가 상세화 예정 — 원본 자료 §4-2 그대로 유지)

### 4-3. Baseline 비교군

1. 반응형 K8s HPA만 사용
2. 규칙 기반 임계치 Policy
3. LSTM 기반 예측 + 동일 Policy Engine (GNN 채택 근거 검증용)
4. GRAF류 baseline: 위험 감지 시 자원할당/스케일업만 수행하는 정책 — 커넥션풀 고갈 시나리오에서 본 연구의 Policy Engine과 비교해 "자원할당 중심 접근의 역효과"를 실증
5. [선택, 시간 허용 시] FIRM류 baseline: 조치 공간은 본 연구와 같으나 위상(그래프)을 반영하지 않는 예측기로 구동하는 정책 — 위상 인지 여부의 기여를 독립적으로 검증하는 2×2 실험 설계 완성

### 4-4. 평가 지표

- TPS 유지율, P99 Tail Latency, 신뢰도 임계치별 비교, Fail-safe 동작 여부, 톰캣 스레드 덤프
- 각 baseline 및 본 연구 방식은 동일 조건에서 N회(예: 5~10회) 반복 실행하며, TPS 유지율 등은 평균±표준편차로, 레이턴시는 P50/P90/P99 백분위수로 리포팅한다.
- [선택, 저비용 보강] 부하 스케일업 실험: 동일 11-tier 위상에서 동시 사용자 수를 100→500→1000으로 늘려가며 정책이 유지되는지 확인.
- [선택, 시간 허용 시] Train Ticket 서브셋 보조 실험: 예매→결제→환불 흐름에 관련된 서비스 15~25개만 부분 배포하여, 그래프가 커질수록 GNN vs LSTM 성능 격차가 벌어지는지 보조적으로 검증(VPS 단기 대여로 진행).

---

## 5. 연구 범위 (Scope) 명시

1. Thread-per-request 모델 한정
2. Java·Spring 실증 한정
3. 경량 CQRS
4. **벤치마크 규모 한계 명시**: 본 연구는 11-tier 규모의 벤치마크에서 개념을 검증하며, 이는 가장 직접적인 비교 대상(GRAF)과 동일한 규모다. 수십~수백 개 서비스 규모의 프로덕션 환경에서의 확장성 검증은 후속 연구로 남긴다.

---

## 6. 심사 방어 전략 (Defense Strategy)

### 우려 6 — "왜 이렇게 작은 MSA 벤치마크를 썼는가?"

3단 논리:
1. **선행연구 전례**: 가장 직접적인 비교 대상인 GRAF(KAIST, CoNEXT/ToN)도 동일한 Online Boutique를 사용했다. GRAF·FIRM·AGQ의 실제 GNN 검증 규모를 재확인한 결과, 세 논문 모두 실질적으로는 본 연구와 같은 자릿수(6~15개 노드) 규모에서 핵심 결과를 냈다.
2. **의도적 스코프 설정**: §5에 명시된 대로, 본 연구의 기여는 "위상 정보 반영의 효과 검증"이지 "초대규모 프로덕션 스케일링 검증"이 아니며, 후자는 후속 연구로 명시적으로 남긴다.
3. **구조적 확장성**: GRAF는 readout에서 노드 임베딩을 flatten하는 방식이라 모델 파라미터가 노드 수에 선형 비례하고, 이를 스스로 확장성 한계로 인정한다(ToN 2024판 Discussion). 본 연구는 flatten이 아닌 pooling 기반 readout을 채택해(§2-A 표) 그래프 크기가 달라져도 모델 구조·파라미터 수가 그대로 유지되도록 설계했다 — "검증은 작은 규모에서 했지만, 모델 구조 자체는 큰 규모에도 적용 가능하도록 설계했다"는 근거.

[선택 보강] 필요 시 §4-4의 부하 스케일업 실험과 Train Ticket 서브셋 보조 실험으로 확장성 검증을 실측 보강했다는 점도 함께 제시한다.

### 우려 7 — "FIRM(OSDI) 같은 학습 기반 조치 선택 연구와 뭐가 다른가?"

극복 전략: §3의 비교표를 제시하며 세 가지 차별점을 명시한다.
1. FIRM은 그래프 구조를 반영하지 않는다.
2. 신뢰도 구간별 대응 메커니즘이 없다(예측을 확신 정도와 무관하게 그대로 실행).
3. 원문(arXiv 2008.08509, Table 3·§3.4~3.5) 재확인 결과 FIRM의 조치 공간은 저수준 자원 5종(CPU/Mem/LLC/IO/Net) 재할당 + 수평 스케일링으로 **전부 자원 프로비저닝에 한정**된다(브라운아웃·서킷브레이킹 등 비프로비저닝 조치 없음). 따라서 상태성 리소스 병목(커넥션풀 등)에서 자원 조절이 역효과를 내는 시나리오(§2-B 비용함수, D2)를 FIRM 역시 다루지 못하며, 본 연구의 비프로비저닝 조치(CB/Shedding/Redirection/Brownout)가 이 지점을 정면으로 다룬다.

§4-3에 FIRM류 baseline을 추가해 실증적으로도 방어한다.

### 우려 8 — "AGQ·GraphGRU처럼 시계열/RL 결합 GNN을 쓴 선행연구가 있는데, 왜 정적 GAT를 썼는가?"

3단 논리:
1. **문제 정의가 다름**: AGQ·GraphGRU의 목표는 미래의 연속적 자원 사용량 수치를 정확히 예측하는 것(회귀)이고, 본 연구의 목표는 현재 상태의 위험 여부와 확신도를 즉각 판단하는 것(분류/신뢰도 추정)이다.
2. **설계 트레이드오프**: 시계열 결합은 예측 정확도를 높이지만 버퍼링 시간과 시간축 attention 연산으로 추론 지연이 늘어난다. 본 연구는 신뢰도 구간별 즉각 대응이 핵심 기여이므로 정확도보다 낮은 추론 지연을 우선했다.
3. **근거 기반 설계**: 이 트레이드오프는 §4-4에서 실측 검증 예정이며, GNN 추론 레이턴시와 즉각 반응 타이밍 간의 관계는 2계층 제어(§2-D)로 다루고 추론 주기 파라미터의 실측 튜닝은 실험 과제로 계획에 포함되어 있다.

예상 후속 질문:
- "정확도가 떨어지지 않나?" → 본 연구가 검증하려는 것은 "위상 정보가 있는 것이 없는 것(LSTM)보다 낫다"이지 "AGQ의 시계열 모델보다 정확하다"가 아니다.
- "시계열을 아예 고려 안 하는가?" → 1차 모델은 정적 GAT로 시작하되, 최근 시점들의 흐름이 판단 정확도에 유의미한 영향을 준다면 최소 침습적 확장(입력 윈도우 확대) 형태의 추가 실험을 열어두고 있다.

### 우려 9 — "왜 MC Dropout이 아니라 Deep Ensemble을 신뢰도 산출 방식으로 택했는가?"

극복 전략: Lakshminarayanan et al.(2017)의 비교 실험 결과(Deep Ensemble이 MC Dropout보다 신뢰할 수 있는 불확실성 추정치 제공)를 1차 근거로, MC Dropout의 반복 추론 구조가 즉각 대응이 핵심인 본 시스템의 추론 지연 요구사항과 상충한다는 시스템 제약 근거를 2차로 결합해 제시한다.

예상 후속 질문:
- "Deep Ensemble이 왜 더 신뢰도가 높은가?" → N개의 서로 다르게 초기화된 모델이 각기 다른 국소 최적점(local optimum)에 수렴해, MC Dropout(동일 모델에서 일부 뉴런만 제거)보다 더 다양한 관점(diversity)을 확보하기 때문.
- "학습 비용이 N배 늘어나는 것 아닌가?" → 벤치마크 그래프가 11개 노드로 작아 N=5 모델 학습 비용 자체가 가볍다.
- "선행연구는 이 방식을 어떻게 검증했는가?" → GRAF·FIRM·AGQ 모두 신뢰도 산출 자체를 하지 않아 참고할 선례가 없다. 불확실성 정량화라는 별도 분야의 표준 문헌(Gal & Ghahramani 2016; Lakshminarayanan et al. 2017)에 근거를 둔다.

### 우려 10 — "그래프(데이터셋)가 커지면 학습이 오래 걸리지 않는가?"

극복 전략: GAT는 실제 존재하는 엣지에 대해서만 attention을 계산하는 sparse 연산이라 레이어당 연산량이 노드/엣지 수에 대략 선형(O(N+E))이며, 노드 수가 늘어도 폭발적으로 증가하지 않는다. Deep Ensemble(N=5)도 단일 모델 학습시간의 상수배(×5)일 뿐 그래프 크기와 무관하다. 실제 병목은 GNN 학습 연산이 아니라 실측 데이터 수집(K8s 클러스터를 실제로 구동해 라벨을 얻는 과정)의 인프라 제약(로컬 클러스터 메모리, 컨테이너 수)이며, 이는 §4-1과 [research/challenges.md](../research/challenges.md)에서 이미 식별·대응되었다.

### 우려 11 — "즉각 반응이 필요한 CB·Shedding을 느린 GNN 추론 주기에 묶으면 오히려 느려지지 않는가? 그럴 거면 GNN이 왜 필요한가?"

극복 전략(2계층 제어, §2-D):
1. 즉각 반응 조치(CB·Shedding)는 로컬 반응층(Tier 1)이 상시 담당해 ms 단위로 반응한다 — GNN 주기를 기다리지 않으므로 급속 장애에 늦지 않는다.
2. GNN(Tier 2)의 기여는 '반사'가 아니라 '선제'다. 위상 전체를 보고 리드타임 있는 장애를 예측해 신뢰도 구간별로 5종 조치를 선택하고 CB·Shedding을 선제 force-open/force-shed한다. 로컬층은 자기 호출 통계만 보므로(위상 무지) 이 선제·차등·위상 인지 대응을 할 수 없다.
3. 두 층이 같은 액추에이터를 건드려도 충돌하지 않는다: OR 의미론 + GNN escalate-only + lease TTL(§2-D).

예상 후속 질문:
- "로컬 반응층만으로 충분하지 않나?" → 로컬층은 사후·국소적이라 선제성과 위상 인지가 없다. '지금 이 blip이 연쇄장애의 앞머리인가'(커넥션풀 시나리오, D2)는 위상을 봐야 판단 가능하며 이것이 Tier 2의 몫이다.
- "GNN이 CB 임계값을 실시간 튜닝하나?" → 1차 설계는 force-open/force-shed(escalate) 방식이다. 임계값 미세조정(pre-arm)은 확장 옵션으로만 열어둔다.

---

## 참고문헌 (References)

### 주요 선행 연구

신뢰도: FIRM > GRAF > AGQ > GraphGRU · 유사도(본 연구와의 관련성): GraphGRU > AGQ > GRAF > FIRM

> 아래 논문 원문 PDF는 저작권 문제로 본 리포지토리에는 포함하지 않으며, 인용 정보와 공식 링크만 정리한다.

**GRAF — KAIST (2021 CoNEXT / 2024 IEEE-ACM ToN 확장)**

지연 SLO를 만족시키면서 총 CPU 자원을 최소화하기 위한 그래프 신경망 기반의 선제적 자원 할당 프레임워크. 프론트엔드 워크로드와 분산 트레이싱 데이터, 머신러닝 기법을 활용해 트래픽 변화의 영향을 관측·추정하고, 최적의 자원 조합을 찾아 선제적으로 자원을 할당한다. 파인튜닝된 오토스케일러 대비 최대 19%의 CPU 자원을 절감하면서 지연 SLO를 달성했고, 쿠버네티스 오토스케일러 대비 36% 적은 자원으로 트래픽 급증을 처리하며 최대 2.6배 빠른 테일 레이턴시 수렴을 달성했다.
- KAIST INA 연구실 프로젝트 페이지: https://ina.kaist.ac.kr/projects/graf/
- 원문 PDF (2021 CoNEXT 버전): https://sands.kaust.edu.sa/classes/CS294E/F21/papers/graf.pdf
- KAIST 공식 연구 아카이브(KOASAS): https://koasas.kaist.ac.kr/handle/10203/290855
- Semantic Scholar (2024 확장판, IEEE/ACM ToN): https://www.semanticscholar.org/paper/Graph-Neural-Network-Based-SLO-Aware-Proactive-for-Park-Choi/08fce6c8ecb5f41d44888af11a9aea56666a7109

**FIRM — UIUC (USENIX OSDI 2020)**

마이크로서비스 간 자원의 예측 가능한 공유를 통해 전체적인 활용도를 높이기 위한 지능적인 세밀한 자원 관리 프레임워크. 온라인 텔레메트리 데이터와 머신러닝 방법을 활용해 SLO 위반을 일으키는 마이크로서비스를 적응적으로 감지·지역화하고, 경쟁 상태의 저수준 자원을 식별하며, 동적 재할당을 통해 SLO 위반을 완화한다. 4개의 마이크로서비스 벤치마크 실험에서 SLO 위반을 최대 16배 줄이면서 전체 요청 CPU 한도를 최대 62% 절감했고, 테일 레이턴시를 최대 11배 줄였다. 강화학습(DDPG, actor-critic) 기반으로 저수준 자원 5종(CPU 시간·메모리 대역폭·LLC 용량·디스크 I/O 대역폭·네트워크 대역폭)의 한도를 재할당하며, 한도가 상·하한에 닿으면 수평 스케일아웃/인을 수행한다(원문 Table 3 Action Space·§3.4~3.5 확인). 조치 공간이 전부 자원 프로비저닝에 한정되어 브라운아웃·서킷브레이킹 등 비프로비저닝 조치는 포함하지 않는다(당초 '브라운아웃 포함'으로 기재했던 것을 원문 재확인으로 정정 — [challenges.md](../research/challenges.md) D14).
- 저자(Haoran Qiu) 공식 페이지: https://haoran-qiu.com/publication/firm-2020/
- USENIX OSDI 2020 공식 게재 페이지: https://www.usenix.org/conference/osdi20/presentation/qiu
- arXiv 원문 PDF: https://arxiv.org/pdf/2008.08509

**AGQ — Taiyuan University of Science and Technology (Future Generation Computer Systems, 2026)**

밀집 연결(dense connectivity) 구조를 적용한 시공간 그래프 신경망(STGNN)과 Q-learning을 결합해 마이크로서비스 자원을 자동으로 조정하는 프레임워크. 서비스 간 시간에 따라 변화하는 의존관계를 포착해 자원 수요를 예측하고, 예측된 수요를 바탕으로 Q-learning이 동적으로 자원 할당 전략을 조정한다.
- Venue: Future Generation Computer Systems (Elsevier), vol. 174, article 107909, 2026
- 저자: P. Liang, Y. Xun, J. Cai, H. Yang
- ScienceDirect (Abstract): https://www.sciencedirect.com/science/article/abs/pii/S0167739X25002043
- ACM DL (DOI 등록): https://dl.acm.org/doi/10.1016/j.future.2025.107909
- 본 연구와의 관계: GNN 기반 위상 인지 예측 + 학습 기반(Q-learning) 조치 결정이라는 조합을 이미 갖추고 있어 겹침 위험이 있으나, 조치 공간이 스케일링(자원 할당) 단일 축에 한정되어 있고 신뢰도 구간별 대응 개념이 없다는 점이 핵심 차별점이다.

**GraphGRU — 중국과학원 선전첨단기술연구원 (IEEE ICPADS 2022, 2023 게재)**

대규모 프로덕션 클러스터에서의 자원 사용량 예측을 위한 그래프 신경망 모델. 기존 RNN 계열이 단일 노드의 과거 데이터만으로 예측하는 한계를 지적하며, GAT(Graph Attention Network) 기반으로 클러스터 관점에서 자원 사용량을 예측한다. DTW(Dynamic Time Warping) 알고리즘으로 여러 물리 노드 간의 그래프 구조를 동적으로 구성하고, 수평·수직 데이터를 함께 학습하는 데이터 보완 방식을 사용한다. 알리바바의 실제 대규모 프로덕션 클러스터 데이터셋으로 검증했으며, 기존 딥러닝 방법 대비 최대 48.27%의 예측 정확도 개선을 달성했다.
- 참고 링크: https://library.sogang.ac.kr/eds/detail/edseee_edseee.10077915
- 본 연구와의 관계: GAT를 마이크로서비스 환경에 이미 적용한 선례로, 축 ①(위상 인지 예측)에서 직접 겹친다. 다만 DTW 기반 동적 그래프 구성(시계열 유사도 기반)을 사용하는 반면, 본 연구는 실제 서비스 호출 관계 기반 정적 그래프를 사용하며, 예측에서 그치고 조치·신뢰도 구간 대응(축 ②·③)은 다루지 않는다.

### 기타 관련 연구

**[신뢰도 산출 방식]**

- **Gal & Ghahramani (2016)**, "Dropout as a Bayesian Approximation", ICML 2016. MC Dropout을 처음 제안한 논문. 신경망 추론 시 Dropout을 끄지 않고 유지한 채 여러 번 반복 추론함으로써 베이지안 근사 방식으로 모델의 불확실성을 추정하는 기법을 제안한다. 본 연구와의 관계: 신뢰도 산출 방식 후보 비교 시 MC Dropout 측 근거로 검토했으나, 추론 시 반복 순전파가 필요해 즉각 반응이 핵심인 본 연구에는 불리하다고 판단해 최종 미채택. [arXiv](https://arxiv.org/abs/1506.02142) · [PMLR](https://proceedings.mlr.press/v48/gal16.html)
- **Lakshminarayanan, Pritzel & Blundell (2017)**, NeurIPS 2017. Deep Ensemble을 제안한 논문. 서로 다르게 초기화된 여러 신경망을 독립적으로 학습시킨 뒤 예측값들의 분산으로 불확실성을 추정하는 기법을 제시하며, MC Dropout과의 비교 실험을 통해 Deep Ensemble이 더 신뢰할 수 있는 불확실성 추정치를 제공함을 실증했다. 본 연구와의 관계: 신뢰도 산출 방식의 핵심 채택 근거 논문. [arXiv](https://arxiv.org/abs/1612.01474) · [NeurIPS](https://papers.nips.cc/paper/2017/hash/9ef2ed4b7fd2c810847ffa5fa85bce38-Abstract.html)

**[GNN 모델 선정]**

- **Veličković et al. (2018)**, "Graph Attention Networks", ICLR 2018. GAT를 처음 제안한 논문. masked self-attention 방식으로 이웃마다 다른 가중치를 학습하도록 설계했다. 14,000회 이상 피인용(Google Scholar 기준). 본 연구와의 관계: GAT 채택의 근본 근거 논문 — "노드마다 다른 가중치를 부여할 수 있다"는 핵심 주장이 커넥션풀 시나리오(서비스별 중요도 차등)를 직접 뒷받침한다. [arXiv](https://arxiv.org/abs/1710.10903) · [OpenReview](https://openreview.net/forum?id=rJXMpikCZ)
- **Kipf & Welling (2017)**, "Semi-Supervised Classification with Graph Convolutional Networks", ICLR 2017. GCN을 제안한 논문. 30,000회 이상 피인용. 본 연구와의 관계: GAT를 채택하며 기각한 비교 대상 — "GCN은 이웃을 균등하게 취급한다"는 GAT 채택 근거(§2-A)가 이 논문의 핵심 메커니즘을 가리킨다. [arXiv](https://arxiv.org/abs/1609.02907)
- **Hamilton, Ying & Leskovec (2017)**, "Inductive Representation Learning on Large Graphs", NeurIPS 2017. GraphSAGE를 제안한 논문. 각 노드마다 이웃을 고정된 크기로 샘플링해 집계하는 귀납적(inductive) 학습 방식을 제시한다. 10,000회 이상 피인용. 본 연구와의 관계: GAT를 채택하며 기각한 비교 대상 — GraphSAGE의 강점(동적·대규모 그래프에서의 확장성)이 본 연구의 벤치마크(11개 노드, 고정 위상)에는 해당하지 않는다. [arXiv](https://arxiv.org/abs/1706.02216)

**[LSTM 비교]**

- **Hochreiter & Schmidhuber (1997)**, "Long Short-Term Memory", Neural Computation, 9(8), 1735-1780. LSTM을 제안한 논문. 기존 RNN의 기울기 소실 문제를 게이트 메커니즘으로 해결했다. 100,000회 이상 피인용. 본 연구와의 관계: §4-3 baseline 3번(LSTM 기반 예측)의 근거 논문 — GRU나 Transformer가 아닌 LSTM을 택한 이유는 "가장 우수한 시계열 모델"이 아니라 "위상 정보 부재를 대표하는 가장 표준적인 모델"이 필요했기 때문이다. [원문(MIT Press)](https://www.bioinf.jku.at/publications/older/2604.pdf)
