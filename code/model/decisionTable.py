"""결론 결정표 — 학습된 판정을 사람이 읽는 규칙으로 환원한다 (timeline 작업 #3, J37).

왜 만드는가
===========

Policy Engine 은 예측기(GBM 앙상블, 174차원)의 출력 `(p̄, u, ŝ)` 로 조치를 고른다. 현업은 그 모델을
배포하지 않고도 *"그래서 무엇을 보고 무엇을 하라는 건가"* 에 답을 원한다. 결정표가 그 한 장이다.

두 단계 중 **학습이 들어간 것은 앞 단계뿐**이다.

=================  ==========================================  =================================
단계                내용                                         환원 방법
=================  ==========================================  =================================
관측 → 판정         지표에서 위험도 p 와 병목 유형 ŝ             **얕은 결정 트리** — 이 파일
판정 → 조치         θₐ 비교 (S1: 벌크헤드 0.46, S3: CB          **해석적** — policyEngine 의 유도
                    0.00025, 증설 ∞)                             그대로. 학습이 없다
=================  ==========================================  =================================

그래서 트리를 **같은 엔진에 넣어** 조치까지 낸다 — 잎의 장애 표본 비율을 위험도로, 잎에서 많은 장애
유형을 ŝ 로 쓴다. 결정표 = 읽을 수 있는 예측기 + 같은 엔진이다. 트리 하나에는 멤버 간 불일치가 없으므로
u = 0 이고 `p_eff` 는 잎의 비율 그대로다(κ 할인 없음).

검증
====

GBM 과 트리를 **같은 접기**(endToEnd 와 같은 GroupKFold 4접기, 접기 밖 예측)에서 비교한다.

- 정확도 — 3클래스 macro F1, 장애 표본에서 ŝ 의 정확도
- 충실도 — GBM 판정·조치와의 일치율
- 조치 결과 — 장애 없는 구간의 개입률·비용(원고 §5.4 와 같은 정의), S1 에 벌크헤드·S3 에 CB 를 건 비율
- 안정성 — 접기마다 다시 학습한 트리의 분할 지표와 임계값

GBM 쪽은 endToEnd 의 함수를 그대로 부른다(같은 접기·시드·보정). **deltaSweep.json 의 같은 Δ 행을
재현하는지 먼저 확인한다** — 재현되지 않으면 비교 기준이 원고의 수치가 아니다.

읽을 수 있는 특징
=================

174차원 중 운영자가 대시보드에서 바로 읽는 양만 남긴다. f4 는 ``p95 / 기준선 / 4`` 를 [0,1] 로 자른
값이므로 4 를 곱해 **기준선 대비 배수**로 되돌린다(4배에서 포화). f2 는 HikariCP 의
``pending / (pending + active)`` 그대로다. *"호출 경로"* 는 1·2홉 호출 대상의 최댓값이라 노드 위치와
무관하게 읽힌다 — orders 에는 payment 가 1홉, gateway 에는 2홉이다.

⚠️ 이 결과는 Docker Compose 의 3단 사슬 하나에서 나왔다. 임계값은 **이 testbed 의 값**이고, 규칙의
**형태**(무엇을 보고 무엇을 하는가)가 옮겨 갈 수 있는 산출이다.

결과 (2026-11-24, J37)
======================

깊이 2 — *호출 경로 p95 > 2배* 이면 *호출 경로 풀 대기율* 로 S1(벌크헤드)·S3(CB)를 가른다. 조치를 강제하지 않은
에피소드에서는 GBM 보다 낫고(F1 0.859 vs 0.833), 벌크헤드·CB 를 강제한 셀에서는 무너진다 — **조치가 풀 대기율을
지운다.** 1단의 "2배" 는 라벨 정의의 재발견이다(``--selfOnlyLabel``). 강제 셀을 학습에서 빼는 대조는
``--trainOn natural``. 상세는 research/challenges.md J37, 표는 docs/decision-table.md.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import numpy as np
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupKFold
from sklearn.tree import DecisionTreeClassifier

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compareTiers  # noqa: E402
import corpus  # noqa: E402
import endToEnd  # noqa: E402
from policyEngine import HOLD, PolicyEngine, defaultProfiles  # noqa: E402

NORMAL_LABEL = endToEnd.NORMAL_LABEL
FAULT_LABELS = ("S1", "S3")
CLASS_ORDER = (NORMAL_LABEL, *FAULT_LABELS)
RIGHT_ACTION = {"S1": "BULKHEAD", "S3": "CIRCUIT_BREAKER"}
WRONG_ACTION = {"S1": "CIRCUIT_BREAKER", "S3": "BULKHEAD"}
# runRemediation 이 장애와 함께 강제한 조치. NONE(무조치 강제)과 runChain 에피소드(필드 없음)는 자연 동역학이다.
FORCED_ACTIONS = ("BULKHEAD", "CIRCUIT_BREAKER")

# 174차원 배치 — buildDataset·minimalObservation 과 같다. 아래 verifyLayout 이 데이터로 확인한다.
NUM_SLOTS = 6
SELF_BLOCK = NUM_SLOTS * 4                  # 현재값·창 평균·slope·표준편차
HOP_OFFSETS = (SELF_BLOCK, SELF_BLOCK + SELF_BLOCK * 3)   # 1홉·2홉, 각각 mean·max·sum
DOWNSTREAM_OFFSET = SELF_BLOCK + 2 * SELF_BLOCK * 3       # 168 — 호출자의 [f2, f4]
STRUCTURAL_OFFSET = DOWNSTREAM_OFFSET + 2                 # 170 — [호출 대상 수, 호출자 수, 사슬 끝, 입구]
FEATURE_DIM = STRUCTURAL_OFFSET + 4                       # 174
CURRENT, WINDOW_MEAN = 0, 1
MAX_AGGREGATION = 1
SLOT_POOL_WAIT, SLOT_LATENCY, SLOT_ERROR = 1, 3, 5
LATENCY_SCALE = 4.0          # toSlots 가 p95 배수를 4 로 나눠 [0,1] 로 자른다

READABLE_FEATURES = {
    "selfLatencyRatio": "자기 p95 ÷ 기준선",
    "selfLatencyRatioMean": "자기 p95 ÷ 기준선 (10초 평균)",
    "selfErrorRate": "자기 오류율",
    "selfPoolWait": "자기 풀 대기율",
    "pathPoolWait": "호출 경로의 풀 대기율",
    "pathPoolWaitMean": "호출 경로의 풀 대기율 (10초 평균)",
    "pathLatencyRatio": "호출 경로의 p95 ÷ 기준선",
    "callerLatencyRatio": "호출자 p95 ÷ 기준선",
    "isChainEnd": "호출 대상 없음 (사슬 끝)",
    "isEntry": "입구 노드",
}


def selfColumn(stat: int, slot: int) -> int:
    return stat * NUM_SLOTS + slot


def hopColumn(hop: int, aggregation: int, stat: int, slot: int) -> int:
    return HOP_OFFSETS[hop] + aggregation * SELF_BLOCK + stat * NUM_SLOTS + slot


def pathMax(matrix: np.ndarray, stat: int, slot: int) -> np.ndarray:
    """1·2홉 호출 대상 중 최댓값 — 노드 위치와 무관한 *"호출 경로 아래쪽"* 지표."""
    return np.maximum(matrix[:, hopColumn(0, MAX_AGGREGATION, stat, slot)],
                      matrix[:, hopColumn(1, MAX_AGGREGATION, stat, slot)])


def readableFeatures(matrix: np.ndarray) -> tuple[np.ndarray, list[str]]:
    if matrix.shape[1] != FEATURE_DIM:
        raise SystemExit(f"feature 차원이 {matrix.shape[1]} 이다. {FEATURE_DIM} 를 가정했다 — "
                         "buildDataset.py 가 바뀌었으면 이 파일의 배치 상수도 고쳐야 한다")
    columns = {
        "selfLatencyRatio": LATENCY_SCALE * matrix[:, selfColumn(CURRENT, SLOT_LATENCY)],
        "selfLatencyRatioMean": LATENCY_SCALE * matrix[:, selfColumn(WINDOW_MEAN, SLOT_LATENCY)],
        "selfErrorRate": matrix[:, selfColumn(CURRENT, SLOT_ERROR)],
        "selfPoolWait": matrix[:, selfColumn(CURRENT, SLOT_POOL_WAIT)],
        "pathPoolWait": pathMax(matrix, CURRENT, SLOT_POOL_WAIT),
        "pathPoolWaitMean": pathMax(matrix, WINDOW_MEAN, SLOT_POOL_WAIT),
        "pathLatencyRatio": LATENCY_SCALE * pathMax(matrix, CURRENT, SLOT_LATENCY),
        "callerLatencyRatio": LATENCY_SCALE * matrix[:, DOWNSTREAM_OFFSET + 1],
        "isChainEnd": matrix[:, STRUCTURAL_OFFSET + 2],
        "isEntry": matrix[:, STRUCTURAL_OFFSET + 3],
    }
    assert list(columns) == list(READABLE_FEATURES)
    return np.column_stack(list(columns.values())).astype(np.float64), list(columns)


def verifyLayout(readable: np.ndarray, names: list[str], nodes: np.ndarray) -> None:
    """열 매핑을 데이터로 확인한다 — 틀리면 엉뚱한 열을 "풀 대기율" 이라 부르게 된다.

    풀은 payment 에만 있으므로(passthrough 노드는 f1·f2 가 구조적으로 없다) 다음이 성립해야 한다.
    """
    column = {name: readable[:, index] for index, name in enumerate(names)}
    chainEnd = column["isChainEnd"] == 1
    checks = {
        "사슬 끝은 payment 하나": set(nodes[chainEnd].tolist()) == {"payment"},
        "입구는 gateway 하나": set(nodes[column["isEntry"] == 1].tolist()) == {"gateway"},
        "gateway·orders 의 자기 풀 대기율은 0": float(np.abs(column["selfPoolWait"][~chainEnd]).max()) == 0.0,
        "payment 의 호출 경로 풀 대기율은 0": float(np.abs(column["pathPoolWait"][chainEnd]).max()) == 0.0,
        "payment 의 자기 풀 대기율은 0 이 아닌 값이 있다": float(column["selfPoolWait"][chainEnd].max()) > 0.0,
        "orders·gateway 의 호출 경로 풀 대기율은 0 이 아닌 값이 있다":
            float(column["pathPoolWait"][~chainEnd].max()) > 0.0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("열 배치 검증 실패 — " + ", ".join(failed))
    print(f"✅ 열 배치 검증 {len(checks)}건 통과")


def probabilitiesInOrder(model: DecisionTreeClassifier, features: np.ndarray) -> np.ndarray:
    """학습 접기에 클래스가 빠져도 열 순서를 CLASS_ORDER 로 맞춘다."""
    raw = model.predict_proba(features)
    ordered = np.zeros((len(features), len(CLASS_ORDER)))
    for index, name in enumerate(model.classes_):
        ordered[:, CLASS_ORDER.index(name)] = raw[:, index]
    return ordered


def summarizeProbabilities(probabilities: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(위험도, ŝ, 3클래스 argmax). ŝ 는 endToEnd 와 같이 **정상을 뺀** argmax 다."""
    risk = 1.0 - probabilities[:, CLASS_ORDER.index(NORMAL_LABEL)]
    faultIndexes = [CLASS_ORDER.index(name) for name in FAULT_LABELS]
    bottleneck = np.array([FAULT_LABELS[i] for i in np.argmax(probabilities[:, faultIndexes], axis=1)])
    predicted = np.array([CLASS_ORDER[i] for i in np.argmax(probabilities, axis=1)])
    return risk, bottleneck, predicted


