# 신뢰도 기반 이질적 조치 선택을 통한 MSA 연쇄 장애 선제 대응 아키텍처

> **상태**: 프로포절(연구계획서) 준비 단계 — 2026년 9월(2학기) 기준, 실험 미착수.
> **2026-09-24 범위 재설계**: GNN이 주력 예측기에서 **검증 대상(비교군)** 으로 내려가고, 기여 서술이 "3축 나열"에서 **"선제 맥락의 조치 선택"이라는 결합**으로 바뀌었다. 경위는 [docs/feedback/0923_scope_redesign_proposal.md](docs/feedback/0923_scope_redesign_proposal.md).

## Abstract

모놀리식에서 쿠버네티스(K8s) 기반 분산 아키텍처로 전환하는 과정에서 마이크로서비스(MSA) 간 연쇄 장애(cascading failure) 위험이 급증한다. 기존 K8s HPA는 (1) 사후반응적이고, (2) 상태성 리소스 병목에서 Thundering Herd를 유발하며, (3) 서비스 간 위상 정보를 반영하지 못하고, (4) **스케일아웃이 물리적으로 불가능한 노드**(LLM 추론)에는 처방 자체가 성립하지 않는다.

본 연구의 주장은 이것이다 — **연쇄 장애는 자원 증설만으로 대응할 수 없으며, 선제 대응 특유의 세 제약 하에서 신뢰도에 따라 이질적 조치를 선택하는 정책이 단일 조치 정책보다 우월하다.**

선제 대응과 신뢰도 기반 조치 선택은 각각 단독으로는 선행연구가 다룬다(전자는 GRAF·DeepScaler·AGQ, 후자는 Safe Remediation). 그러나 **결합하면 사후 복구에는 정의상 존재하지 않는 세 제약이 생긴다** — (a) 예측 불확실성이 진단 불확실성보다 크고, (b) 장애가 오지 않았을 때의 오탐 비용이 존재하며, (c) 조치 실행시간이 예측 리드타임 안에 들어와야 한다. 본 연구는 이 제약 하에서 조치별 임계값 `θₐ`를 유도하는 Policy Engine을 제안하고, **(b)와 (c)를 실측**해 그 필요성을 실증한다.

## Motivation

- 모놀리식 → K8s 분산 전환 시 연쇄 장애 위험 급증
- **자원 증설이 답이 아닌 두 가지 경우가 실재한다**:
  - **역효과** — DB 커넥션풀 고갈에서 레플리카를 늘리면 DB 커넥션 총량이 늘어 상황이 악화된다
  - **불가능** — LLM 추론 노드는 GPU가 단일하면 레플리카를 늘릴 수 없다 (업계에서는 이미 품질 저하로 대응한다 — 본 연구는 이를 *새로운 발견*이 아니라 **주장의 두 번째 실증 사례**로 쓴다)
- 증상이 동형인데 정답 조치가 상충하는 구간이 존재하며(커넥션풀 고갈 ↔ 다운스트림 지연), 여기서 **틀린 조치는 무조치보다 나쁠 수 있다**

## Research Goal

선제 대응 특유의 제약(예측 불확실성 · 오탐 비용 · 조치 리드타임) 하에서 신뢰도에 따라 이질적 조치를 선택하는 Policy Engine을 설계하고 검증한다.

| 축 | GRAF | FIRM | DeepScaler | AGQ | **Safe Remediation** | 본 연구 |
|---|---|---|---|---|---|---|
| **개입 시점** | 선제 | 선제 | 선제 | 선제 | **사후** (RCA 진단 후) | **선제** |
| 예측 모델 | GNN(MPNN) | SVM | STGNN | STGNN+Q-learning | (RCA 외부화) | **①규칙/②GBM+위상feature/③GAT 비교** |
| 조치 공간 | 자원 할당 | 자원 재할당(전부 프로비저닝) | 자원 프로비저닝 | 자원 할당 | **런북형 복구 12종** | **질적 이질 4종** (CB/Bulkhead/Scale-out/Brownout) |
| 불확실성 활용 | 없음 | 없음 | 없음 | 없음 | **있음** (앙상블 M=5) | **있음** |
| **임계값 유도** | — | — | — | — | **조치 무관** (`𝝉(s)` 균일) | **조치별 `θₐ`** |
| **오탐 비용 / 리드타임 제약** | 미고려 | 미고려 | 미고려 | 미고려 | **정의상 부재** | **명시적 모델링** |

## Method

- **예측 레이어 — 위상 정보의 3단 사다리**: ① 규칙/임계값(위상 없음) → ② **GBM + 이웃 집계 feature**(주력) → ③ GAT(위상 학습, 검증 대상). ①→②가 *위상 정보 자체의 기여*를, ②→③이 *표현 학습의 기여*를 분리 측정한다. 출력은 병목 유형 다중 클래스 + 불확실성이며, **CPU만으로 학습·추론한다.**
- **의사결정 계층 (핵심 기여)**: 기대비용 최소화 비용함수. 신뢰도 구간을 손으로 긋지 않고 조치별 임계값 `θₐ = Dₐ·Rₐ / (mₐ·L − Dₐ(1−Rₐ))`가 유도된다. **완화효과 `mₐ`가 병목 유형에 의존**하므로 같은 조치가 시나리오마다 다른 값을 갖는다.
- **실행 계층 — 이질적 조치 4종**: Circuit Breaker(차단) / Bulkhead(동시성 제한) / Scale-out(자원 증설) / Brownout(품질 저하) + 무조치. Resilience4j로 애플리케이션 레이어에서 수행하므로 서비스 메시에 의존하지 않는다.
- **실험**: 자작 Spring 구성 6~7개 서비스(의존 깊이 4단) + LLM 노드(Ollama, 레플리카 1 고정) + PostgreSQL. Locust 트래픽 + **앱 레벨 장애 주입**. 반응형 HPA·규칙 기반·단일 조치 정책·무조치와 비교하며, Online Boutique는 외부 타당성 보조 검증으로 둔다.

### 장애 시나리오

| # | 시나리오 | 정답 조치 | 검증 대상 |
|---|---|---|---|
| S1 | DB 커넥션풀 고갈 | **Bulkhead** | 증설이 **역효과** |
| S2 | LLM 노드 포화 | **Brownout** | 증설이 **불가능** |
| S3 | 다운스트림 지연 | **Circuit Breaker** | **S1과의 증상 동형 쌍** |

S1과 S3는 관측 증상이 유사하지만 정답이 상충한다 — S1에 Circuit Breaker를 걸면 재시도 증폭으로 악화된다. 이 쌍이 *"신뢰도에 따라 조치를 가른다"* 의 필요성을 실증한다.

자세한 설계 근거와 심사 방어 논리는 [docs/proposal.md](docs/proposal.md)를 참고.

## Results

현재 프로포절 준비 단계로 **실험은 착수하지 않았다**. 산출물 현황은 [research/outputs.md](research/outputs.md)에 정리되어 있다.

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
│   ├── environment.md        # 실험 환경 사양 · 제약 · 실험 프로토콜
│   ├── challenges.md         # 의사결정 및 팩트체크 로그
│   ├── outputs.md            # 산출물 현황
│   └── study_notes/          # 연구를 위한 학습 커리큘럼 및 주제별 학습 노트
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
