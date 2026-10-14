"""①→② 사다리 비교 — 위상 정보의 기여를 분리 측정한다 (H1).

게이트 ① 은 랜덤 데이터로 모델이 도는 것만 확인했다. 여기서는 사슬이 만든
**실제 지표와 §4-2 라벨**로 단계별 차이를 처음으로 잰다.

사다리에 중간 항을 넣는 이유
=============================

문서의 사다리는 ``① 규칙/임계값(위상 없음) → ② GBM + 이웃 집계`` 이고 그 차이를
*"위상 정보 자체의 기여"* 로 읽는다. 그런데 **①→② 는 두 가지가 동시에 바뀐다** —
규칙에서 학습 모델로, 그리고 위상 없음에서 위상 있음으로. 차이가 나와도 어느 쪽
덕인지 분리되지 않는다.

=========  ==========  ==========  ====================================
단계        예측기      위상        측정되는 것
=========  ==========  ==========  ====================================
①          규칙/임계값  없음        —
②-self     GBM         없음        ①→②-self = **학습 모델 자체의 기여**
②          GBM         **있음**    ②-self→② = **위상 정보의 기여**  ← H1
=========  ==========  ==========  ====================================

⚠️ 측정에 순환이 있다 — 위상 기여가 부풀려진다
================================================

§4-2 의 라벨은 **두 조건의 논리곱**이다.

.. code-block:: text

    positive(v, t) ⟺ v 가 [t, t+Δ] 에 SLO 위반         ← 미래. feature 에 없다
                     ∧ ∃u ∈ 1-hop 상류 : [t−W, t] 선행 열화  ← 과거. **feature 에 있다**

②-self 는 첫 조건만 추정할 수 있지만, ② 는 **둘째 조건을 직접 본다**. 이웃 집계
feature 에 상류의 f4(지연)·f6(에러율)과 그 윈도우 통계가 들어 있기 때문이다.
따라서 ②-self→② 차이에는 *"위상 정보가 예측에 유용하다"* 뿐 아니라
*"라벨이 상류 상태로 정의되어 있다"* 는 몫이 섞인다.

전부가 순환은 아니다 — 상류가 열화했다는 것을 알아도 **v 가 실제로 위반할지는
여전히 예측해야** 하고, ②-self 가 상류 정보 없이 0.810 을 내는 것이 그 증거다.
그러나 ②-self→② 차이를 **"위상 정보의 순수 기여"로 읽으면 과대평가**다.

**순환 몫을 실제로 재 봤다** (``--selfOnlyLabel``). 라벨에서 상류 조건을 빼고
*"v 가 위반하는가"* 단독으로 바꾼 뒤 같은 비교를 돌린다.

=========================  ==============
라벨                        위상 기여
=========================  ==============
§4-2 전파성 (상류 포함)      +0.071
v 위반 단독 (상류 제거)      +0.063
**순환 몫**                 **+0.008**
=========================  ==============

라벨이 상류를 전혀 참조하지 않아도 위상 정보가 +0.063 기여한다. **순환은
전체 기여의 11% 에 불과하며 나머지는 진짜다.** 우려는 타당했으나 양적으로 작다.

분할은 에피소드 단위다
=======================

§4-2 는 **주입 지점 단위 분할**을 요구한다 — 1Hz 수집에서 인접 스냅샷은 거의
동일하므로 랜덤 분할하면 test 표본의 이웃이 train 에 있어 사실상 답을 보고 맞힌다.

이 사슬은 주입 지점이 ``payment`` 하나뿐이라 그 축으로는 못 나눈다. 같은 성질을
갖는 가장 가까운 축이 **에피소드**이므로 그것으로 그룹을 만든다. 본 실험에서는
주입 지점이 여럿이므로 §4-2 그대로 가능하다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "chain"))

import buildDataset  # noqa: E402

ENSEMBLE_SIZE = 3
RANDOM_SEED = 20261014


class RuleBasedPredictor:
    """① 규칙/임계값 — 위상 정보를 쓰지 않는다.

    현장 관행 baseline 이다. *"지연이 기준선의 N 배를 넘거나 에러율이 임계를 넘으면
    위험"* 이라는, 대시보드 알람에 실제로 들어가는 형태다.

    ⚠️ 이기라고 만드는 것이 아니라 **②가 이것을 얼마나 이기는지 재려고** 만든다.
    따라서 ② 와 **같은 self feature 만** 본다 — 입력 지표 개수가 다르면 ①→② 차이가
    위상 정보가 아니라 관측 범위의 차이가 된다.
    """

    def __init__(self, latencyThreshold: float = 0.5, errorThreshold: float = 0.05) -> None:
        self.latencyThreshold = latencyThreshold
        self.errorThreshold = errorThreshold
        self.riskClass = "S1"

    def fit(self, features: np.ndarray, labels: np.ndarray) -> "RuleBasedPredictor":
        # 규칙은 학습하지 않는다. 다만 어느 위험 클래스를 내놓을지는 다수결로 정한다 —
        # 규칙은 "위험하다"까지만 말할 수 있고 병목 유형을 구분하지 못하기 때문이다.
        risky = [label for label in labels if label != "정상"]
        if risky:
            values, counts = np.unique(risky, return_counts=True)
            self.riskClass = values[int(np.argmax(counts))]
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        # self feature 배치: [f1..f6 현재값 6][윈도우 평균 6][slope 6][표준편차 6]
        latency = features[:, 3]    # f4 지연 분위수
        errorRate = features[:, 5]  # f6 에러율
        risky = (latency > self.latencyThreshold) | (errorRate > self.errorThreshold)
        return np.where(risky, self.riskClass, "정상")


def buildEnsemble(features: np.ndarray, labels: np.ndarray, size: int | None = None
                  ) -> list[HistGradientBoostingClassifier]:
    """부트스트랩 멤버 앙상블.

    ``size`` 를 인자로 받는다. 모듈 상수를 몽키패치해 크기를 바꾸면 호출 순서에
    따라 조용히 틀린 값이 쓰일 수 있다.
    """
    models = []
    for member in range(size if size is not None else ENSEMBLE_SIZE):
        model = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, max_depth=6,
            random_state=RANDOM_SEED + member)
        rng = np.random.default_rng(RANDOM_SEED + member)
        indexes = rng.choice(len(features), size=len(features), replace=True)
        model.fit(features[indexes], labels[indexes])
        models.append(model)
    return models


def buildCalibratedEnsemble(fitFeatures: np.ndarray, fitLabels: np.ndarray,
                            calibFeatures: np.ndarray, calibLabels: np.ndarray,
                            size: int | None = None, method: str = "sigmoid") -> list:
    """멤버별로 보정한 앙상블.

    **평균이 아니라 멤버별로 보정한다.** `p_eff = max(0, p̄ − κ√u)` 는 `p̄` 와 `√u` 가
    같은 공간에 있어야 성립하는데, 평균만 보정하면 `p̄` 는 보정 공간, `u` 는 원시
    공간이 되어 뺄셈이 의미를 잃는다.

    기본값이 ``sigmoid`` 인 이유는 isotonic 이 계단 함수라 보정 표본이 적을 때
    서로 다른 원시값을 같은 값으로 뭉갤 수 있어서다. 실측에서는 둘 다 네 조건을
    통과했으나 sigmoid 가 중간 구간을 더 넓혔다(calibrateEnsemble.py).
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator

    calibrated = []
    for model in buildEnsemble(fitFeatures, fitLabels, size=size):
        calibrator = CalibratedClassifierCV(FrozenEstimator(model), method=method)
        calibrator.fit(calibFeatures, calibLabels)
        calibrated.append(calibrator)
    return calibrated


