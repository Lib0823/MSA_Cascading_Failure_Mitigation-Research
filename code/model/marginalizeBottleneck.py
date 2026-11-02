"""병목 사후확률 주변화의 효과를 **오프라인으로** 추정한다.

왜 이 추정이 먼저인가
=====================

[[research/challenges.md]] J26 이 구조적 비일관을 특정했다 — `κ` 는 위반 확률의
불확실성을 할인하면서 **병목 유형은 `argmax` 로 확실한 것처럼 취급한다.** 그리고
그 `argmax` 가 `θ` 를 고르므로 **개입 여부를 정한다.**

주변화하면 `θ` 식이 보존된다.

    E[비용|a] = Σ_b P(b)·[(1−mₐ(b))L + Dₐ] + P(정상)·Dₐ·Rₐ
    m̄ₐ = Σ_b P(b)·mₐ(b) / p̄        (위반이 있다는 조건부 기대 완화효과)
    θₐ = Dₐ·Rₐ / (m̄ₐ·L − Dₐ(1−Rₐ))

`argmax` 는 `P(b)` 가 한 점에 몰린 특수경우이므로 **패치가 아니라 일반화**다.

**그런데 구현하면 `θ` 가 전부 바뀌어 수집한 데이터가 또 무효가 된다.** 재측정은
33분이고, 이미 두 번 무효화했다([[research/challenges.md]] J23·J24). 그래서
**에피소드 코퍼스로 먼저 추정한다** — 머신도 재실행도 필요 없다.

⚠️ **순환 주의**: 배포된 `predictor.joblib` 은 이 코퍼스로 학습됐으므로 사후확률이
**실제보다 뾰족하다.** 뾰족한 사후확률은 주변화를 **덜 유용하게** 보이게 하므로,
이 추정은 **보수적**이다 — 여기서 효과가 나오면 실제로는 더 크다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

import joblib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import episodeValidity  # noqa: E402
import policyEngine  # noqa: E402
from compareTiers import loadAllEpisodes  # noqa: E402

# controlLoop.py 와 **같은 상수**를 쓴다. 어긋나면 normalIndex 가 틀린다.
NORMAL_LABEL = "정상"
KAPPA = 1.0
AVAILABILITY_LOSS = 1.0


def thresholdFor(mitigation: float, disruption: float, reversibility: float) -> float:
    denominator = mitigation * AVAILABILITY_LOSS - disruption * (1.0 - reversibility)
    if denominator <= 0:
        return float("inf")
    return disruption * reversibility / denominator


def holdUnder(mitigations: dict[str, float], profiles: list, effectiveRisk: float
              ) -> tuple[bool, str | None, float]:
    """그 완화효과 배정에서 보류인가. 엔진의 규칙과 같다 — 유한한 `θ` 중 최소와 비교."""
    best, bestThreshold = None, float("inf")
    for profile in profiles:
        threshold = thresholdFor(mitigations.get(profile.name, 0.0),
                                 profile.disruption, profile.reversibility)
        if threshold < bestThreshold:
            best, bestThreshold = profile.name, threshold
    if best is None or effectiveRisk < bestThreshold:
        return True, None, bestThreshold
    return False, best, bestThreshold


def main() -> int:
    parser = argparse.ArgumentParser(description="병목 주변화 효과 오프라인 추정")
    parser.add_argument("--chainDir", default=str(HERE.parent / "chain"))
    parser.add_argument("--predictor", default=str(HERE.parent / "control" / "predictor.joblib"))
    parser.add_argument("--windowSamples", type=int, default=10)
    parser.add_argument("--aggregationHops", type=int, default=2)
    # 라벨링 파라미터 — minimalObservation.py 와 **같은 기본값**을 쓴다. 어긋나면
    # 여기가 보는 표본 집합이 다른 분석과 달라져 비교가 성립하지 않는다.
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--keepInvalid", action="store_true")
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    episodes = loadAllEpisodes(chainDir, arguments)
    if not episodes:
        print("에피소드가 없다.")
        return 1
    if not arguments.keepInvalid:
        invalid = episodeValidity.invalidTracePaths(chainDir)
        before = len(episodes)
        episodes = [e for e in episodes if e["episodeKey"] not in invalid]
        if before != len(episodes):
            print(f"🔴 유효성 탈락 {before - len(episodes)}개 제외 (episodeValidity.py)")

    selfFeatures, neighbourFeatures, labels = [], [], []
    for episode in episodes:
        for sample in episode["samples"]:
            selfFeatures.append(sample["self"])
            neighbourFeatures.append(sample["neighbour"])
            labels.append(sample["label"])
    matrix = np.concatenate([np.stack(selfFeatures), np.stack(neighbourFeatures)], axis=1)
    labelArray = np.array(labels)

    artifact = joblib.load(arguments.predictor)
    models = artifact["models"]
    classes = [str(c) for c in artifact["classes"]]   # np.str_ 를 벗긴다
    if matrix.shape[1] != artifact["featureDim"]:
        print(f"🔴 feature 차원 불일치: {matrix.shape[1]} != {artifact['featureDim']}")
        return 1
    normalIndex = classes.index(NORMAL_LABEL)
    bottleneckNames = [c for c in classes if c != NORMAL_LABEL]

    memberProba = np.stack([m.predict_proba(matrix) for m in models])   # (N, samples, C)
    meanProba = memberProba.mean(axis=0)
    memberRisk = 1.0 - memberProba[:, :, normalIndex]
    meanRisk = memberRisk.mean(axis=0)
    rootU = np.sqrt(memberRisk.var(axis=0))
    effectiveRisk = np.maximum(0.0, meanRisk - KAPPA * rootU)

    print(f"에피소드 {len(episodes)}개 / 표본 {len(labelArray)}개 / 클래스 {classes}")
    print(f"⚠️ 배포 예측기가 이 코퍼스로 학습됐으므로 사후확률이 뾰족하다 — **보수적 추정**이다")
    print(f"\np̄   p50 {np.median(meanRisk):.4f}   √u p50 {np.median(rootU):.4f}   "
          f"p_eff p50 {np.median(effectiveRisk):.4f}")

    # 사후확률이 실제로 퍼져 있는가. 퍼져 있지 않으면 주변화할 것이 없다.
    bottleneckIndexes = [classes.index(b) for b in bottleneckNames]
    conditional = meanProba[:, bottleneckIndexes]
    totals = conditional.sum(axis=1, keepdims=True)
    conditional = np.divide(conditional, totals, out=np.zeros_like(conditional),
                            where=totals > 0)
    topShare = conditional.max(axis=1)
    print(f"\n병목 조건부 사후확률의 최대값 (1.0 이면 argmax 와 같다)")
    for quantile in (0.1, 0.25, 0.5, 0.75, 0.9):
        print(f"  p{int(quantile * 100):<3} {np.quantile(topShare, quantile):.3f}")
    print(f"  0.9 미만(= 퍼져 있음) 비율 {np.mean(topShare < 0.9):.1%}")

    profiles = [p for p in policyEngine.defaultProfiles() if p.nodeType == "service"]
    changed = collections.Counter()
    rows = []
    for index in range(len(labelArray)):
        probabilities = {b: float(conditional[index, j]) for j, b in enumerate(bottleneckNames)}
        argmaxBottleneck = max(probabilities, key=probabilities.get)

        argmaxMitigation = {p.name: p.mitigationByBottleneck.get(argmaxBottleneck, 0.0)
                            for p in profiles}
        marginalMitigation = {
            p.name: sum(probabilities[b] * p.mitigationByBottleneck.get(b, 0.0)
                        for b in bottleneckNames)
            for p in profiles}

        heldArg, actionArg, _ = holdUnder(argmaxMitigation, profiles, effectiveRisk[index])
        heldMar, actionMar, _ = holdUnder(marginalMitigation, profiles, effectiveRisk[index])
        changed[(heldArg, heldMar)] += 1
        rows.append({"label": labelArray[index], "heldArg": heldArg, "heldMar": heldMar,
                     "actionArg": actionArg, "actionMar": actionMar})

    total = len(rows)
    print(f"\n{'argmax':<9}{'주변화':<9}{'건':>7}{'비율':>8}")
    for (heldArg, heldMar), count in sorted(changed.items()):
        label = lambda held: "보류" if held else "개입"
        mark = "  ← 바뀜" if heldArg != heldMar else ""
        print(f"{label(heldArg):<9}{label(heldMar):<9}{count:>7}{count / total:>8.1%}{mark}")

    holdArg = sum(1 for r in rows if r["heldArg"]) / total
    holdMar = sum(1 for r in rows if r["heldMar"]) / total
    print(f"\n보류율: argmax {holdArg:.1%} → 주변화 {holdMar:.1%}  ({holdMar - holdArg:+.1%}p)")

    # 정상 라벨 표본에서의 개입 — 그것이 오탐이다.
    normals = [r for r in rows if r["label"] == NORMAL_LABEL]
    if normals:
        fpArg = sum(1 for r in normals if not r["heldArg"]) / len(normals)
        fpMar = sum(1 for r in normals if not r["heldMar"]) / len(normals)
        print(f"정상 라벨 {len(normals)}건의 개입률(오탐): "
              f"argmax {fpArg:.1%} → 주변화 {fpMar:.1%}  ({fpMar - fpArg:+.1%}p)")
    faults = [r for r in rows if r["label"] != NORMAL_LABEL]
    if faults:
        missArg = sum(1 for r in faults if r["heldArg"]) / len(faults)
        missMar = sum(1 for r in faults if r["heldMar"]) / len(faults)
        print(f"장애 라벨 {len(faults)}건의 보류율(미개입): "
              f"argmax {missArg:.1%} → 주변화 {missMar:.1%}  ({missMar - missArg:+.1%}p)")

    print(f"\n조치 선택의 변화")
    pairs = collections.Counter((r["actionArg"], r["actionMar"]) for r in rows)
    for (before, after), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
        mark = "" if before == after else "  ← 바뀜"
        print(f"  {str(before):<18} → {str(after):<18}{count:>7}{mark}")

    print("\n⚠️ 이것은 **표본 수준 추정**이다. 폐루프에서는 조치가 바뀌면 다음 틱의")
    print("   위험도가 달라지므로([[research/challenges.md]] J24) 실제 효과는 다를 수 있다.")
    print("   용도는 **구현 가치가 있는지 가늠하는 것**이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