def gbmArgmax(models: list, features: np.ndarray) -> np.ndarray:
    classes = list(models[0].classes_)
    meanProba = np.mean([model.predict_proba(features) for model in models], axis=0)
    return np.array([classes[i] for i in np.argmax(meanProba, axis=1)])


def decideAll(engine: PolicyEngine, nodes: np.ndarray, bottleneck: np.ndarray, risk: np.ndarray,
              uncertainty: np.ndarray, horizonSeconds: float) -> np.ndarray:
    return np.array([
        engine.decide(node=str(node), nodeType="service", bottleneck=str(neck), meanRisk=float(p),
                      uncertainty=float(u), predictionLeadSeconds=horizonSeconds).action
        for node, neck, p, u in zip(nodes, bottleneck, risk, uncertainty)])


def classificationMetrics(labels: np.ndarray, predicted: np.ndarray, bottleneck: np.ndarray) -> dict:
    faultMask = np.isin(labels, FAULT_LABELS)
    return {
        "macroF1": float(f1_score(labels, predicted, labels=list(CLASS_ORDER), average="macro",
                                  zero_division=0)),
        "typeAccuracy": float((bottleneck[faultMask] == labels[faultMask]).mean()),
        "typeMacroF1": float(f1_score(labels[faultMask], bottleneck[faultMask], labels=list(FAULT_LABELS),
                                      average="macro", zero_division=0)),
    }


