"""캘리브레이션이 `p̄` 분포를 넓히면서 `u` 를 보존하는가.

§2-A 가 *"소형 앙상블 + 캘리브레이션"* 을 설계에 넣어 뒀는데 구현에 빠져 있었다.
넣기 전에 **부작용이 없는지** 확인한다.

왜 멤버별로 보정해야 하는가
============================

`p_eff = max(0, p̄ − κ√u)` 는 `p̄` 와 `√u` 가 **같은 공간**에 있어야 성립한다.
앙상블 평균만 보정하면 `p̄` 는 보정 공간, `u` 는 원시 공간이 되어 뺄셈이 의미를
잃는다. 따라서 **멤버별로 보정한 뒤 평균·분산을 다시 낸다.**

그런데 바로 그것이 위험한 쪽이다 — isotonic 은 계단 함수라 **서로 다른 원시값을
같은 보정값으로 뭉갤 수 있다.** 그러면 멤버 불일치가 사라져 `u → 0` 이 되고,
Safety Guard 가 죽는다. 그래서 다음 넷을 **동시에** 본다.

1. `p̄` 중간 구간 비율 — **늘어야** 한다 (보정의 목적)
2. `√u > 0.05` 비율 — **줄면 안 된다** (Safety Guard 의 재료)
3. ECE — **줄어야** 한다 (보정의 정의)
4. macro F1 — **떨어지면 안 된다** (예측 성능)

하나라도 반대로 가면 채택하지 않는다. isotonic 과 sigmoid 를 함께 재는 이유도
isotonic 이 보정 표본이 적을 때 과적합해 계단이 거칠어지기 때문이다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import compareTiers  # noqa: E402

NORMAL_LABEL = "정상"
MIDDLE_LOW, MIDDLE_HIGH = 0.1, 0.9
SPREAD_FLOOR = 0.05


def riskFromProba(proba: np.ndarray, classes: list) -> np.ndarray:
    return 1.0 - proba[:, :, classes.index(NORMAL_LABEL)]


def summarize(memberProba: np.ndarray, classes: list, trueLabels: np.ndarray) -> dict:
    risk = riskFromProba(memberProba, classes)
    meanRisk = risk.mean(axis=0)
    spread = np.sqrt(risk.var(axis=0))
    meanProba = memberProba.mean(axis=0)
    predicted = np.array(classes)[np.argmax(meanProba, axis=1)]

    isPositive = (trueLabels != NORMAL_LABEL).astype(float)
    edges = np.linspace(0.0, 1.0, 11)
    binIndex = np.clip(np.digitize(meanRisk, edges) - 1, 0, len(edges) - 2)
    expectedError = 0.0
    for b in range(len(edges) - 1):
        mask = binIndex == b
        if mask.any():
            expectedError += mask.mean() * abs(meanRisk[mask].mean() - isPositive[mask].mean())

    return {
        "middleShare": float(((meanRisk > MIDDLE_LOW) & (meanRisk < MIDDLE_HIGH)).mean()),
        "spreadAboveFloor": float((spread > SPREAD_FLOOR).mean()),
        "spreadMedian": float(np.median(spread)),
        "zeroSpreadShare": float((spread < 1e-6).mean()),
        "ece": float(expectedError),
        "macroF1": float(f1_score(trueLabels, predicted, average="macro", zero_division=0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="캘리브레이션 부작용 검증")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--ensembleSize", type=int, default=5)
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    episodes = compareTiers.loadAllEpisodes(chainDir, arguments)

    features, labels, groups = [], [], []
    for index, episode in enumerate(episodes):
        for sample in episode["samples"]:
            features.append(np.concatenate([sample["self"], sample["neighbour"]]))
            labels.append(sample["label"])
            groups.append(index)
    featureMatrix = np.stack(features)
    labelArray = np.array(labels)
    groupArray = np.array(groups)

    # ── 분할: test 를 먼저 떼고, 남은 것을 fit / calib 으로 다시 나눈다 ──────
    outer = GroupKFold(n_splits=4)
    trainIndex, testIndex = next(iter(outer.split(featureMatrix, labelArray, groupArray)))
    inner = GroupKFold(n_splits=3)
    fitLocal, calibLocal = next(iter(inner.split(
        featureMatrix[trainIndex], labelArray[trainIndex], groupArray[trainIndex])))
    fitIndex, calibIndex = trainIndex[fitLocal], trainIndex[calibLocal]

    # 🔒 더블 체크 — 세 집합의 에피소드가 겹치면 누수다. 조용히 틀릴 수 있는 지점이다.
    fitGroups = set(groupArray[fitIndex])
    calibGroups = set(groupArray[calibIndex])
    testGroups = set(groupArray[testIndex])
    assert not (fitGroups & calibGroups), f"fit∩calib 에피소드 겹침: {fitGroups & calibGroups}"
    assert not (fitGroups & testGroups), f"fit∩test 에피소드 겹침: {fitGroups & testGroups}"
    assert not (calibGroups & testGroups), f"calib∩test 에피소드 겹침: {calibGroups & testGroups}"
    print(f"에피소드 분할  fit {len(fitGroups)} / calib {len(calibGroups)} / test {len(testGroups)}"
          "  — 겹침 없음 확인")
    print(f"표본          fit {len(fitIndex)} / calib {len(calibIndex)} / test {len(testIndex)}")
    print(f"앙상블 N={arguments.ensembleSize}\n")

    # ── 원본 앙상블 ────────────────────────────────────────────────────────
    models = compareTiers.buildEnsemble(featureMatrix[fitIndex], labelArray[fitIndex],
                                        size=arguments.ensembleSize)
    assert len(models) == arguments.ensembleSize
    classes = list(models[0].classes_)
    rawProba = np.stack([m.predict_proba(featureMatrix[testIndex]) for m in models])

    # 🔒 더블 체크 — 보정 전에 멤버가 실제로 서로 다른지. 같으면 비교가 무의미하다.
    rawRisk = riskFromProba(rawProba, classes)
    pairwiseGap = float(np.abs(rawRisk[0] - rawRisk[-1]).mean())
    assert pairwiseGap > 1e-9, "멤버가 전부 동일하다 — 부트스트랩이 작동하지 않는다"
    print(f"멤버 간 평균 위험확률 차이(첫–끝): {pairwiseGap:.5f}  — 다양성 존재 확인\n")

    results = {"raw": summarize(rawProba, classes, labelArray[testIndex])}

    # 🔒 더블 체크 — 공정성. 보정 모델은 base 가 fit 을, 보정기가 calib 를 본다.
    # 즉 fit+calib 전체를 쓴 셈이라 fit 만 쓴 원본보다 정보가 많다. F1 이 오르면
    # 보정 덕인지 데이터 덕인지 구분되지 않으므로, **같은 데이터량의 원본**을
    # 기준선으로 함께 둔다.
    fullModels = compareTiers.buildEnsemble(
        featureMatrix[trainIndex], labelArray[trainIndex], size=arguments.ensembleSize)
    fullProba = np.stack([m.predict_proba(featureMatrix[testIndex]) for m in fullModels])
    results["rawFullData"] = summarize(fullProba, classes, labelArray[testIndex])

    # ── 멤버별 보정 ────────────────────────────────────────────────────────
    for method in ("isotonic", "sigmoid"):
        calibratedProba = []
        for model in models:
            calibrator = CalibratedClassifierCV(FrozenEstimator(model), method=method)
            calibrator.fit(featureMatrix[calibIndex], labelArray[calibIndex])
            assert list(calibrator.classes_) == classes, "보정 후 클래스 순서가 바뀌었다"
            calibratedProba.append(calibrator.predict_proba(featureMatrix[testIndex]))
        results[method] = summarize(np.stack(calibratedProba), classes, labelArray[testIndex])

    print("━━━ 네 지표를 동시에 본다 ━━━")
    print(f"{'구성':<14}{'중간 p̄':>10}{'√u>0.05':>10}{'√u=0':>9}{'ECE':>9}{'macro F1':>10}")
    for name in ("raw", "rawFullData", "isotonic", "sigmoid"):
        row = results[name]
        label = {"raw": "원본 (fit만)", "rawFullData": "원본 (fit+calib)",
                 "isotonic": "보정 isotonic", "sigmoid": "보정 sigmoid"}[name]
        print(f"{label:<14}{row['middleShare']:>10.1%}{row['spreadAboveFloor']:>10.1%}"
              f"{row['zeroSpreadShare']:>9.1%}{row['ece']:>9.4f}{row['macroF1']:>10.3f}")

    print("\n━━━ 판정 — 하나라도 반대로 가면 채택하지 않는다 ━━━")
    raw = results["raw"]
    verdicts = {}
    for method in ("isotonic", "sigmoid"):
        row = results[method]
        checks = {
            "중간 p̄ 증가": row["middleShare"] > results["rawFullData"]["middleShare"],
            # 기준선은 데이터량을 맞춘 쪽이다. fit 만 쓴 원본과 비교하면 관대해진다.
            "u 보존 (0.8배 이상)":
                row["spreadAboveFloor"] >= results["rawFullData"]["spreadAboveFloor"] * 0.8,
            "ECE 감소": row["ece"] <= results["rawFullData"]["ece"],
            "F1 유지 (0.02 이내)":
                row["macroF1"] >= results["rawFullData"]["macroF1"] - 0.02,
        }
        verdicts[method] = checks
        passed = all(checks.values())
        print(f"\n  {method}: {'✅ 채택 가능' if passed else '❌ 채택 불가'}")
        for name, ok in checks.items():
            print(f"      {'✔' if ok else '✘'} {name}")

    # 🔒 F1 상승이 보정 덕인가 데이터 덕인가
    fullData = results["rawFullData"]
    print("\n━━━ F1 상승의 출처 ━━━")
    print(f"  원본(fit만) {raw['macroF1']:.3f} → 원본(fit+calib) {fullData['macroF1']:.3f}"
          f" → 보정 {max(results['isotonic']['macroF1'], results['sigmoid']['macroF1']):.3f}")
    bestCalibrated = max(results["isotonic"]["macroF1"], results["sigmoid"]["macroF1"])
    dataGain = fullData["macroF1"] - raw["macroF1"]
    calibrationGain = bestCalibrated - fullData["macroF1"]
    print(f"  데이터량 기여 {dataGain:+.3f} / 보정 기여 {calibrationGain:+.3f}")
    if abs(calibrationGain) < 0.01:
        print("  → F1 상승은 거의 **데이터량 덕**이다. 보정 자체는 성능을 바꾸지 않는다")
        print("     (보정의 목적이 성능이 아니라 확률의 신뢰성이므로 정상이다).")
    elif calibrationGain > 0:
        print("  → 데이터량을 맞춰도 보정이 F1 을 올린다. 다중 클래스에서 보정이")
        print("     argmax 를 바꿀 수 있으므로 가능한 결과다.")
    else:
        print("  → 데이터량을 맞추면 보정이 F1 을 **깎는다.** 확률 신뢰성과 성능의")
        print("     맞교환이므로 어느 쪽을 택할지 명시해야 한다.")

    print("\n  ⚠️ sigmoid 의 '√u=0 비율 0.0%' 는 다양성의 증거가 아니다 — sigmoid 가")
    print("     매끄러운 단조 함수라 서로 다른 원시값이 항상 서로 다른 값으로 가므로")
    print("     정확히 0 인 경우가 사라질 뿐이다. 판정은 √u>0.05 로 한다.")

    recommended = [m for m in ("isotonic", "sigmoid") if all(verdicts[m].values())]
    print()
    if recommended:
        best = max(recommended, key=lambda m: results[m]["middleShare"])
        print(f"  → **{best}** 를 파이프라인에 넣는다. 중간 구간 "
              f"{raw['middleShare']:.1%} → {results[best]['middleShare']:.1%}, "
              f"불확실 표본 {raw['spreadAboveFloor']:.1%} → "
              f"{results[best]['spreadAboveFloor']:.1%}")
    else:
        print("  → 어느 쪽도 네 조건을 다 만족하지 못한다. 캘리브레이션으로는 해결되지")
        print("     않으므로 실험 설계(장애 강도 다단계)로 가야 한다.")

    outputPath = pathlib.Path(__file__).resolve().parent / "calibration.json"
    outputPath.write_text(json.dumps(
        {"ensembleSize": arguments.ensembleSize, "results": results,
         "verdicts": {m: {k: bool(v) for k, v in c.items()} for m, c in verdicts.items()}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
