---
name: msa-research-orchestrator
description: "MSA 연쇄 장애 완화 연구(신뢰도 기반 GNN 예측 + Policy Engine) 프로젝트의 Agent Team(research-writer, experiment-designer, ml-implementer)을 조율한다. 연구계획서/문서 검토, 온톨로지 일관성 점검, 선행연구 비교 보강, 실험 설계 검토, GAT/Policy Engine 구현 착수 등 이 프로젝트 관련 작업 요청 시 반드시 사용. '프로포절 전체 리뷰해줘', '이 프로젝트 온톨로지/실험/구현 다시 봐줘', '지난 리뷰 이어서', '전 결과 기반으로 보완', '~만 다시 검토' 같은 후속 요청에도 사용한다. 이 프로젝트와 무관한 일반 코딩 질문에는 사용하지 않는다."
---

# MSA 연쇄 장애 완화 연구 — Agent Team 오케스트레이터

**실행 모드: 에이전트 팀(Expert Pool)** — 요청 하나가 보통 전문가 1명으로 충분하지만, 문서 전체 리뷰처럼 여러 영역이 겹치는 요청은 관련 에이전트를 함께 소집해 `SendMessage`로 교차 확인하게 한다.

## 팀 구성

| 에이전트 | 전담 영역 | 사용 스킬 |
|---|---|---|
| `research-writer` | 문서·온톨로지 개념 일관성, 선행연구 비교 논증 | `ontology-consistency-check`, `related-work-strengthen` |
| `experiment-designer` | 벤치마크·baseline·ablation·평가지표·라벨 누수 | `experiment-design-review` |
| `ml-implementer` | GAT/Deep Ensemble/Policy Engine/Actuator Python 구현 착수 지원 | `gnn-policy-implementation-support` |

모든 `Agent` 호출에는 `model: "opus"`를 명시한다.

## Phase 0: 컨텍스트 확인

1. `_workspace/` 폴더가 있으면 이전 실행의 산출물이 있다는 뜻 — 사용자가 "이어서"/"보완"/"~만 다시" 요청이면 **부분 재실행**(관련 에이전트만 재호출, 이전 산출물을 읽고 개선), 새 문서/새 관점 요청이면 기존 `_workspace/`를 `_workspace_prev/`로 옮기고 **새 실행**.
2. `_workspace/`가 없으면 **초기 실행**.
3. 요청 범위를 판단해 라우팅 (아래 표).

## 라우팅

| 요청 예시 | 소집 에이전트 |
|---|---|
| "온톨로지/문서 일관성 확인해줘", "용어 통일됐는지 봐줘" | `research-writer` 단독 |
| "선행연구 비교표/차별점 논증 강화" | `research-writer` 단독 (`related-work-strengthen`) |
| "실험 설계/baseline/ablation 검토" | `experiment-designer` 단독 |
| "GAT/Policy Engine 구현 시작", "code/model 스켈레톤" | `ml-implementer` 단독 |
| "프로포절 전체 리뷰", "심사 대비 최종 점검" | 3명 전원 소집 — 아래 "전체 리뷰 흐름" 참조 |
| 두 영역이 겹치는 요청(예: "실험 설계가 온톨로지 규칙 지키는지") | 관련 2명 소집, `SendMessage`로 교차 확인 지시 |

## 전체 리뷰 흐름 (3명 소집 시)

1. `TeamCreate`로 세 에이전트를 소집한다.
2. `TaskCreate`로 각자에게 자기 영역 검토를 할당한다(의존관계 없음 — 병렬 시작 가능).
3. 각 에이전트는 산출물을 `_workspace/{agent-name}_review.md`에 저장한다.
4. 한 에이전트가 자기 영역 밖 문제(예: `research-writer`가 실험 설계 불일치 발견)를 찾으면 `SendMessage`로 해당 에이전트에게 전달 — 리더는 이 전달이 실제로 이뤄졌는지 최종 보고에서 확인한다.
5. 전원 완료 후 리더(오케스트레이터)가 `_workspace/*_review.md`를 종합해 우선순위(심사 방어에 치명적/보강 권장/사소함)별로 정리한 최종 보고서를 사용자에게 제시한다. 중간 산출물(`_workspace/`)은 보존한다.

## 데이터 전달

- **태스크 기반**: 3명 소집 시 `TaskCreate`/`TaskUpdate`로 진행상황 추적.
- **파일 기반**: `_workspace/{agent}_review.md`에 각자 산출물 저장, 파일명 규칙 `{phase}_{agent}_{artifact}.md`.
- **메시지 기반**: 영역 교차 발견 사항은 `SendMessage`로 즉시 전달.

## 에러 핸들링

- 특정 에이전트가 실패(응답 없음/에러)하면 1회 재시도 후, 재실패 시 해당 영역 없이 진행하고 최종 보고서에 "누락된 검토 영역"으로 명시한다. 다른 에이전트의 산출물은 그대로 유지한다.
- `devkit/ontology.yaml`이 없는 상태에서 `research-writer`가 소집되면, 온톨로지 없이 가능한 문서 간 대조만 수행하고 그 사실을 보고에 명시한다.

## 후속 작업

- "지난 리뷰 이어서", "실험 설계만 다시", "온톨로지 갱신했으니 재검증" 같은 요청은 Phase 0의 부분 재실행 경로를 탄다 — 관련 에이전트만 재소집하고 이전 `_workspace/` 결과를 입력으로 준다.

## 테스트 시나리오

- **정상 흐름**: "온톨로지 문서 일관성 확인해줘" → `research-writer` 단독 소집 → `ontology-consistency-check` 스킬 실행 → 격차 표 갱신 보고.
- **에러 흐름**: 3명 전체 리뷰 도중 `experiment-designer`가 응답 없음 → 1회 재시도 → 재실패 → 나머지 2명 결과로 보고서 작성 + "실험 설계 검토 누락" 명시.