def actionOutcomes(actions: np.ndarray, labels: np.ndarray, faultFree: np.ndarray) -> dict:
    """원고 §5.4 와 같은 정의 — 장애 없는 구간의 정상 표본에 건 조치의 비율과 Dₐ·Rₐ 평균."""
    profiles = {p.name: p for p in defaultProfiles() if p.nodeType == "service"}
    unitCost = {name: profile.disruption * profile.reversibility for name, profile in profiles.items()}
    normal = labels == NORMAL_LABEL
    outcomes = {}
    for key, mask in (("faultFree", normal & faultFree), ("faultNormalLabel", normal & ~faultFree)):
        chosen = actions[mask]
        acted = chosen != HOLD
        cost = np.array([0.0 if action == HOLD else unitCost[action] for action in chosen])
        outcomes[key] = {"samples": int(mask.sum()), "rate": float(acted.mean()), "cost": float(cost.mean()),
                         "byAction": dict(collections.Counter(chosen[acted].tolist()))}
    for label in FAULT_LABELS:
        chosen = actions[labels == label]
        outcomes[label] = {"samples": len(chosen),
                           "rightAction": float((chosen == RIGHT_ACTION[label]).mean()),
                           "wrongAction": float((chosen == WRONG_ACTION[label]).mean()),
                           "hold": float((chosen == HOLD).mean())}
    return outcomes


