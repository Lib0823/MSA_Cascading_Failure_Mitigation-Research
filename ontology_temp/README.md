# MSA 연쇄 장애 완화 연구 온톨로지 (작업본)

> 본 디렉터리는 「신뢰도 기반 GNN 예측을 활용한 MSA 연쇄 장애 선제 대응 아키텍처」연구의
> 도메인 지식을 온톨로지로 정형화하기 위한 **작업 자료(temp)** 다. 저장소의
> [docs/proposal.md](../docs/proposal.md) · [research/challenges.md](../research/challenges.md) ·
> [research/overview.md](../research/overview.md) · [README.md](../README.md)를 원본으로 추출·구조화했다.

## 목적

이 연구는 (1) 서비스 호출 **위상(topology)** 을 학습하는 GNN 예측, (2) 예측의 **신뢰도(불확실성)**,
(3) 신뢰도 구간별로 **이질적 조치(Actuator)** 를 선택하는 Policy Engine을 결합한다. 이 세 축과
그 하위 개념·관계를 온톨로지로 고정하면 다음이 가능해진다.

- 논문 개념·용어의 **일관성 검증**(같은 개념이 문서마다 다르게 쓰이는지)
- 선행연구(GRAF/FIRM/AGQ/GraphGRU)와 본 연구의 **차별점을 관계 그래프로 명시**
- Policy Engine의 **의사결정 흐름을 개체·속성으로 추적**(입력 → p_eff → 조치)
- 지식그래프/RAG·논문 그림(개념도) 생성의 **스키마 소스**

## 파일 구성

| 파일 | 내용 | 온톨로지 요소 |
|---|---|---|
| [competency-questions.md](competency-questions.md) | 이 온톨로지가 답해야 하는 질문 목록 | 범위 정의(scope) |
| [classes.md](classes.md) | 개념(클래스) 분류 체계와 정의 | `owl:Class` 계층 |
| [properties.md](properties.md) | 개념 간 관계(객체 속성)·데이터 속성 | `owl:ObjectProperty` / `owl:DatatypeProperty` |
| [individuals.md](individuals.md) | 실제 개체(조치 5종·선행연구 4편·벤치마크·변수 등) | `owl:NamedIndividual` |
| [ontology.ttl](ontology.ttl) | 위 전체를 합친 형식 온톨로지(OWL) | RDF/Turtle 직렬화 |

## 네임스페이스

```
@prefix msacf: <https://example.org/ontology/msa-cascading-failure#> .
```

- 접두사 `msacf:` = **M**icro**s**ervice **A**rchitecture **C**ascading **F**ailure
- 식별자(클래스/속성/개체 이름)는 영문 관례(PascalCase 클래스, camelCase 속성)를 따르고,
  `rdfs:label`·`rdfs:comment`에 한국어 정의를 붙인다.
- `https://example.org/...` 는 임시 IRI다. 실제 발행 시 연구자/기관 도메인으로 교체한다.

## 범위(Scope)와 경계

**포함**: 시스템/위상, 장애·전파, 예측 모델·불확실성, 비용함수·Policy Engine, 조치 5종,
2계층 제어, 실험 설계·지표, 선행연구·비교축.

**제외**(의도적): 구체 수치 튜닝값(L·Dₐ·Rₐ·mₐ의 실측값, Δ 값 등 — 실험 이월 항목),
일정/예산, 저장소 파일 구조. 온톨로지는 **개념 스키마**를 담고 실측 파라미터는 담지 않는다.

## 출처 추적

각 클래스/속성/개체는 원본 문서의 근거 절을 `rdfs:isDefinedBy` 또는 주석으로 표기했다.
주요 근거: proposal §2-0(문제정의)·§2-A(모델)·§2-B(비용함수)·§2-C(Actuator)·§2-D(2계층)·§2-E(알고리즘)·§3(선행연구)·§4(실험),
challenges D14·E5·E6·F1·G1·G2·G4·H1~H6.

## 위치 설정 (Positioning) — 중요

**이 온톨로지는 연구 기여(contribution)가 아니라, 설계 정합성 관리·개념 정형화·발표 시각화를 위한
보조 표현물이다.** 논문에서는 Method/Contribution이 아니라 **부록 또는 각주 + 발표 보조 슬라이드**로만
위치시킨다. 자기참조적(proposal에서 추출해 proposal을 재표현)이라 "온톨로지로 무엇을 도출했다"는
프레이밍은 쓰지 않는다. 상세 근거·서술 상한·금지 프레이밍은 [research/challenges.md](../research/challenges.md) I1 참고.

## 상태

작업본(temp). 개념 누락·관계 정합성은 competency-questions.md의 질문으로 검증하며,
연구 진행에 따라 갱신한다. **논문 제출 직전, 본문↔온톨로지 정합성 점검을 한 번 돌려 낡은 부분을 맞춘다(I1).**
