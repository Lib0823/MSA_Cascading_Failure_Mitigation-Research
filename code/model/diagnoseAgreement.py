"""앙상블 합의를 두 정의로 나눠 잰다 — 설계가 말한 "동의하면 신뢰도 높음".

§2-B 는 `u_v` 를 **앙상블 분산(멤버 불일치)** 으로 정의하고 구현은
`var(1 − P(정상))` 를 쓴다. 그런데 "동일한 답이 여러 개 나오면 신뢰도가 높다"는
서술은 **클래스 투표의 일치**를 가리킨다. 둘은 같지 않다.

====================  ====================================================
정의                   셋 다 S1 이라 답하되 확률이 0.6/0.8/0.95 일 때
====================  ====================================================
투표 일치             **3/3 완전 일치** — 신뢰도 최고로 읽힌다
확률 분산             **0.02 로 작지 않다** — 불확실로 읽힌다
====================  ====================================================

어느 쪽을 쓰느냐로 Safety Guard 의 동작이 달라지므로 둘 다 재고 비교한다.

**N=3 의 해상도 문제**도 함께 본다. 멤버가 셋이면 투표 일치는 3/3 아니면 2/3
두 값뿐이다. §2-A 가 Deep Ensemble N=5 에서 N=3 으로 줄인 것이 불확실성
해상도를 떨어뜨렸는지 확인한다(우려 4).
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import compareTiers  # noqa: E402

NORMAL_LABEL = "정상"


def memberOutputs(models: list, features: np.ndarray):
    """멤버별 (예측 클래스, 위험 확률)."""
    classes = list(models[0].classes_)
    normalIndex = classes.index(NORMAL_LABEL)
    proba = np.stack([model.predict_proba(features) for model in models])  # [M, N, C]
    predicted = np.stack([np.array(classes)[np.argmax(p, axis=1)] for p in proba])  # [M, N]
    risk = 1.0 - proba[:, :, normalIndex]
    return predicted, risk


def main() -> int:
    parser = argparse.ArgumentParser(description="앙상블 합의 — 투표 일치 vs 확률 분산")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--ensembleSizes", default="3,5,9")
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

    splitter = GroupKFold(n_splits=4)
    trainIndex, testIndex = next(iter(splitter.split(featureMatrix, labelArray, groupArray)))

    print(f"평가 {len(testIndex)} 표본\n")
    print("━━━ 두 정의가 같은 말을 하는가 ━━━")
    print(f"{'N':>3}{'3/3 일치':>12}{'완전 일치':>11}{'√u 중앙값':>12}{'√u>0.05':>10}"
          f"{'유효 해상도':>12}")

    rows = []
    for size in [int(s) for s in arguments.ensembleSizes.split(",")]:
        models = compareTiers.buildEnsemble(
            featureMatrix[trainIndex], labelArray[trainIndex], size=size)
        assert len(models) == size, f"앙상블 크기 불일치: {len(models)} != {size}"

        predicted, risk = memberOutputs(models, featureMatrix[testIndex])
        # 투표 일치 — 다수 득표 비율. 1.0 이면 전원 동일한 답.
        voteShare = np.array([
            collections.Counter(predicted[:, i]).most_common(1)[0][1] / size
            for i in range(predicted.shape[1])])
        spread = np.sqrt(risk.var(axis=0))
        unanimous = float((voteShare == 1.0).mean())
        # 투표 일치가 가질 수 있는 서로 다른 값의 개수 = 불확실성 해상도
        resolution = len(np.unique(voteShare))

        rows.append({"size": size, "unanimous": unanimous,
                     "spreadMedian": float(np.median(spread)),
                     "spreadAbove": float((spread > 0.05).mean()), "resolution": resolution})
        print(f"{size:>3}{unanimous:>12.1%}{(spread < 1e-6).mean():>11.1%}"
              f"{np.median(spread):>12.4f}{(spread > 0.05).mean():>10.1%}{resolution:>12}")

    print("\n  '3/3 일치' = 멤버 전원이 같은 클래스를 답한 비율 (설계 서술의 정의)")
    print("  '완전 일치' = 확률까지 동일해 √u = 0 인 비율 (구현의 정의)")

    base = rows[0]
    print(f"\n━━━ 판정 ━━━")
    gap = base["unanimous"] - (1.0 - base["spreadAbove"])
    if abs(base["unanimous"] - (1.0 - (base["spreadMedian"] > 0))) > 0.05:
        pass
    print(f"  N=3 에서 투표는 {base['unanimous']:.1%} 가 만장일치인데")
    print(f"  확률 분산이 의미 있는(√u>0.05) 표본은 {base['spreadAbove']:.1%} 다.")
    if base["unanimous"] > 0.9 and base["spreadAbove"] < 0.2:
        print("  → 두 정의가 **같은 말을 한다.** 멤버들이 클래스도 확률도 거의 일치한다.")
        print("     설계가 말한 '동의하면 신뢰도 높음'이 작동하고 있으며, 대부분의")
        print("     표본에서 **실제로 신뢰도가 높다**. 모델 결함이 아니라 문제가 쉬운 것이다.")
    else:
        print("  → 두 정의가 **다른 말을 한다.** 어느 쪽을 u 로 쓸지 문서에 명시해야 한다.")

    print("\n━━━ N 을 늘리면 해상도가 올라가는가 ━━━")
    for row in rows:
        print(f"  N={row['size']}  투표 만장일치 {row['unanimous']:.1%}  "
              f"√u>0.05 {row['spreadAbove']:.1%}  투표 해상도 {row['resolution']}단계")
    if rows[-1]["spreadAbove"] > rows[0]["spreadAbove"] * 1.3:
        print("\n  ✅ N 을 늘리면 불확실성이 드러난다. N=3 의 해상도 한계가 실재한다.")
        print("     §2-A 가 비용 때문에 N=5 → N=3 으로 줄인 것(우려 4)을 재검토할 근거다.")
    else:
        print("\n  ❌ N 을 늘려도 불확실성이 거의 안 는다. 멤버 수가 원인이 아니다 —")
        print("     데이터에 불확실성 자체가 없다는 쪽을 지지한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