def leafTable(tree: DecisionTreeClassifier, features: np.ndarray, labels: np.ndarray, names: list[str],
              engine: PolicyEngine, horizonSeconds: float) -> list[dict]:
    """잎마다 경로 조건·표본·위험도·ŝ·조치. 표본 수는 tree_.value 대신 직접 센다(버전마다 의미가 다르다)."""
    structure = tree.tree_
    conditions: dict[int, list[dict]] = {0: []}
    stack = [0]
    while stack:
        node = stack.pop()
        left, right = structure.children_left[node], structure.children_right[node]
        if left == right:      # 잎
            continue
        feature, threshold = names[structure.feature[node]], float(structure.threshold[node])
        conditions[left] = conditions[node] + [{"feature": feature, "op": "<=", "threshold": threshold}]
        conditions[right] = conditions[node] + [{"feature": feature, "op": ">", "threshold": threshold}]
        stack += [left, right]
    leafOf = tree.apply(features)
    rows = []
    for leaf in sorted(set(leafOf.tolist())):
        inLeaf = labels[leafOf == leaf]
        counts = collections.Counter(inLeaf.tolist())
        risk = 1.0 - counts[NORMAL_LABEL] / len(inLeaf)
        bottleneck = max(FAULT_LABELS, key=lambda name: counts[name])
        decision = engine.decide(node="leaf", nodeType="service", bottleneck=bottleneck, meanRisk=risk,
                                 uncertainty=0.0, predictionLeadSeconds=horizonSeconds)
        # 보류일 때 엔진은 θ 자리에 ∞ 를 둔다. 표에 필요한 것은 "어떤 조치가 몇에서 켜지는가" 이므로
        # ŝ 에서 유한한 θ 를 갖는 후보 중 가장 낮은 것을 따로 적는다.
        eligible = [c for c in decision.candidates
                    if c["excluded"] is None and np.isfinite(c["threshold"])]
        candidate = min(eligible, key=lambda c: c["threshold"]) if eligible else None
        rows.append({"leaf": leaf, "conditions": conditions[leaf], "samples": len(inLeaf),
                     "classCounts": {name: int(counts[name]) for name in CLASS_ORDER},
                     "risk": float(risk), "bottleneck": bottleneck, "action": decision.action,
                     "candidateAction": candidate["action"] if candidate else None,
                     "candidateThreshold": float(candidate["threshold"]) if candidate else None})
    return rows


def splitSummary(tree: DecisionTreeClassifier, names: list[str]) -> list[dict]:
    """내부 노드의 분할을 깊이 순으로 — 접기 간 안정성 비교용."""
    structure = tree.tree_
    summary, queue = [], [(0, 0)]
    while queue:
        node, depth = queue.pop(0)
        left, right = structure.children_left[node], structure.children_right[node]
        if left == right:
            continue
        summary.append({"depth": depth, "feature": names[structure.feature[node]],
                        "threshold": float(structure.threshold[node])})
        queue += [(left, depth + 1), (right, depth + 1)]
    return summary


def poolWaitSensitivity(tree: DecisionTreeClassifier, features: np.ndarray, labels: np.ndarray,
                        names: list[str], subsets: dict[str, np.ndarray]) -> dict | None:
    """트리가 풀 대기율로 유형을 가르는 노드에서 임계를 바꿔 본다(기술 통계 — 학습 없음).

    접기마다 이 임계가 크게 달랐다면 그 사이 구간에 표본이 적다는 뜻일 수 있다. 그러면 어느 값을
    써도 결과가 비슷하고, 아니면 임계가 결과를 좌우한다. 둘 중 무엇인지 여기서 가린다. 에피소드 종류
    (``subsets``)별로 따로 센다 — 조치가 신호를 지우면 강제 셀에서만 곡선이 무너진다.
    """
    structure = tree.tree_
    featureIndex = names.index("pathPoolWait")
    splitNodes = [node for node in range(structure.node_count)
                  if structure.children_left[node] != structure.children_right[node]
                  and structure.feature[node] == featureIndex]
    if not splitNodes:
        return None
    node = splitNodes[0]
    reached = tree.decision_path(features).toarray()[:, node].astype(bool)
    poolWait = features[:, featureIndex]
    thresholds = np.round(np.arange(0.05, 0.81, 0.05), 2)
    result = {"node": node, "fittedThreshold": float(structure.threshold[node]), "subsets": {}}
    for key, subset in subsets.items():
        inScope = reached & subset
        s1Wait = poolWait[inScope & (labels == "S1")]
        s3Wait = poolWait[inScope & (labels == "S3")]
        rows = []
        for threshold in thresholds:
            row = {"threshold": float(threshold),
                   "S1": float((s1Wait > threshold).mean()) if len(s1Wait) else float("nan"),
                   "S3": float((s3Wait <= threshold).mean()) if len(s3Wait) else float("nan")}
            row["balanced"] = (row["S1"] + row["S3"]) / 2
            rows.append(row)
        counts = collections.Counter(labels[inScope].tolist())
        result["subsets"][key] = {"reached": {name: int(counts[name]) for name in CLASS_ORDER}, "rows": rows}
    return result


