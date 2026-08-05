---
name: related-work-strengthen
description: "GRAF·FIRM·AGQ·GraphGRU 4편의 선행연구 대비 이 연구(신뢰도 구간별 대응 Policy Engine)의 차별점 논증을 검토·보강한다. '선행연구 비교표 다듬어줘', 'FIRM이랑 뭐가 다른지 논증 강화', '차별점 약한 것 같아', 'Concern7 방어 논리 봐줘', '3축 비교 표 검토' 요청 시 반드시 사용."
---

# Related Work Strengthen

이 연구의 핵심 기여는 "GRAF(위상 인지 예측) ∩ FIRM(적응 조치 선택)의 교집합에 신뢰도 구간별 대응(Confidence-Tiered Response)이라는 세 번째 축을 더한 것"이다. 이 스킬은 그 논증이 문헌적으로 정확하고 방어 가능한지 점검한다.

## 3축 비교 프레임

| 축 | GRAF | FIRM | AGQ | GraphGRU | 본 연구 |
|---|---|---|---|---|---|
| 위상 인지 예측 (Topology-Aware Prediction) | ✅ (MPNN) | ❌ (SVM) | ✅ (STGNN) | ✅ (GAT+DTW) | ✅ (GAT, 정적) |
| 조치 공간 이질성 (Action Space Heterogeneity) | ❌ | 부분적(전부 프로비저닝, 이질적이지 않음) | ❌ | ❌(예측만) | ✅ (CB/Shedding/Redirect/Scale/Brownout) |
| 신뢰도 구간별 대응 (Confidence-Tiered Response) | ❌ | ❌ | ❌ | ❌ | ✅ (신규 기여, `Contribution_Main`) |

`devkit/ontology.yaml`의 `usesModel`/`coversAxis`/`lacksAxis` 관계가 이 표의 근거다. FIRM은 "조치 적응 선택"을 하지만 조치가 전부 프로비저닝(자원 재할당류)이라 **이질적이지 않다** — 이 구분(이질성 vs 단순 적응)을 흐리면 심사에서 "FIRM도 하지 않냐"는 반박(Concern7)에 취약해진다.

## 절차

1. **표 정확성 검증**: `docs/proposal.md` §3(선행연구)의 비교표가 위 3축·4편 구조와 정확히 일치하는지 확인한다. `venue`/`year`/`verifiedGraphSize`(GRAF 6~10노드, FIRM 15~41(비GNN), AGQ ~13노드) 등 인용 수치가 `individuals.md`/`ontology.ttl`과 일치하는지 대조한다.
2. **논증 강도 점검**: 다음 세 가지 오류 패턴을 찾는다.
   - **혼동**: "조치 적응 선택"(FIRM이 하는 것)과 "조치 공간 이질성"(본 연구만 하는 것)을 같은 것처럼 서술
   - **과장**: 선행연구가 부분적으로 다루는 축을 "전혀 안 다룬다"고 서술(예: AGQ의 위상 인지 예측 능력을 과소평가)
   - **누락**: 4편 모두가 `Axis_ConfidenceTieredResponse`를 `lacksAxis`한다는 핵심 문장이 비교표 근처에 명시적으로 없음
3. **개체 사실 확인**: `GRAF`(flatten readout 확장성 한계 자인, D13), `FIRM`(전이학습은 선례로만 인용, GNN 아님), `AGQ`("수백 노드" 실험은 비공개·비재현), `GraphGRU`(DTW 동적그래프+예측에서 그침) — 이 개체별 주석이 논문에서 정확히 재현되는지 확인한다. 근거 없이 선행연구를 폄훼하는 서술은 지적한다.
4. **결과 보고**: 표/문단 단위로 "정확/보강 필요/과장·오류"를 표시하고 대안 문구를 제시한다.

## 하지 않는 것

- 선행연구 원문을 새로 조사하지 않는다(이미 `individuals.md`에 정리된 사실 기반). 원문 재확인이 필요하면 사용자에게 요청한다.
- 실험 결과로 차별점을 입증하는 것은 다루지 않는다(실험 미착수 — `experiment-design-review`의 영역).
