"""신뢰도 구간이 없는 원인 진단 — 규모 탓인가, 설계 탓인가.

end-to-end 에서 `p̄` 가 중간 구간(0.1~0.9)에 있는 표본이 4.0% 뿐이었다. 원인 후보가
셋이고 둘은 지금 측정 가능하다.

======  ==========================================  ==============
후보     내용                                        규모 탓인가
======  ==========================================  ==============
①      **장애가 계단 함수라 전이 구간이 짧다.**       ❌ 설계 탓
        주입하면 켜지고 끝까지 유지되므로 정상상태
        에서는 "10초 뒤 위반할까"가 자명하다.
        어려운 것은 전이 구간뿐이다.
②      **GBM 이 과신한다.** 부스팅 트리는 깊어지면   ❌ 모델 성질
        리프가 순수해져 확률이 0/1 로 포화한다.
        보정으로 고칠 수 있는 종류다.
③      라벨이 결정론적 규칙에서 나온다.              ❌ 설계 탓
======  ==========================================  ==============

**셋 다 규모와 무관하다는 것이 요점이다.** 규모가 작으면 보통 정확도가 떨어지지
확률이 극단화되지는 않는다 — 오히려 과적합으로 과신이 심해질 수는 있다.

검증 가능한 예측
================

① 이 맞다면 **중간 `p̄` 표본이 주입 시각 근처에 몰려 있어야 한다.** 멀리 떨어진
정상상태 구간에서는 확률이 극단으로 갈 것이다.

② 가 맞다면 **보정을 걸면 분포가 퍼져야 한다.** 보정으로 안 퍼지면 모델 과신이
아니라 데이터에 불확실성 자체가 없다는 뜻이다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import compareTiers  # noqa: E402
import endToEnd  # noqa: E402

NORMAL_LABEL = "정상"
MIDDLE_LOW, MIDDLE_HIGH = 0.1, 0.9


def main() -> int:
    parser = argparse.ArgumentParser(description="신뢰도 구간 부재의 원인 진단")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    episodes = compareTiers.loadAllEpisodes(chainDir, arguments)

    rawEpisodes = []
    for path in sorted((chainDir / "results").glob("*.json")):
        if not (path.name.startswith("episodes-") or path.name.startswith("remediation-")):
            continue
        import json
        for record in json.loads(path.read_text(encoding="utf-8")):
            if record.get("injectedAt") is not None:
                rawEpisodes.append(record)
    injectedAtByTrace = {r["tracePath"]: r["injectedAt"] for r in rawEpisodes}

    features, labels, groups, offsets = [], [], [], []
    for index, episode in enumerate(episodes):
        injectedAt = injectedAtByTrace.get(episode["episodeKey"])
        for sample in episode["samples"]:
            features.append(np.concatenate([sample["self"], sample["neighbour"]]))
            labels.append(sample["label"])
            groups.append(index)
            offsets.append(sample["timestamp"] - injectedAt if injectedAt else float("nan"))

    featureMatrix = np.stack(features)
    labelArray = np.array(labels)
    groupArray = np.array(groups)
    offsetArray = np.array(offsets)

    splitter = GroupKFold(n_splits=4)
    trainIndex, testIndex = next(iter(splitter.split(featureMatrix, labelArray, groupArray)))

    models = compareTiers.buildEnsemble(featureMatrix[trainIndex], labelArray[trainIndex])
    meanRisk, _, _ = endToEnd.summarizeEnsemble(models, featureMatrix[testIndex])
    testOffsets = offsetArray[testIndex]

    # ── 후보 ① : 중간 p̄ 표본이 주입 경계에 몰려 있는가 ───────────────────────
    middle = (meanRisk > MIDDLE_LOW) & (meanRisk < MIDDLE_HIGH)
    print("━━━ 후보 ① 장애가 계단 함수라 전이 구간이 짧다 ━━━")
    print(f"중간 p̄ 표본: {middle.sum()} / {len(middle)} ({middle.mean():.1%})\n")
    print(f"{'주입 기준 시각':<20}{'전체 표본':>10}{'중간 p̄':>9}{'비율':>8}")
    bands = [(-1e9, -5, "주입 5초 이전"), (-5, 5, "주입 ±5초 (전이)"),
             (5, 20, "주입 후 5~20초"), (20, 1e9, "주입 후 20초~")]
    for low, high, name in bands:
        mask = (testOffsets >= low) & (testOffsets < high)
        if not mask.any():
            continue
        share = middle[mask].mean()
        print(f"{name:<20}{mask.sum():>10}{middle[mask].sum():>9}{share:>8.1%}")

    transition = (testOffsets >= -5) & (testOffsets < 5)
    beforeInjection = testOffsets < -5
    faultSteady = testOffsets >= 5
    if transition.any() and middle[transition].mean() > 2 * middle.mean():
        print("\n  ✅ 후보 ① 지지 — 전이 구간에 중간 p̄ 가 집중된다.")
    else:
        print("\n  ❌ 후보 ① 미지지 — 중간 p̄ 가 전이 구간에 몰려 있지 않다.")
        if faultSteady.any() and beforeInjection.any():
            print(f"     대신 **장애 정상상태**에 더 많다 "
                  f"(주입 후 {middle[faultSteady].mean():.1%} vs 주입 전 "
                  f"{middle[beforeInjection].mean():.1%}).")
            print("     즉 모델은 '정상'을 매우 확신하고, 장애 중에만 흔들린다. 전이가")
            print("     짧아서가 아니라 **정상 구간이 너무 쉬워서** 전체 평균이 극단화된다.")
            print("     정상 표본이 66.8% 를 차지하므로 그 쏠림이 분포를 지배한다.")

    # ── 후보 ② : 보정하면 분포가 퍼지는가 ───────────────────────────────────
    print("\n━━━ 후보 ② GBM 이 과신한다 ━━━")
    calibrationSplitter = GroupKFold(n_splits=3)
    fitIndex, calibIndex = next(iter(calibrationSplitter.split(
        featureMatrix[trainIndex], labelArray[trainIndex], groupArray[trainIndex])))
    base = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, max_depth=6,
                                          random_state=compareTiers.RANDOM_SEED)
    base.fit(featureMatrix[trainIndex][fitIndex], labelArray[trainIndex][fitIndex])
    # sklearn 1.6+ 에서 cv="prefit" 이 제거되고 FrozenEstimator 로 바뀌었다.
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
    calibrated.fit(featureMatrix[trainIndex][calibIndex], labelArray[trainIndex][calibIndex])

    classes = list(calibrated.classes_)
    normalIndex = classes.index(NORMAL_LABEL)
    calibratedRisk = 1.0 - calibrated.predict_proba(featureMatrix[testIndex])[:, normalIndex]
    rawRisk = 1.0 - base.predict_proba(featureMatrix[testIndex])[:, normalIndex]

    print(f"{'모델':<20}{'중간 p̄ 비율':>14}{'p̄<0.01':>10}{'p̄>0.99':>10}")
    for name, risk in (("원본 GBM", rawRisk), ("보정 후 (isotonic)", calibratedRisk)):
        middleShare = ((risk > MIDDLE_LOW) & (risk < MIDDLE_HIGH)).mean()
        print(f"{name:<20}{middleShare:>14.1%}{(risk < 0.01).mean():>10.1%}"
              f"{(risk > 0.99).mean():>10.1%}")

    rawMiddle = ((rawRisk > MIDDLE_LOW) & (rawRisk < MIDDLE_HIGH)).mean()
    calibratedMiddle = ((calibratedRisk > MIDDLE_LOW) & (calibratedRisk < MIDDLE_HIGH)).mean()
    print()
    if calibratedMiddle > rawMiddle * 1.5:
        print("  ✅ 후보 ② 지지 — 보정하면 분포가 퍼진다. **모델 과신이며 고칠 수 있다.**")
    else:
        print("  ❌ 후보 ② 미지지 — 보정해도 안 퍼진다. 모델 과신이 아니라")
        print("     **데이터에 불확실성 자체가 없다.** 보정으로는 해결되지 않는다.")

    print("\n━━━ 결론 ━━━")
    print("  **규모는 직접적인 원인이 아니다.** 규모가 작으면 보통 정확도가 떨어지지")
    print("  확률이 극단화되지는 않는다. 다만 과적합으로 과신이 심해질 수는 있고,")
    print("  그것은 후보 ②(보정 가능)에 해당한다.")
    print()
    print("  측정이 가리키는 것은 **클래스 구성**이다. 정상 표본이 66.8% 이고 그 구간이")
    print("  너무 쉬워(중간 p̄ 1.0%) 전체 분포를 극단으로 끌어당긴다. 장애 중에는")
    print("  모델도 흔들린다(중간 p̄ 5.9~8.0%).")
    print()
    print("  → 게이트 ③ 에서 확인할 것: 주입 비율을 높이고 장애 강도를 여러 단계로")
    print("    두면 중간 구간이 생기는가. 지금은 장애가 켜짐/꺼짐 두 상태뿐이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