def formatCondition(condition: dict) -> str:
    name = condition["feature"]
    if name in ("isChainEnd", "isEntry"):
        return f"{READABLE_FEATURES[name]} {'아님' if condition['op'] == '<=' else '예'}"
    return f"{READABLE_FEATURES[name]} {'≤' if condition['op'] == '<=' else '>'} {condition['threshold']:.2f}"


def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="결론 결정표 — GBM 판정을 얕은 트리로 환원")
    parser.add_argument("--chainDir", default=str(HERE.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=30.0,
                        help="Δ = L_pred. 원고 §5.4 와 폐루프 예측기가 30초다")
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--selfOnlyLabel", action="store_true",
                        help="라벨에서 상류 선행 열화 조건을 뺀다(compareTiers 와 같은 방식). 1단 분할이 라벨 "
                             "정의를 되찾은 것인지 가리는 대조")
    parser.add_argument("--trainOn", default="all", choices=["all", "natural"],
                        help="natural 이면 접기는 그대로 두고 학습에서만 벌크헤드·CB 강제 셀을 뺀다 — "
                             "J35 결정 3 의 판단 재료(평가 표본은 같다)")
    parser.add_argument("--ensembleSize", type=int, default=5)
    parser.add_argument("--calibration", default="sigmoid", choices=["none", "sigmoid", "isotonic"])
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--kappa", type=float, default=1.0, help="GBM 쪽 엔진의 κ (원고 A5)")
    parser.add_argument("--depths", default="1,2,3,4")
    parser.add_argument("--minLeaf", type=int, default=100, help="잎의 최소 표본 — 표 한 줄의 근거 크기")
    parser.add_argument("--tolerance", type=float, default=0.01,
                        help="최고 트리와 macro F1 차이가 이 안이면 더 얕은 트리를 고른다")
    parser.add_argument("--out", default=str(HERE / "decisionTable.json"))
    return parser


