# === DevKit 라우팅 규칙 (자동 생성) ===
<!-- setup-all 이 설치된 컴포넌트 블록만 골라 프로젝트 CLAUDE.md 최상단에 주입한다.
     이미 이 블록이 있으면 교체한다(중복 주입 금지). -->

## [ontology 설치 시]
도메인/비즈니스 개념 질문은 devkit/ontology.yaml을 먼저 참조한다.
개체의 코드 근거가 필요하면 source_refs를 따라간다.
온톨로지 수정 시 ontology.yaml을 갱신하고 manual: true로 표시한다.

## [harness 설치 시]
복잡한 다단계 작업은 구축된 Agent Team이 자동 발동한다.
(실행에는 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 필요)

## 하네스: MSA 연쇄 장애 완화 연구 지원

**목표:** 연구계획서/온톨로지 문서 일관성 검증, 선행연구 비교 논증 보강, 실험 설계 검토, 향후 GAT/Policy Engine Python 구현 착수 지원.

**트리거:** 이 프로젝트(연구계획서, 온톨로지, 실험 설계, GNN/Policy Engine 구현) 관련 작업 요청 시 `msa-research-orchestrator` 스킬을 사용하라. 단순 질문은 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-08-05 | 초기 구성 (research-writer/experiment-designer/ml-implementer 3-agent Expert Pool) | 전체 | `/devkit:setup-all` ontology+harness 최초 설치 |
