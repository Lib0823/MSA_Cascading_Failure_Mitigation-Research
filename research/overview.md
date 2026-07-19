# 연구 소개 (Overview)

> 상세 내용은 [docs/proposal.md](../docs/proposal.md)를 참고. 이 문서는 연구를 처음 접하는 사람이 3~5분 안에 맥락을 잡을 수 있도록 요약한 것이다.

## 연구 배경

모놀리식 아키텍처에서 쿠버네티스(K8s) 기반 마이크로서비스(MSA)로 전환하는 과정에서, 서비스 간 의존관계로 인한 연쇄 장애(cascading failure) 위험이 급증한다. 기존 K8s HPA(Horizontal Pod Autoscaler)는 다음 네 가지 한계를 가진다.

1. 사후반응적으로만 동작한다.
2. 병목이 DB 커넥션풀 같은 상태성 리소스일 때, 단순 스케일아웃이 Thundering Herd를 유발해 상태를 오히려 악화시킬 수 있다.
3. 서비스 간 호출 위상(topology) 정보를 반영하지 못한다.
4. 시계열 전용 모델은 장애 전파 경로를 포착하지 못한다.

## 문제 정의

관련 선행연구(GRAF, FIRM, AGQ, GraphGRU)를 "예측 모델의 위상 인지 여부", "조치 공간의 이질성", "신뢰도 구간별 대응 여부"라는 세 축으로 분해해 재검토한 결과, 네 연구 모두 세 축 중 최소 하나 이상을 다루지 않고 있음을 확인했다. 특히 세 논문(GRAF·FIRM·AGQ) 모두 모델이 낸 예측을 확신 정도와 무관하게 그대로 실행하는 구조이며, **신뢰도 구간별 대응**은 문헌상 비어 있는 자리다.

## 연구 목적

서비스 호출 관계를 반영한 GNN 예측과, 예측의 신뢰도(확신도)에 따라 조치 강도를 달리하는 정책 엔진을 결합한 아키텍처를 제안하고 검증하는 것이 목적이다. 위상 인지형 예측(GRAF류)과 이질적 조치 선택(FIRM류)의 교집합에, 신뢰도 구간별 대응이라는 세 번째 축을 더하는 것이 핵심 기여다.

## 연구 방법

- **예측 레이어**: 정적 GAT(Graph Attention Network) + Deep Ensemble(N=5)로 예측값과 신뢰도를 함께 산출.
- **의사결정 계층**: 신뢰도 구간(고/중/저)에 따라 조치 강도를 달리하는 Policy Engine.
- **실행 계층**: Traffic Shedding, Circuit Breaker, K8s Scale-up, Read Redirection 4종 Actuator.
- **실험**: Online Boutique(11~12개 서비스) 벤치마크 위에서 Locust/k6 트래픽 생성 + Istio/Chaos Mesh 장애주입을 결합, 반응형 HPA·규칙기반 Policy·LSTM baseline·GRAF류 baseline과 비교.

자세한 아키텍처 설계와 근거는 [docs/proposal.md](../docs/proposal.md) §2, 비교 실험 설계는 §4를 참고.

## 기대 효과

- 위상 정보를 반영한 예측이 시계열 전용 모델보다 연쇄 장애 대응에 효과적임을 실증.
- 신뢰도 구간별 대응을 통해, 자원할당 중심 접근이 상태성 리소스 병목에서 일으키는 역효과(Thundering Herd)를 완화.
- GRAF·FIRM·AGQ·GraphGRU 네 갈래로 나뉜 선행연구 사이의 공백(신뢰도 구간별 대응)을 채우는 학술적 기여.

## 현재 진행 상태

2026년 7월, 1학기 종료 시점 기준 프로포절(연구계획서) 준비 단계이며 실험은 아직 착수하지 않았다. 진행 경과는 [timeline.md](timeline.md), 그동안의 의사결정과 팩트체크는 [challenges.md](challenges.md)를 참고.
