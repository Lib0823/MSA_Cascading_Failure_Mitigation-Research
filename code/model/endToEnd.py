"""예측기 → Policy Engine 연결 — 조각은 됐지만 연결은 한 번도 안 돌았다.

각 층은 따로 검증됐다. ②는 실데이터에서 macro F1 0.882 를 냈고(compareTiers),
Policy Engine 은 유도 8종을 통과했다(policySmokeTest). 그런데 **둘을 이어 본 적이
없다.** 이 모듈이 그 연결이다.

연결하면서 드러난 구멍
=======================

``compareTiers.predictEnsemble`` 은 **argmax 클래스만 돌려주고 `p̄`·`u` 를 버린다.**
Policy Engine 은 둘 다 요구한다(`p_eff = max(0, p̄ − κ√u)`). 게이트 ① 의
``summarizeEnsemble`` 에는 있던 경로가 실데이터 파이프라인에서 빠져 있었다.

게이트 ① 이 남긴 숙제
======================

게이트 ① C2 는 앙상블 폭 `√u ≈ 0.029` 를 쟀지만 *"랜덤 초기화 산물이라 학습 후
폭을 보증하지 않는다"* 는 단서를 달았다. 여기서는 **실제 학습된 앙상블**의 폭을
재므로 그 숙제에 부분적으로 답한다.

폭이 0 에 가까우면 Safety Guard 가 작동하지 않는다 — `κ` 를 아무리 올려도
`p_eff` 가 줄지 않아 *"불확실하면 보류"* 가 유도되지 않는다. 그 경우 N=3 앙상블의
존재 이유가 사라지므로 **설계 재검토 대상**이다.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import compareTiers  # noqa: E402
import corpus  # noqa: E402
from policyEngine import HOLD, PolicyEngine, defaultProfiles  # noqa: E402

NORMAL_LABEL = "정상"


def summarizeEnsemble(models: list, features: np.ndarray) -> tuple:
    """멤버 확률에서 `(p̄, u, ŝ)` 를 뽑는다.

    - `p̄ = mean(1 − P(정상))` — 위험 확률. 멤버 평균이다.
    - `u  = var(1 − P(정상))` — 불확실성. **멤버 간 불일치**이며 분산이다.
    - `ŝ` — 병목 유형. 정상을 제외한 클래스 중 평균 확률이 가장 큰 것.

    ŝ 를 argmax 전체가 아니라 **정상을 뺀 argmax** 로 잡는 이유: Policy Engine 은
    *"위험한가"* 와 *"어떤 병목인가"* 를 분리해 받는다. 위험도가 낮으면 어차피
    보류되므로, 유형은 "만약 위험하다면 어느 쪽인가"를 답하면 된다.
    """
    classes = list(models[0].classes_)
    normalIndex = classes.index(NORMAL_LABEL) if NORMAL_LABEL in classes else None
    memberProba = np.stack([model.predict_proba(features) for model in models])

    if normalIndex is None:
        memberRisk = memberProba.sum(axis=2)
    else:
        memberRisk = 1.0 - memberProba[:, :, normalIndex]
    meanRisk = memberRisk.mean(axis=0)
    uncertainty = memberRisk.var(axis=0)

    meanProba = memberProba.mean(axis=0)
    bottleneckIndexes = [i for i, name in enumerate(classes) if name != NORMAL_LABEL]
    if bottleneckIndexes:
        picked = np.array(bottleneckIndexes)[np.argmax(meanProba[:, bottleneckIndexes], axis=1)]
        bottleneck = np.array([classes[i] for i in picked])
    else:
        bottleneck = np.array([NORMAL_LABEL] * len(features))
    return meanRisk, uncertainty, bottleneck


def trainEnsemble(combined: np.ndarray, labelArray: np.ndarray, groupArray: np.ndarray,
                  trainIndex: np.ndarray, testIndex: np.ndarray, arguments) -> list:
    """한 접기의 앙상블. 보정기는 base 가 보지 않은 표본으로 학습해야 하므로 train 을 다시 쪼갠다."""
    if arguments.calibration == "none":
        return compareTiers.buildEnsemble(combined[trainIndex], labelArray[trainIndex],
                                          size=arguments.ensembleSize)
    inner = GroupKFold(n_splits=3)
    fitLocal, calibLocal = next(iter(inner.split(
        combined[trainIndex], labelArray[trainIndex], groupArray[trainIndex])))
    fitIndex, calibIndex = trainIndex[fitLocal], trainIndex[calibLocal]
    assert not (set(groupArray[fitIndex]) & set(groupArray[calibIndex])), "fit∩calib 누수"
    assert not (set(groupArray[fitIndex]) & set(groupArray[testIndex])), "fit∩test 누수"
    return compareTiers.buildCalibratedEnsemble(
        combined[fitIndex], labelArray[fitIndex],
        combined[calibIndex], labelArray[calibIndex],
        size=arguments.ensembleSize, method=arguments.calibration)


def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="예측기 → Policy Engine end-to-end")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0,
                        help="Δ. 예측 지평이자 Policy Engine 의 L_pred 다")
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--kappas", default="0,0.5,1,2,4")
    parser.add_argument("--ensembleSize", type=int, default=5,
                        help="N=3 은 불일치가 3/3 아니면 2/3 두 값뿐이라 해상도가 낮다")
    parser.add_argument("--calibration", default="sigmoid",
                        choices=["none", "sigmoid", "isotonic"],
                        help="멤버별 보정. §2-A 가 설계에 넣어 둔 항목이다")
    parser.add_argument("--folds", type=int, default=4,
                        help="GroupKFold 접기 수. 모든 접기의 평가 예측을 모아 지표를 낸다(J36)")
    return parser


def run(arguments, write: bool = True) -> dict:
    """end-to-end 한 번. `write=False` 면 endToEnd.json 을 쓰지 않는다(Δ 표용 — deltaSweep.py)."""
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
    labelArray = np.array(labels)
    groupArray = np.array(groups)
    nodeArray = np.array(nodes)

    # 🔴 J36 — 접기 **하나**로 평가하면 에피소드가 늘 때 GroupKFold 가 접기를 다시 배정해 결과가
    #    흔들린다(J35: 학습 데이터 효과 없이 0.011 → 0.423). 접기마다 학습하고 **모든 평가 예측을
    #    모아**(out-of-fold) 지표를 낸다 — 표본마다 정확히 한 번 평가된다.
    folds = list(GroupKFold(n_splits=arguments.folds).split(combined, labelArray, groupArray))
    risks, uncertainties, bottlenecks, indexes, foldIds = [], [], [], [], []
    for fold, (trainIndex, testIndex) in enumerate(folds):
        models = trainEnsemble(combined, labelArray, groupArray, trainIndex, testIndex, arguments)
        assert len(models) == arguments.ensembleSize
        risk, spreadPart, neck = summarizeEnsemble(models, combined[testIndex])
        risks.append(risk)
        uncertainties.append(spreadPart)
        bottlenecks.append(neck)
        indexes.append(testIndex)
        foldIds.append(np.full(len(testIndex), fold))
    testIndex = np.concatenate(indexes)
    meanRisk = np.concatenate(risks)
    uncertainty = np.concatenate(uncertainties)
    bottleneck = np.concatenate(bottlenecks)
    foldOf = np.concatenate(foldIds)
    trueLabels = labelArray[testIndex]
    testNodes = nodeArray[testIndex]
    # 표본이 **장애가 없는 구간**인가 — 대조군 에피소드이거나 주입 전. H5a(장애 미발생 시 개입 비용)는
    # 이 구간에서 재야 한다. `정상` 라벨에는 장애 에피소드의 비전파 노드도 들어가기 때문이다(J36).
    records = {record["tracePath"]: record for record in corpus.corpusRecords(chainDir)}
    episodeRecords = [records[episode["episodeKey"]] for episode in episodes]
    faultFree = np.array([
        episodeRecords[group].get("scenario") == "normal"
        or timestamps[sample] < episodeRecords[group]["injectedAt"]
        for group, sample in zip(groupArray[testIndex], testIndex)])

    print(f"{arguments.folds}접기 교차 검증(out-of-fold) — 에피소드 {len(episodes)} · 표본 "
          f"{len(testIndex)}, 앙상블 N={arguments.ensembleSize}, 보정 {arguments.calibration}")
    print(f"Δ = L_pred = {arguments.horizonSeconds}s\n")

    spread = np.sqrt(uncertainty)
    print("━━━ 학습된 앙상블의 불확실성 — 게이트 ① C2 의 숙제 ━━━")
    print(f"  √u  중앙값 {np.median(spread):.4f}  평균 {spread.mean():.4f}"
          f"  최대 {spread.max():.4f}")
    print(f"  p̄   중앙값 {np.median(meanRisk):.3f}  (정상 라벨 표본만: "
          f"{np.median(meanRisk[trueLabels == NORMAL_LABEL]):.3f})")
    zeroShare = float((spread < 1e-6).mean())
    print(f"  멤버가 완전히 일치하는 표본 비율: {zeroShare:.1%}")
    # ⚠️ 최대값이 크다는 것만으로 "폭이 있다"고 하면 안 된다. 소수의 경계 표본에서만
    #    갈리고 대부분이 0 이면 Safety Guard 는 그 소수에만 작동한다.
    if np.median(spread) < 1e-3:
        print("  ❌ **중앙값이 0 이다.** 소수 표본에서만 멤버가 갈리므로 Safety Guard 는")
        print("     그 소수에만 작동한다. N=3 부트스트랩 앙상블이 다양성을 거의 못 만든다 —")
        print("     데이터가 충분하고 신호가 강하면 세 모델이 같은 답을 낸다.")
        print("     §2-A 의 대안(MC dropout)이나 feature 서브샘플링을 검토해야 한다.")
    else:
        print(f"  ✅ 폭이 존재한다. κ=1 이면 p_eff 가 중앙값 기준 {np.median(spread):.3f} 만큼 깎인다.")
    print()

    print("━━━ κ 에 따른 조치 분포 — Safety Guard 가 실데이터에서 작동하는가 ━━━")
    kappas = [float(k) for k in arguments.kappas.split(",") if k.strip()]
    rows = []
    for kappa in kappas:
        engine = PolicyEngine(defaultProfiles(), availabilityLoss=1.0, kappa=kappa)
        counts = collections.Counter()
        wrongScaleOut = 0
        for index in range(len(testIndex)):
            decision = engine.decide(
                node=str(testNodes[index]), nodeType="service",
                bottleneck=str(bottleneck[index]),
                meanRisk=float(meanRisk[index]), uncertainty=float(uncertainty[index]),
                predictionLeadSeconds=arguments.horizonSeconds)
            counts[decision.action] += 1
            # H4 — S1 에서 Scale-out 이 한 번이라도 나오면 안 된다.
            if decision.action == "SCALE_OUT" and bottleneck[index] == "S1":
                wrongScaleOut += 1
        total = sum(counts.values())
        rows.append({"kappa": kappa, "counts": dict(counts),
                     "holdShare": counts[HOLD] / total, "wrongScaleOut": wrongScaleOut})
        summary = "  ".join(f"{name} {count / total:.1%}" for name, count in counts.most_common())
        print(f"  κ={kappa:<5} 보류 {counts[HOLD] / total:>6.1%}   {summary}")

    holdShares = [r["holdShare"] for r in rows]
    monotone = all(a <= b + 1e-9 for a, b in zip(holdShares, holdShares[1:]))
    print()
    holdSwing = holdShares[-1] - holdShares[0]
    print(f"  κ {kappas[0]} → {kappas[-1]} 에서 보류 변화: {holdSwing:+.1%}p")
    if not monotone:
        print("  ⚠️ 단조가 아니다. 유형 추정이 κ 와 함께 흔들린 결과일 수 있다.")
    if holdSwing <= 0:
        print("  ❌ κ 를 올려도 보류가 늘지 않는다. Safety Guard 가 작동하지 않는다.")
    elif holdSwing < 0.10:
        print("  ⚠️ **단조 증가하나 폭이 작다.** 기술적으로는 유도되지만 실질적 효과가")
        print("     미미하다 — 불확실성 자체가 거의 0 이기 때문이다. 'Safety Guard 가")
        print("     작동한다'고 주장하려면 이 폭이 의미 있는 크기여야 한다.")
    else:
        print("  ✅ κ 가 커질수록 보류가 늘고 폭도 의미 있다 — Safety Guard 가 실데이터에서 작동한다.")

    totalWrong = sum(r["wrongScaleOut"] for r in rows)
    print(f"\n  S1 에서 Scale-out 이 선택된 횟수: {totalWrong}")
    if totalWrong == 0:
        print("  ✅ H4 전반부가 전 구간에서 지켜진다 — 비용함수가 외부 규칙 없이 배제한다.")
    else:
        print("  ❌ H4 위반. θ=∞ 가 작동하지 않는다.")

    print("\n━━━ 보정(calibration) — §4-4 가 요구하는 ECE ━━━")
    # p̄ 가 "위반 확률"이라면 p̄≈0.7 인 표본의 실제 위반 비율도 0.7 이어야 한다.
    # 비용함수가 p 를 확률로 쓰므로(E[비용|보류] = p·L) 보정이 깨지면 임계값 유도가
    # 통째로 흔들린다 — 성능 지표가 아니라 **전제**다.
    isPositive = (trueLabels != NORMAL_LABEL).astype(float)
    binEdges = np.linspace(0.0, 1.0, 11)
    binIndex = np.clip(np.digitize(meanRisk, binEdges) - 1, 0, len(binEdges) - 2)
    expectedError, reliability = 0.0, []
    for b in range(len(binEdges) - 1):
        mask = binIndex == b
        if not mask.any():
            continue
        confidence = float(meanRisk[mask].mean())
        accuracy = float(isPositive[mask].mean())
        weight = float(mask.mean())
        expectedError += weight * abs(confidence - accuracy)
        reliability.append({"bin": f"{binEdges[b]:.1f}~{binEdges[b+1]:.1f}",
                            "count": int(mask.sum()), "predicted": confidence,
                            "observed": accuracy})
    print(f"{'구간':<12}{'표본':>7}{'예측 p̄':>10}{'실제 비율':>11}{'차이':>9}")
    for row in reliability:
        print(f"{row['bin']:<12}{row['count']:>7}{row['predicted']:>10.3f}"
              f"{row['observed']:>11.3f}{row['observed'] - row['predicted']:>+9.3f}")
    print(f"\n  ECE = {expectedError:.4f}")
    if expectedError < 0.05:
        print("  ✅ 보정이 양호하다. p̄ 를 확률로 쓰는 비용함수의 전제가 성립한다.")
    else:
        print("  ⚠️ 보정 오차가 크다. p̄ 가 확률이 아니면 θₐ 유도가 흔들린다 —")
        print("     비용함수가 E[비용|보류] = p·L 로 p 를 확률로 쓰기 때문이다.")
    print("  ⚠️ 구간별 표본이 얇으면 ECE 가 낙관적으로 나온다(§4-4 클래스 불균형 주).")

    # ECE 가 낮아도 확률이 양 극단에 몰려 있으면 "신뢰도 구간"이 존재하지 않는다.
    # 연구의 기여가 "신뢰도에 따라 조치를 차등화한다"(H2)이므로 이것은 성능이 아니라
    # **기여의 성립 조건**이다.
    middleShare = float(((meanRisk > 0.1) & (meanRisk < 0.9)).mean())
    print(f"\n  p̄ 가 중간 구간(0.1~0.9)에 있는 표본: {middleShare:.1%}")
    if middleShare < 0.10:
        print("  ❌ **확률이 사실상 0/1 이다. 신뢰도 구간이 존재하지 않는다.**")
        print("     H2 는 '신뢰도에 따라 조치를 차등화한다'인데 차등화할 구간이 없으면")
        print("     Policy Engine 이 이진 규칙으로 환원된다. θₐ 가 0 에 가까운 것과")
        print("     겹치면 'p̄>0 이면 무조건 조치'가 되어 비용함수가 하는 일이 사라진다.")
        print("     원인 후보: 라벨이 결정론적 규칙(§4-2)에서 나오고 feature 가 그 규칙의")
        print("     입력을 포함해 모델이 규칙을 거의 완벽히 학습한다. 실계에서는 노이즈가")
        print("     더 많을 것이므로 **게이트 ③ 에서 재확인이 필요하다.**")
    elif middleShare < 0.25:
        print("  ⚠️ 중간 구간 표본이 얇다. 신뢰도 차등화의 여지가 제한적이다.")
    else:
        print("  ✅ 중간 구간이 충분하다. 신뢰도 차등화가 의미를 갖는다.")

    print("\n━━━ 실제 라벨별 조치 (κ=1) ━━━")
    engine = PolicyEngine(defaultProfiles(), availabilityLoss=1.0, kappa=1.0)
    byLabel: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    actions = []
    for index in range(len(testIndex)):
        decision = engine.decide(
            node=str(testNodes[index]), nodeType="service", bottleneck=str(bottleneck[index]),
            meanRisk=float(meanRisk[index]), uncertainty=float(uncertainty[index]),
            predictionLeadSeconds=arguments.horizonSeconds)
        byLabel[str(trueLabels[index])][decision.action] += 1
        actions.append(decision.action)
    actions = np.array(actions)
    print(f"{'실제 라벨':<10}{'표본':>7}  조치 분포")
    for label in sorted(byLabel):
        counts = byLabel[label]
        total = sum(counts.values())
        print(f"{label:<10}{total:>7}  "
              + "  ".join(f"{n} {c / total:.0%}" for n, c in counts.most_common(3)))

    # 정상 라벨에 조치가 나간 비율 = H5a(장애 미발생 시 개입 비용)의 직접 재료다.
    normalCounts = byLabel.get(NORMAL_LABEL, collections.Counter())
    normalTotal = sum(normalCounts.values())
    falsePositiveRate = (1.0 - normalCounts[HOLD] / normalTotal) if normalTotal else float("nan")
    print(f"\n  정상 라벨에 조치가 나간 비율(오탐): {falsePositiveRate:.1%}"
          f"  — H5a 의 직접 재료다")
    print("  ⚠️ 캘리브레이션은 공짜가 아니다. 확률을 퍼뜨리면 정상 표본 일부가 θ 를 넘어")
    print("     오탐이 는다. θ 가 0 에 가까운 현 가정에서 특히 그렇다 —")
    print("     **캘리브레이션과 θ 보정(Dₐ·Rₐ 실측)은 함께 풀어야 하는 짝이다.**")

    # 🔴 오탐을 **비용으로도** 본다(J26·J36). 장애가 없는 표본에 조치 a 를 걸면 비용함수의
    #    (1−p)·Dₐ·Rₐ 항이 p=0 에서 실현된다. CB 는 Dₐ·Rₐ 가 0.0002 라 건수로만 보면 과장된다.
    profiles = {p.name: p for p in defaultProfiles() if p.nodeType == "service"}
    normalMask = trueLabels == NORMAL_LABEL
    normalActions = actions[normalMask]
    unitCost = {name: profile.disruption * profile.reversibility for name, profile in profiles.items()}
    normalCosts = np.array([0.0 if action == HOLD else unitCost[action] for action in normalActions])
    falsePositiveCost = float(normalCosts.mean()) if normalMask.any() else float("nan")
    costByAction = {action: float(normalCosts[normalActions == action].sum() / normalMask.sum())
                    for action in sorted(set(normalActions)) if action != HOLD}
    rateByFold = [float((actions[normalMask & (foldOf == fold)] != HOLD).mean())
                  for fold in range(arguments.folds) if (normalMask & (foldOf == fold)).any()]
    print(f"  정상 표본당 오탐 비용(Dₐ·Rₐ 평균): {falsePositiveCost:.4f}"
          + (" — " + ", ".join(f"{a} {c:.4f}" for a, c in costByAction.items()) if costByAction else "")
          + f"  (1건: " + ", ".join(f"{a} {c:.4f}" for a, c in sorted(unitCost.items())) + ")")
    print("  접기별 오탐률: " + ", ".join(f"{r:.1%}" for r in rateByFold))

    def falsePositiveIn(window: np.ndarray) -> dict | None:
        subset = normalMask & window
        if not subset.any():
            return None
        chosen = actions[subset]
        acted = chosen != HOLD
        cost = np.array([0.0 if action == HOLD else unitCost[action] for action in chosen])
        return {"samples": int(subset.sum()), "rate": float(acted.mean()), "cost": float(cost.mean()),
                "byAction": dict(collections.Counter(chosen[acted]))}

    # 🔴 `정상` 라벨 = 장애 없는 구간 + **장애 구간의 비전파 노드**. 후자에 CB 를 거는 것은 장애가
    #    없는데 개입한 것이 아니다 — 사슬 어딘가가 실제로 장애 중이다. H5a 는 전자로 잰다(J36).
    falsePositiveByWindow = {"faultFree": falsePositiveIn(faultFree),
                             "faultNormalLabel": falsePositiveIn(~faultFree)}
    for key, name in (("faultFree", "장애 없는 구간"), ("faultNormalLabel", "장애 구간의 정상 라벨")):
        row = falsePositiveByWindow[key]
        if row:
            print(f"  {name:<14} 표본 {row['samples']:>6}  오탐률 {row['rate']:>6.1%}  비용 {row['cost']:.4f}  "
                  + ", ".join(f"{a} {n}" for a, n in sorted(row["byAction"].items())))

    payload = {"evaluation": f"GroupKFold {arguments.folds}접기 out-of-fold",
               "horizonSeconds": arguments.horizonSeconds,
               "episodes": len(episodes), "samples": int(len(testIndex)),
               "falsePositiveRate": float(falsePositiveRate),
               "falsePositiveRateByFold": rateByFold,
               "falsePositiveCost": falsePositiveCost,
               "falsePositiveCostByAction": costByAction,
               "falsePositiveByWindow": falsePositiveByWindow,
               "expectedCalibrationError": float(expectedError), "reliability": reliability,
               "zeroSpreadShare": zeroShare, "holdSwing": float(holdSwing),
               "middleConfidenceShare": middleShare,
               "spreadMedian": float(np.median(spread)), "spreadMax": float(spread.max()),
               "kappaRows": rows, "wrongScaleOut": int(totalWrong),
               "byLabel": {k: dict(v) for k, v in byLabel.items()}}
    if write:
        outputPath = pathlib.Path(__file__).resolve().parent / "endToEnd.json"
        outputPath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기록: {outputPath}")
    return payload


def main() -> int:
    arguments = buildParser().parse_args()
    return 0 if run(arguments) else 1


if __name__ == "__main__":
    raise SystemExit(main())
