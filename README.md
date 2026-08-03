# 신뢰도 기반 GNN 예측을 활용한 MSA 연쇄 장애 선제 대응 아키텍처

> **상태**: 프로포절(연구계획서) 준비 단계 — 2026년 7월(1학기 종료 시점) 기준, 실험 미착수.

## Abstract

모놀리식에서 쿠버네티스(K8s) 기반 분산 아키텍처로 전환하는 과정에서 마이크로서비스(MSA) 간 연쇄 장애(cascading failure) 위험이 급증한다. 기존 K8s HPA는 (1) 사후반응적이라는 점, (2) 상태성 리소스 병목에서 Thundering Herd를 유발할 수 있다는 점, (3) 서비스 간 위상(topology) 정보를 반영하지 못한다는 점, (4) 시계열 전용 모델은 장애 전파 경로를 포착하지 못한다는 점에서 한계를 가진다.

본 연구는 그래프 어텐션 네트워크(GAT)로 서비스 호출 관계를 학습해 장애를 선제적으로 예측하고, Deep Ensemble(N=5) 기반 신뢰도 추정치에 따라 조치 강도를 차등화하는 **신뢰도 구간별 대응(confidence-tiered response)** 정책 엔진을 제안한다. 관련 연구(GRAF, FIRM, AGQ, GraphGRU)는 "위상 인지 예측", "이질적 조치 선택", "신뢰도 구간별 대응"이라는 세 축 중 최소 하나 이상을 다루지 않으며, 본 연구는 이 세 축을 모두 충족하는 위치에 있다. Online Boutique 벤치마크 위에서 Circuit Breaker, Traffic Shedding, K8s Scale-up, Read Redirection, Brownout(비핵심 기능 차단) 등 이질적 조치를 신뢰도 구간에 따라 선택적으로 트리거하는 아키텍처를 검증할 예정이다.

## Motivation

- 모놀리식 → K8s 분산 전환 시 연쇄 장애 위험 급증
- 기존 K8s HPA의 4가지 한계: 사후반응성 / Thundering Herd 유발 가능성 / 위상 정보 부재 / 시계열 전용 모델의 전파경로 포착 실패
- 선행연구(GRAF·FIRM·AGQ·GraphGRU)를 원문까지 재확인한 결과, 세 논문(GRAF·FIRM·AGQ) 모두 모델이 낸 예측을 확신 정도와 무관하게 그대로 실행하는 구조 — **신뢰도 구간별 대응**은 문헌상 비어 있는 자리

## Research Goal

위상 인지형 예측(GRAF류)과 학습 기반 조치 선택(FIRM류)의 교집합에, **신뢰도 구간별 대응**이라는 세 번째 축을 더한 Policy Engine을 설계하고 검증한다.

| 축 | GRAF | FIRM | AGQ | GraphGRU | 본 연구 |
|---|---|---|---|---|---|
| 예측 모델 | GNN(MPNN, 위상 반영) | SVM(위상 미반영) | STGNN+Q-learning | GAT(DTW 동적 그래프) | GAT(정적, 실 호출관계 기반) |
| 조치 공간 | 자원 할당 | 자원 재할당(다차원, 전부 프로비저닝) | 자원 할당 | 없음(예측만) | 질적 이질(CB/Shedding/Scale/Redirect/Brownout) |
| 신뢰도 구간별 대응 | 없음 | 없음 | 없음 | 없음 | **있음 (신규 기여)** |

## Method

- **AI 예측 레이어**: 정적 GAT(Graph Attention Network) + Deep Ensemble(N=5)로 예측값과 신뢰도를 함께 산출. 무거운 STGNN 대신 노드 feature에 슬라이딩 윈도우 시계열 통계량을 주입해(TA-GAT) 정적 그래프 위에서 시간적 추세와 위상 전파를 함께 학습. 출력은 flatten이 아닌 공유 per-node head 기반 노드 레벨 예측 채택(서비스별 위험도 산출, 그래프 크기 확장에도 파라미터 구조 유지).
- **의사결정 계층 (핵심 Contribution)**: 신뢰도 구간(고/중/저)에 따라 조치 강도를 달리하는 Policy Engine. 고신뢰도=적극적 조치 / 중간신뢰도=저비용·가역적 조치 / 저신뢰도=보류.
- **실행 계층**: Circuit Breaker, Traffic Shedding, K8s Scale-up, Read Redirection, Brownout 5종 Actuator.
- **실험**: Online Boutique(11~12개 서비스) 벤치마크, Locust/k6 트래픽 생성 + Istio/Chaos Mesh 장애주입 결합, 반응형 HPA·규칙기반 Policy·LSTM baseline·GRAF류 baseline과 비교.

자세한 아키텍처 설계 근거와 심사 방어 논리는 [docs/proposal.md](docs/proposal.md)를 참고.

## Results

현재 프로포절 준비 단계로 **실험은 착수하지 않았다**. 지금까지의 산출물(연구계획서, 문헌 비교표, 의사결정 로그 등)과 향후 예정된 산출물은 [research/outputs.md](research/outputs.md)에 정리되어 있다.

## Repository Structure

```text
.
├── README.md
│
├── docs/
│   └── proposal.md          # 연구계획서 (아키텍처 설계, 실험 설계, 심사 방어 전략, 참고문헌)
│
├── research/
│   ├── overview.md           # 연구 소개 요약
│   ├── timeline.md           # 연구 진행 과정 · 일정 · 리스크 관리
│   ├── challenges.md         # 의사결정 및 팩트체크 로그
│   ├── outputs.md            # 산출물 현황
│   └── study-notes/          # 연구를 위한 학습 커리큘럼 및 주제별 학습 노트
│
├── code/                     # (착수 전, 폴더 구조만 준비)
│   ├── backend/              # Policy Engine / API
│   ├── model/                # GNN(GAT) 예측 모델
│   ├── simulator/             # 트래픽 생성 · 장애주입 스크립트
│   └── utils/
│
├── data/                     # (착수 전, 폴더 구조만 준비)
│   ├── raw/
│   ├── processed/
│   └── sample/
│
├── assets/                   # (착수 전, 폴더 구조만 준비)
│   ├── diagrams/
│   ├── figures/
│   └── images/
│
└── temp/                     # 원본 작업 노트 (PDF), 정리 작업의 원본 자료
```

> `code/`, `data/`, `assets/`는 실험 착수 전이라 아직 내용은 없고 폴더 구조만 미리 준비해 두었다(`.gitkeep`으로 git에 추적). `LICENSE`는 아직 결정 전이라 생성하지 않았다.

## Related Links

- GRAF (KAIST INA Lab): https://ina.kaist.ac.kr/projects/graf/
- FIRM (USENIX OSDI 2020): https://www.usenix.org/conference/osdi20/presentation/qiu
- AGQ (Future Generation Computer Systems): https://www.sciencedirect.com/science/article/abs/pii/S0167739X25002043
- GraphGRU: https://library.sogang.ac.kr/eds/detail/edseee_edseee.10077915

전체 참고문헌 목록과 각 논문과의 관계는 [docs/proposal.md](docs/proposal.md#참고문헌-references)를 참고.