def main() -> int:
    arguments = buildParser().parse_args()
    if arguments.selfOnlyLabel:
        # 배수 0 이면 어떤 이웃이든 "열화" 로 판정되어 라벨이 "v 가 위반하는가" 단독이 된다(compareTiers).
        arguments.precursorMultiplier = 0.0
    chainDir = pathlib.Path(arguments.chainDir).resolve()
    episodes = compareTiers.loadAllEpisodes(chainDir, arguments)
    if not episodes:
        print("에피소드가 없다.")
        return 1

    selfFeatures, neighbourFeatures, labels, groups, nodes, timestamps = [], [], [], [], [], []
    for index, episode in enumerate(episodes):
        for sample in episode["samples"]:
            selfFeatures.append(sample["self"])
            neighbourFeatures.append(sample["neighbour"])
            labels.append(sample["label"])
            groups.append(index)
            nodes.append(sample["node"])
            timestamps.append(sample["timestamp"])
    combined = np.concatenate([np.stack(selfFeatures), np.stack(neighbourFeatures)], axis=1)
    labelArray, groupArray, nodeArray = np.array(labels), np.array(groups), np.array(nodes)
    readable, names = readableFeatures(combined)
    verifyLayout(readable, names, nodeArray)

    # 장애가 없는 구간 — endToEnd 와 같은 정의(대조군 에피소드이거나 주입 전)
    records = {record["tracePath"]: record for record in corpus.corpusRecords(chainDir)}
    episodeRecords = [records[episode["episodeKey"]] for episode in episodes]
    faultFree = np.array([episodeRecords[group].get("scenario") == "normal"
                          or timestamps[index] < episodeRecords[group]["injectedAt"]
                          for index, group in enumerate(groupArray)])
    # 벌크헤드·CB 를 장애와 함께 강제한 셀은 조치 뒤의 동역학이다. 무조치(NONE)·runChain 에피소드는 자연
    # 동역학이다. 두 집단에서 따로 재면 "조치가 진단 신호를 지우는가" 가 보인다(J35 결정 3).
    forced = np.array([episodeRecords[group].get("remediation") in FORCED_ACTIONS for group in groupArray])

    depths = [int(depth) for depth in arguments.depths.split(",") if depth.strip()]
    print(f"에피소드 {len(episodes)} · 표본 {len(labelArray)} · Δ = L_pred = {arguments.horizonSeconds:g}초 · "
          f"GroupKFold {arguments.folds}접기 out-of-fold (endToEnd 와 같은 접기)")
    print(f"라벨: {'v 위반 단독 (상류 조건 제거)' if arguments.selfOnlyLabel else '§4-2 전파성 (상류 조건 포함)'}")
    if arguments.trainOn == "natural":
        print(f"🔶 학습에서 강제 셀을 뺀다(--trainOn natural) — 강제 셀 {len(set(groupArray[forced].tolist()))}개 · "
              f"표본 {int(forced.sum())}. 평가 표본은 그대로다")

    # 접기 밖 예측을 표본 위치에 모은다 — 표본마다 정확히 한 번 평가된다.
    total = len(labelArray)
    gbmRisk, gbmUncertainty = np.zeros(total), np.zeros(total)
    gbmBottleneck, gbmPredicted = np.empty(total, dtype=object), np.empty(total, dtype=object)
    treeProba = {depth: np.zeros((total, len(CLASS_ORDER))) for depth in depths}
    foldSplits = {depth: [] for depth in depths}
    folds = list(GroupKFold(n_splits=arguments.folds).split(combined, labelArray, groupArray))
    for fold, (trainIndex, testIndex) in enumerate(folds):
        if arguments.trainOn == "natural":
            trainIndex = trainIndex[~forced[trainIndex]]
        models = endToEnd.trainEnsemble(combined, labelArray, groupArray, trainIndex, testIndex, arguments)
        risk, spread, neck = endToEnd.summarizeEnsemble(models, combined[testIndex])
        gbmRisk[testIndex], gbmUncertainty[testIndex], gbmBottleneck[testIndex] = risk, spread, neck
        gbmPredicted[testIndex] = gbmArgmax(models, combined[testIndex])
        for depth in depths:
            tree = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=arguments.minLeaf,
                                          random_state=compareTiers.RANDOM_SEED)
            tree.fit(readable[trainIndex], labelArray[trainIndex])
            treeProba[depth][testIndex] = probabilitiesInOrder(tree, readable[testIndex])
            foldSplits[depth].append(splitSummary(tree, names))
        print(f"  접기 {fold + 1}/{len(folds)} 완료 (평가 표본 {len(testIndex)})", flush=True)
    gbmBottleneck, gbmPredicted = gbmBottleneck.astype(str), gbmPredicted.astype(str)

    # ── GBM 경로 — 원고 §5.4 의 재현 확인 ─────────────────────────────
    gbmEngine = PolicyEngine(defaultProfiles(), availabilityLoss=1.0, kappa=arguments.kappa)
    gbmActions = decideAll(gbmEngine, nodeArray, gbmBottleneck, gbmRisk, gbmUncertainty,
                           arguments.horizonSeconds)
    gbmRow = {"metrics": classificationMetrics(labelArray, gbmPredicted, gbmBottleneck),
              "outcomes": actionOutcomes(gbmActions, labelArray, faultFree)}
    reference = None
    sweepPath = HERE / "deltaSweep.json"
    comparable = (not arguments.selfOnlyLabel and arguments.kappa == 1.0 and arguments.trainOn == "all"
                  and arguments.precursorMultiplier == 1.5 and sweepPath.exists())
    if comparable:
        for row in json.loads(sweepPath.read_text(encoding="utf-8"))["rows"]:
            if row["horizonSeconds"] == arguments.horizonSeconds:
                reference = row["falsePositiveByWindow"]["faultFree"]
    produced = gbmRow["outcomes"]["faultFree"]
    reproduced = (reference is not None and reference["samples"] == produced["samples"]
                  and reference["byAction"] == produced["byAction"])
    print("\n━━━ GBM 경로 재현 확인 (deltaSweep.json 의 같은 Δ 행) ━━━")
    print(f"  장애 없는 구간 정상 표본 {produced['samples']} · 개입 {produced['rate']:.1%} · "
          f"비용 {produced['cost']:.5f} · {produced['byAction']}")
    if not comparable:
        print("  — 라벨·κ 가 deltaSweep 과 달라 대조 대상이 아니다")
    elif reference is None:
        print("  ⚠️ deltaSweep.json 에 같은 Δ 행이 없어 대조하지 못했다")
    else:
        print(f"  기준 {reference['samples']} · 개입 {reference['rate']:.1%} · {reference['byAction']} → "
              + ("✅ 같다" if reproduced else "🔴 다르다 — 비교 기준이 원고의 수치가 아니다"))

    # ── 트리 경로 — 같은 엔진, u = 0 ────────────────────────────────
    treeEngine = PolicyEngine(defaultProfiles(), availabilityLoss=1.0, kappa=arguments.kappa)
    treeRows, treeOutputs = [], {}
    for depth in depths:
        risk, neck, predicted = summarizeProbabilities(treeProba[depth])
        actions = decideAll(treeEngine, nodeArray, neck, risk, np.zeros(total), arguments.horizonSeconds)
        treeOutputs[depth] = (predicted, neck, actions)
        faultMask = np.isin(labelArray, FAULT_LABELS)
        treeRows.append({
            "depth": depth,
            "metrics": classificationMetrics(labelArray, predicted, neck),
            "agreement": {"predicted": float((predicted == gbmPredicted).mean()),
                          "typeOnFaults": float((neck[faultMask] == gbmBottleneck[faultMask]).mean()),
                          "action": float((actions == gbmActions).mean()),
                          "actionOnFaults": float((actions[faultMask] == gbmActions[faultMask]).mean())},
            "outcomes": actionOutcomes(actions, labelArray, faultFree),
            "foldSplits": foldSplits[depth],
        })

    print(f"\n━━━ 정확도·충실도 (접기 밖, 표본 {total}) ━━━")
    print(f"{'예측기':<10}{'3클래스 F1':>11}{'ŝ 정확도':>10}{'판정 일치':>10}{'조치 일치':>10}"
          f"{'무장애 개입':>12}{'무장애 BH':>10}{'S1→BH':>8}{'S3→CB':>8}{'S1→CB':>8}")

    def printRow(name: str, row: dict, agreement: dict | None) -> None:
        metrics, outcomes = row["metrics"], row["outcomes"]
        free = outcomes["faultFree"]
        print(f"{name:<10}{metrics['macroF1']:>11.3f}{metrics['typeAccuracy']:>10.1%}"
              + (f"{agreement['predicted']:>10.1%}{agreement['action']:>10.1%}" if agreement else f"{'—':>10}{'—':>10}")
              + f"{free['rate']:>12.1%}{free['byAction'].get('BULKHEAD', 0):>10}"
              f"{outcomes['S1']['rightAction']:>8.1%}{outcomes['S3']['rightAction']:>8.1%}"
              f"{outcomes['S1']['wrongAction']:>8.1%}")

    printRow("GBM", gbmRow, None)
    for row in treeRows:
        printRow(f"트리 d{row['depth']}", row, row["agreement"])

    print("\n━━━ 장애 표본의 조치 (맞는 조치 / 틀린 조치 / 보류) ━━━")
    for name, row in [("GBM", gbmRow)] + [(f"트리 d{r['depth']}", r) for r in treeRows]:
        outcomes = row["outcomes"]
        extra = (f"   GBM 과 조치 일치 {row['agreement']['actionOnFaults']:.1%}"
                 if "agreement" in row else "")
        print(f"  {name:<8} S1 {outcomes['S1']['rightAction']:>6.1%} / {outcomes['S1']['wrongAction']:>6.1%} / "
              f"{outcomes['S1']['hold']:>6.1%}    S3 {outcomes['S3']['rightAction']:>6.1%} / "
              f"{outcomes['S3']['wrongAction']:>6.1%} / {outcomes['S3']['hold']:>6.1%}{extra}")

    bestF1 = max(row["metrics"]["macroF1"] for row in treeRows)
    chosen = min(row["depth"] for row in treeRows if row["metrics"]["macroF1"] >= bestF1 - arguments.tolerance)
    print(f"\n고른 깊이: {chosen} — 최고 트리 F1 {bestF1:.3f} 와 {arguments.tolerance} 이내인 가장 얕은 트리")

    # ── 에피소드 종류별 — 강제 조치 셀은 조치가 지표를 바꾼다(J35 결정 3) ──────────
    treePredicted, treeBottleneck, treeActions = treeOutputs[chosen]
    breakdown = {}
    print(f"\n━━━ 에피소드 종류별 (자연 = 조치를 강제하지 않음, 강제 = {'·'.join(FORCED_ACTIONS)} 강제 셀) ━━━")
    print(f"  {'':<6}{'표본':>6}  {'예측기':<8}{'3클래스 F1':>11}{'ŝ 정확도':>10}{'S1→BH':>8}{'S1→CB':>8}"
          f"{'S3→CB':>8}{'S3→BH':>8}")
    for key, name, mask in (("natural", "자연", ~forced), ("forced", "강제", forced)):
        breakdown[key] = {"samples": int(mask.sum()), "episodes": len(set(groupArray[mask].tolist()))}
        for model, predicted, neck, actions in (("gbm", gbmPredicted, gbmBottleneck, gbmActions),
                                                ("tree", treePredicted, treeBottleneck, treeActions)):
            metrics = classificationMetrics(labelArray[mask], predicted[mask], neck[mask])
            outcomes = actionOutcomes(actions[mask], labelArray[mask], faultFree[mask])
            breakdown[key][model] = {"metrics": metrics, "outcomes": outcomes}
            print(f"  {name if model == 'gbm' else '':<6}{mask.sum() if model == 'gbm' else '':>6}  "
                  f"{'GBM' if model == 'gbm' else f'트리 d{chosen}':<8}{metrics['macroF1']:>11.3f}"
                  f"{metrics['typeAccuracy']:>10.1%}{outcomes['S1']['rightAction']:>8.1%}"
                  f"{outcomes['S1']['wrongAction']:>8.1%}{outcomes['S3']['rightAction']:>8.1%}"
                  f"{outcomes['S3']['wrongAction']:>8.1%}")

    print(f"\n━━━ 접기별 분할 (깊이 {chosen}) — 같은 지표·비슷한 임계가 나와야 규칙으로 쓸 수 있다 ━━━")
    for fold, splits in enumerate(foldSplits[chosen]):
        print(f"  접기 {fold + 1}: " + " | ".join(
            f"d{split['depth']} {split['feature']} ≤ {split['threshold']:.3f}" for split in splits))

    # ── 최종 표 — 전체 코퍼스로 다시 학습 ──────────────────────────
    finalTree = DecisionTreeClassifier(max_depth=chosen, min_samples_leaf=arguments.minLeaf,
                                       random_state=compareTiers.RANDOM_SEED)
    fitMask = ~forced if arguments.trainOn == "natural" else np.ones(total, dtype=bool)
    finalTree.fit(readable[fitMask], labelArray[fitMask])
    table = leafTable(finalTree, readable[fitMask], labelArray[fitMask], names, treeEngine,
                      arguments.horizonSeconds)
    print(f"\n━━━ 결정표 (전체 {len(episodes)} 에피소드로 학습, 깊이 {chosen}) ━━━")
    for row in table:
        counts = row["classCounts"]
        candidate = (f"{row['candidateAction']} θ {row['candidateThreshold']:.5g}"
                     if row["candidateAction"] else "유한한 θ 없음")
        print(f"  [{row['samples']:>5}] " + " ∧ ".join(formatCondition(c) for c in row["conditions"]))
        print(f"          정상 {counts[NORMAL_LABEL]} · S1 {counts['S1']} · S3 {counts['S3']}  → 위험도 "
              f"{row['risk']:.2f}, ŝ {row['bottleneck']} ({candidate}) → **{row['action']}**")

    subsets = {"all": np.ones(total, dtype=bool), "natural": ~forced, "forced": forced}
    sensitivity = poolWaitSensitivity(finalTree, readable, labelArray, names, subsets)
    if sensitivity:
        print(f"\n━━━ 풀 대기율 임계 감도 — 트리가 유형을 가르는 노드, 학습 임계 "
              f"{sensitivity['fittedThreshold']:.3f} (맞힌 비율 S1 / S3) ━━━")
        parts = sensitivity["subsets"]
        titles = [f"{key} (S1 {part['reached']['S1']} · S3 {part['reached']['S3']})" for key, part in parts.items()]
        print(f"  {'임계':>5}" + "".join(f"{title:>26}" for title in titles))
        for index, row in enumerate(parts["all"]["rows"]):
            cells = "".join(f"{part['rows'][index]['S1']:>14.1%} / {part['rows'][index]['S3']:>7.1%}"
                            for part in parts.values())
            print(f"  {row['threshold']:>5.2f}{cells}")
    else:
        print("\n(최종 트리가 풀 대기율로 나누지 않는다 — 감도 생략)")

    payload = {
        "evaluation": f"GroupKFold {arguments.folds}접기 out-of-fold (endToEnd 와 같은 접기)",
        "label": "selfOnly" if arguments.selfOnlyLabel else "propagation", "trainOn": arguments.trainOn,
        "horizonSeconds": arguments.horizonSeconds, "kappa": arguments.kappa,
        "ensembleSize": arguments.ensembleSize, "calibration": arguments.calibration,
        "minLeaf": arguments.minLeaf, "episodes": len(episodes), "samples": total,
        "features": READABLE_FEATURES,
        "reproduction": {"reference": reference, "produced": produced, "matches": bool(reproduced)},
        "gbm": gbmRow, "trees": treeRows, "chosenDepth": chosen, "table": table,
        "byEpisodeKind": breakdown, "poolWaitSensitivity": sensitivity,
    }
    outputPath = pathlib.Path(arguments.out)
    outputPath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
