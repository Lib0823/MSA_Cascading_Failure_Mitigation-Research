"""예측기를 학습해 디스크에 남긴다 — 폐루프는 추론만 한다.

지금까지는 평가 때마다 학습했다. 폐루프 제어기는 **N초 주기로 추론만** 해야
하므로(§2-D Tier 2) 학습과 추론을 분리한다.

함께 저장하는 것
================

모델만 저장하면 안 된다. **추론 시점에 feature 를 같은 방식으로 만들 수 있어야**
하므로 다음을 함께 남긴다.

=====================  ====================================================
항목                    왜 필요한가
=====================  ====================================================
클래스 순서             `p̄ = 1 − P(정상)` 의 정상 인덱스를 찾는 데 쓴다
슬롯 기준선             f3·f4 가 정상 구간 기준선 대비 비율이다. 기준선이
                       바뀌면 같은 지표가 다른 feature 가 된다
호출 그래프             이웃 집계의 1-hop·2-hop 범위를 정한다
윈도우 길이             self feature 의 시계열 통계 구간
=====================  ====================================================

**기준선을 함께 저장하지 않으면 학습과 추론의 feature 공간이 어긋난다.** 조용히
틀리는 종류의 오류라 명시적으로 묶어 둔다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import joblib
import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "model"))
sys.path.insert(0, str(HERE.parent / "chain"))

import buildDataset  # noqa: E402
import compareTiers  # noqa: E402
import episodeValidity  # noqa: E402

DEFAULT_ARTIFACT = HERE / "predictor.joblib"


def main() -> int:
    parser = argparse.ArgumentParser(description="폐루프용 예측기 학습·저장")
    parser.add_argument("--chainDir", default=str(HERE.parent / "chain"))
    parser.add_argument("--out", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--ensembleSize", type=int, default=5)
    parser.add_argument("--calibration", default="sigmoid", choices=["none", "sigmoid", "isotonic"])
    # 라벨 파라미터. 학습 데이터의 라벨을 만드는 값이며 추론에는 쓰이지 않는다.
    parser.add_argument("--horizonSeconds", type=float, default=30.0,
                        help="Δ. ℓₐ(Scale-out) 약 10s 보다 충분히 커야 한다 (§4-2 실측)")
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    # 기준선이 참조로 부적격인 에피소드는 `f3`·`f4` 가 그 기준선으로 정규화되므로
    # **feature 가 틀린다**([[research/challenges.md]] J17). 기본으로 뺀다.
    parser.add_argument("--keepInvalid", action="store_true",
                        help="유효성 판정에서 탈락한 에피소드도 학습에 넣는다(대조용)")
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    episodes = compareTiers.loadAllEpisodes(chainDir, arguments)
    if not episodes:
        print("에피소드가 없다. code/chain 을 먼저 돌려야 한다.")
        return 1

    if arguments.keepInvalid:
        print("⚠️ 유효성 탈락 에피소드를 포함해 학습한다(--keepInvalid) — 대조용")
    else:
        invalid = episodeValidity.invalidTracePaths(chainDir)
        kept = [e for e in episodes if e["episodeKey"] not in invalid]
        dropped = len(episodes) - len(kept)
        episodes = kept
        if dropped:
            print(f"🔴 유효성 탈락 {dropped}개를 뺐다 — 장애가 느려지지 않아 기준선이 "
                  f"참조로 부적격이다 (code/model/episodeValidity.py)")
        if not episodes:
            print("유효한 에피소드가 없다.")
            return 1

    features, labels, groups = [], [], []
    for index, episode in enumerate(episodes):
        for sample in episode["samples"]:
            features.append(np.concatenate([sample["self"], sample["neighbour"]]))
            labels.append(sample["label"])
            groups.append(index)
    featureMatrix = np.stack(features)
    labelArray = np.array(labels)
    groupArray = np.array(groups)

    values, counts = np.unique(labelArray, return_counts=True)
    print(f"에피소드 {len(episodes)} / 표본 {len(labelArray)} / {featureMatrix.shape[1]}차원")
    print("클래스: " + ", ".join(f"{v} {c}" for v, c in zip(values, counts)))
    print(f"Δ={arguments.horizonSeconds}s  앙상블 N={arguments.ensembleSize}  "
          f"보정 {arguments.calibration}\n")

    if arguments.calibration == "none":
        models = compareTiers.buildEnsemble(featureMatrix, labelArray,
                                            size=arguments.ensembleSize)
    else:
        # 보정기는 base 가 보지 않은 표본으로 학습해야 한다. 에피소드 단위로
        # 쪼개되 **층화**한다 — 이 데이터는 에피소드 하나가 시나리오 하나라 라벨이
        # 그룹에 쏠려 있어(S3 는 12개 에피소드에만 있다) 단순 GroupKFold 로는
        # 보정 표본에 클래스가 빠질 수 있다. 아래 가드가 그때 ValueError 로
        # 실패하므로 조용히 틀리지는 않으나, **학습이 아예 안 되는 것**도 곤란하다.
        splitter = StratifiedGroupKFold(n_splits=3, shuffle=True,
                                        random_state=compareTiers.RANDOM_SEED)
        fitLocal, calibLocal = next(iter(splitter.split(featureMatrix, labelArray, groupArray)))
        assert not (set(groupArray[fitLocal]) & set(groupArray[calibLocal])), "fit∩calib 누수"
        models = compareTiers.buildCalibratedEnsemble(
            featureMatrix[fitLocal], labelArray[fitLocal],
            featureMatrix[calibLocal], labelArray[calibLocal],
            size=arguments.ensembleSize, method=arguments.calibration)

    classes = list(models[0].classes_)
    artifact = {
        "models": models,
        "classes": classes,
        "selfDim": buildDataset.SELF_DIM,
        "windowSamples": buildDataset.WINDOW_SAMPLES,
        "aggregationHops": buildDataset.AGGREGATION_HOPS,
        "featureDim": int(featureMatrix.shape[1]),
        "trainedOn": {"episodes": len(episodes), "samples": int(len(labelArray)),
                      "horizonSeconds": arguments.horizonSeconds,
                      "calibration": arguments.calibration,
                      "ensembleSize": arguments.ensembleSize},
    }
    outputPath = pathlib.Path(arguments.out)
    joblib.dump(artifact, outputPath)
    print(f"저장: {outputPath}  ({outputPath.stat().st_size / 1024:.0f} KB)")
    print(f"클래스 순서 {classes} — 추론 시 정상 인덱스를 여기서 찾는다")

    # 🔒 되읽어서 추론이 되는지 확인한다. 저장은 됐는데 못 읽는 경우가 있다.
    reloaded = joblib.load(outputPath)
    probe = reloaded["models"][0].predict_proba(featureMatrix[:1])
    assert probe.shape == (1, len(classes)), f"되읽기 후 shape 불일치: {probe.shape}"
    print(f"되읽기 확인 — 추론 출력 {probe.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