def predictEnsemble(models: list, features: np.ndarray) -> np.ndarray:
    classes = models[0].classes_
    probabilities = np.mean([model.predict_proba(features) for model in models], axis=0)
    return classes[np.argmax(probabilities, axis=1)]


def loadAllEpisodes(chainDir: pathlib.Path, arguments) -> list[dict]:
    episodes = []
    for path in sorted((chainDir / "results").glob("*.json")):
        if not (path.name.startswith("episodes-") or path.name.startswith("remediation-")):
            continue
        for record in json.loads(path.read_text(encoding="utf-8")):
            if record.get("injectedAt") is None:
                continue
            episodes.append(record)
    built = []
    for episode in episodes:
        try:
            built.append(buildDataset.buildEpisodeSamples(episode, chainDir, arguments))
        except FileNotFoundError:
            continue   # 중단된 실행이 남긴 고아 기록
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description="①→② 사다리 비교 (H1)")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--selfOnlyLabel", action="store_true",
                        help="라벨에서 상류 선행 열화 조건을 뺀다. 순환 몫을 분리하기 위한 대조")
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    if arguments.selfOnlyLabel:
        # 상류 조건을 사실상 무력화한다 — 배수 0 이면 어떤 이웃이든 "열화"로 판정되어
        # 라벨이 "v 가 위반하는가" 단독이 된다. 라벨 정의에서 위상을 빼는 것이 목적이다.
        arguments.precursorMultiplier = 0.0
    episodes = loadAllEpisodes(chainDir, arguments)
    if not episodes:
        print("에피소드가 없다. code/chain 을 먼저 돌려야 한다.")
        return 1

    selfFeatures, neighbourFeatures, labels, groups = [], [], [], []
    for index, episode in enumerate(episodes):
        for sample in episode["samples"]:
            selfFeatures.append(sample["self"])
            neighbourFeatures.append(sample["neighbour"])
            labels.append(sample["label"])
            groups.append(index)

    selfMatrix = np.stack(selfFeatures)
    combinedMatrix = np.concatenate([selfMatrix, np.stack(neighbourFeatures)], axis=1)
    labelArray = np.array(labels)
    groupArray = np.array(groups)

    values, counts = np.unique(labelArray, return_counts=True)
    print(f"에피소드 {len(episodes)}개 / 표본 {len(labelArray)}개")
    print(f"self {selfMatrix.shape[1]}차원  ②(self+이웃) {combinedMatrix.shape[1]}차원")
    print("클래스 분포: " + ", ".join(
        f"{v} {c}({c / len(labelArray):.1%})" for v, c in zip(values, counts)))
    print(f"분할: 에피소드 단위 GroupKFold(k={arguments.folds})")
    print(f"라벨: {'v 위반 단독 (상류 조건 제거)' if arguments.selfOnlyLabel else '§4-2 전파성 (상류 조건 포함)'}\n")

    if len(values) < 2:
        print("라벨이 한 종류뿐이라 비교가 성립하지 않는다.")
        return 1

    tiers = {
        "① 규칙/임계값 (위상 없음)": ("rule", selfMatrix),
        "②-self GBM (위상 없음)": ("gbm", selfMatrix),
        "② GBM + 이웃 집계": ("gbm", combinedMatrix),
    }

    scores: dict[str, list[float]] = {name: [] for name in tiers}
    lastPredictions: dict[str, tuple] = {}
    splitter = GroupKFold(n_splits=min(arguments.folds, len(set(groups))))

    for trainIndex, testIndex in splitter.split(selfMatrix, labelArray, groupArray):
        for name, (kind, matrix) in tiers.items():
            trainX, testX = matrix[trainIndex], matrix[testIndex]
            trainY, testY = labelArray[trainIndex], labelArray[testIndex]
            if kind == "rule":
                model = RuleBasedPredictor().fit(trainX, trainY)
                predicted = model.predict(testX)
            else:
                predicted = predictEnsemble(buildEnsemble(trainX, trainY), testX)
            scores[name].append(f1_score(testY, predicted, average="macro", zero_division=0))
            lastPredictions[name] = (testY, predicted)

    print("━━━ 사다리 (macro F1, 에피소드 단위 교차검증) ━━━")
    print(f"{'단계':<28}{'평균':>8}{'표준편차':>10}{'폴드별'}")
    means = {}
    for name in tiers:
        fold = scores[name]
        means[name] = float(np.mean(fold))
        print(f"{name:<28}{np.mean(fold):>8.3f}{np.std(fold):>10.3f}  "
              + " ".join(f"{v:.3f}" for v in fold))

    ruleName = "① 규칙/임계값 (위상 없음)"
    selfName = "②-self GBM (위상 없음)"
    fullName = "② GBM + 이웃 집계"
    modelGain = means[selfName] - means[ruleName]
    topologyGain = means[fullName] - means[selfName]

    print("\n━━━ 분리 측정 ━━━")
    print(f"  ①→②-self  학습 모델 자체의 기여   {modelGain:+.3f}")
    print(f"  ②-self→②  **위상 정보의 기여**     {topologyGain:+.3f}   ← H1")
    print(f"  ①→②       합계                    {means[fullName] - means[ruleName]:+.3f}")

    print("\n━━━ 판정 ━━━")
    if topologyGain > 0.02:
        print("  ✅ 위상 정보가 예측에 기여한다. 이웃 집계 feature 가 자기 지표만으로는")
        print("     얻을 수 없는 정보를 더한다 — §4-4 에서 구분자가 이웃 노드에만")
        print("     존재했던 것과 같은 방향의 결과다.")
    elif topologyGain < -0.02:
        print("  ⚠️ 이웃 집계가 오히려 성능을 깎는다. 차원이 늘어난 만큼 과적합했거나,")
        print("     이 규모(3노드 사슬)에서는 이웃 정보가 잡음으로 작용한다.")
    else:
        print("  ⚠️ 위상 정보의 기여가 측정 한계 안에 있다. 3노드 사슬은 1-hop 상류가")
        print("     최대 1개뿐이라 집계가 평균이 아니라 복사에 가깝다. 본 실험(6~7노드,")
        print("     깊이 4단)에서 재측정해야 한다.")

    print("\n━━━ ② 의 클래스별 성능 (마지막 폴드) ━━━")
    testY, predicted = lastPredictions[fullName]
    print(classification_report(testY, predicted, zero_division=0))

    payload = {
        "episodeCount": len(episodes), "sampleCount": int(len(labelArray)),
        "selfDim": int(selfMatrix.shape[1]), "combinedDim": int(combinedMatrix.shape[1]),
        "classDistribution": {str(v): int(c) for v, c in zip(values, counts)},
        "macroF1": {name: scores[name] for name in tiers},
        "means": means, "modelGain": modelGain, "topologyGain": topologyGain,
        "selfOnlyLabel": bool(arguments.selfOnlyLabel),
    }
    suffix = "-selfOnlyLabel" if arguments.selfOnlyLabel else ""
    outputPath = pathlib.Path(__file__).resolve().parent / f"tierComparison{suffix}.json"
    outputPath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
