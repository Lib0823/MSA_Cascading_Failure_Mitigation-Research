"""최소 관측 집합 (H7) — 어떤 지표를 수집해야 S1 과 S3 가 갈리는가.

왜 이것이 독립 산출인가
=======================

Policy Engine 은 *"예측에 따라 자동으로 조치한다"* 라 현업 도입 장벽이 높지만,
H7 은 *"이 지표를 추가로 수집하면 S1 과 S3 가 구분된다"* 이므로 **수집만 늘리면
끝**이다. 모델 배포 없이 적용 가능한 유일한 산출이다(research/timeline.md).

②(GBM)를 주력으로 고른 근거도 여기다 — 성능이 아니라 **feature 중요도로 최소
관측 집합을 뽑을 수 있어서**다(§2-A). H7 을 자르면 *"왜 GBM 인가"* 도 흐려진다.

전수 탐색을 쓴다
================

수집 단위는 174 차원 feature 가 아니라 **원시 지표**다. ``f2 의 slope`` 만
수집하는 것은 불가능하기 때문이다. 그래서 탐색 공간은 슬롯 단위다.

==  ==========================  ======================================
슬롯  원시 지표                   수집 가능?
==  ==========================  ======================================
f1  ``poolActive / poolTotal``  ✅
f2  ``poolPending`` 기반 대기율   ✅ — H5b 가 지목한 구분자(350배 차이)
f3  ``throughputRps``           ✅
f4  ``latencyP95``              ✅ — 표준 지표
f5  컨테이너 자원 사용률          ❌ **결손**. 게이트 ② 에서 채워진다
f6  ``errorRate``               ✅ — 표준 지표
==  ==========================  ======================================

수집 가능한 슬롯이 5종이므로 부분집합이 31개뿐이다. **순차 선택 대신 전수
탐색한다** — 순차 선택은 탐욕적이라 조합 효과를 놓치는데, 31개는 그냥 다 돈다.

슬롯을 빼면 **self·이웃 집계·하류 전부에서 뺀다.** ``poolPending`` 을 수집하지
않기로 하면 이웃의 것도 없다. 구조 feature 4종(위상)은 지표가 아니라 호출
그래프이므로 항상 남긴다.

두 과제를 따로 본다
===================

- **3클래스** (정상/S1/S3) — 전체 예측 과제의 성능
- **S1 vs S3 이진** — H7 의 직접 답. S1·S3 표본만으로 학습·평가한다.
  H5b 가 *"표준 지표로는 구분 불가"* 라 했으므로 여기서 f4·f6 만 주었을 때
  성능이 낮고 f2 를 더하면 오르는 것이 확인되어야 한다

탐색은 N=1, 확인은 N=5
=======================

31개 부분집합 × 2과제를 N=5 로 돌면 77분이고 N=1 이면 15분이다. 그런데 전체
집합의 macro F1 이 **N=1 과 N=5 모두 0.888** 로 같았다 — 앙상블은 `p_eff` 의
불확실성 `u` 를 만들기 위한 것이고, 여기서 필요한 것은 부분집합 **순위**뿐이다.

그래서 **탐색은 N=1 로 하고, 뽑힌 최소 집합과 전체 집합만 N=5 로 다시 잰다.**
순위가 뒤집힐 수 있는 지점에만 비용을 쓴다.

⚠️ **이 분석은 Docker Compose 데이터다.** HPA 가 없고 f5 가 결손이다. 게이트 ②
에서 재검증이 필요하다 — 다만 f2(풀 획득 대기)는 앱 레벨 지표이므로 HPA 유무에
영향이 작을 것으로 본다. 그 예상 자체도 게이트 ② 에서 확인할 대상이다.
"""

from __future__ import annotations

import argparse
import itertools
import pathlib
import sys

import numpy as np
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from compareTiers import (  # noqa: E402
    RANDOM_SEED, buildEnsemble, loadAllEpisodes, predictEnsemble,
)

SLOT_NAMES = ("f1", "f2", "f3", "f4", "f5", "f6")
SLOT_LABELS = {
    "f1": "풀 점유율",
    "f2": "풀 획득 대기율",
    "f3": "처리량 대비 용량",
    "f4": "지연 p95",
    "f5": "자원 사용률(결손)",
    "f6": "에러율",
}
# f5 는 전부 0 이라 탐색에서 뺀다. 빼는 것이 아니라 "수집 자체가 없다" 는 사실이다.
COLLECTABLE = ("f1", "f2", "f3", "f4", "f6")
# 표준 지표만 쓴 집합 — H5b 의 "구분 불가" 주장과 대조된다.
STANDARD_ONLY = ("f4", "f6")

NUM_SLOTS = 6
STATS_PER_BLOCK = 4        # 현재값·윈도우 평균·slope·표준편차
SELF_BLOCK = NUM_SLOTS * STATS_PER_BLOCK          # 24
HOP_BLOCK = SELF_BLOCK * 3                        # mean·max·sum → 72
HOP_OFFSETS = (SELF_BLOCK, SELF_BLOCK + HOP_BLOCK)   # 24, 96
DOWNSTREAM_OFFSET = SELF_BLOCK + 2 * HOP_BLOCK       # 168 — [f2, f4]
STRUCTURAL_OFFSET = DOWNSTREAM_OFFSET + 2            # 170 — 4종
FEATURE_DIM = STRUCTURAL_OFFSET + 4                  # 174


def slotColumns(slotIndex: int) -> list[int]:
    """슬롯 하나가 차지하는 모든 열.

    self 1블록 + hop 2개×3집계 = 7블록이며 블록마다 통계 4종이 붙는다.
    하류 벡터는 ``[f2, f4]`` 둘뿐이므로 해당 슬롯에만 더한다.
    """
    columns = [stat * NUM_SLOTS + slotIndex for stat in range(STATS_PER_BLOCK)]
    for hopOffset in HOP_OFFSETS:
        for aggregation in range(3):
            base = hopOffset + aggregation * SELF_BLOCK
            columns += [base + stat * NUM_SLOTS + slotIndex
                        for stat in range(STATS_PER_BLOCK)]
    if slotIndex == 1:      # 하류의 f2
        columns.append(DOWNSTREAM_OFFSET)
    if slotIndex == 3:      # 하류의 f4
        columns.append(DOWNSTREAM_OFFSET + 1)
    return columns


STRUCTURAL_COLUMNS = list(range(STRUCTURAL_OFFSET, FEATURE_DIM))


def columnsFor(slots: tuple[str, ...]) -> list[int]:
    """선택한 슬롯들의 열 + 구조 feature. 위상은 지표가 아니므로 항상 남긴다."""
    selected: list[int] = []
    for slot in slots:
        selected += slotColumns(SLOT_NAMES.index(slot))
    return sorted(set(selected) | set(STRUCTURAL_COLUMNS))


def verifyLayout(matrix: np.ndarray) -> None:
    """열 배치 가정을 데이터로 검증한다.

    버그가 나면 **조용히 틀린다** — 엉뚱한 열을 "f2" 라 부르고 그 결과를
    최소 관측 집합이라 보고하게 된다. 그래서 단정으로 묶는다.
    """
    if matrix.shape[1] != FEATURE_DIM:
        raise SystemExit(f"feature 차원이 {matrix.shape[1]} 이다. {FEATURE_DIM} 를 가정했다 "
                         "— buildDataset.py 가 바뀌었으면 이 파일의 배치 상수도 고쳐야 한다")
    everyColumn = set(STRUCTURAL_COLUMNS)
    for index in range(NUM_SLOTS):
        everyColumn |= set(slotColumns(index))
    if everyColumn != set(range(FEATURE_DIM)):
        raise SystemExit("슬롯 열 매핑이 전체를 덮지 않는다 — 배치 상수가 틀렸다")
    # f5 는 결손이므로 모든 f5 열이 정확히 0 이어야 한다. 아니면 매핑이 어긋난 것이다.
    f5Columns = [c for c in slotColumns(SLOT_NAMES.index("f5")) if c < DOWNSTREAM_OFFSET]
    residual = float(np.abs(matrix[:, f5Columns]).max())
    if residual != 0.0:
        raise SystemExit(f"f5 열에 0 이 아닌 값이 있다(max {residual}). f5 는 미수집이므로 "
                         "전부 0 이어야 한다 — 열 매핑이 어긋났다는 뜻이다")
    print(f"✅ 열 배치 검증: {FEATURE_DIM}차원 전수 피복, f5 열 {len(f5Columns)}개 모두 0")


def crossValidatedF1(matrix: np.ndarray, labels: np.ndarray, groups: np.ndarray,
                     folds: int, ensembleSize: int, average: str) -> tuple[float, float]:
    """에피소드 단위 GroupKFold. 같은 에피소드가 학습과 평가에 동시에 들어가면
    표본이 상관돼 있어 성능이 부풀려진다."""
    splitter = GroupKFold(n_splits=min(folds, len(set(groups.tolist()))))
    scores = []
    for trainIndex, testIndex in splitter.split(matrix, labels, groups):
        trainY = labels[trainIndex]
        if len(np.unique(trainY)) < 2:
            continue
        models = buildEnsemble(matrix[trainIndex], trainY, size=ensembleSize)
        predicted = predictEnsemble(models, matrix[testIndex])
        scores.append(f1_score(labels[testIndex], predicted,
                               average=average, zero_division=0))
    if not scores:
        return float("nan"), float("nan")
    return float(np.mean(scores)), float(np.std(scores))


def sweepSubsets(matrix: np.ndarray, labels: np.ndarray, groups: np.ndarray,
                 folds: int, ensembleSize: int, average: str) -> list[dict]:
    """수집 가능한 슬롯의 부분집합 31개를 전수 평가한다."""
    results = []
    total = 2 ** len(COLLECTABLE) - 1
    for size in range(1, len(COLLECTABLE) + 1):
        for slots in itertools.combinations(COLLECTABLE, size):
            columns = columnsFor(slots)
            mean, std = crossValidatedF1(matrix[:, columns], labels, groups,
                                         folds, ensembleSize, average)
            results.append({"slots": slots, "size": size, "columns": len(columns),
                            "mean": mean, "std": std})
            print(f"  [{len(results):>2}/{total}] {'+'.join(slots):<20} "
                  f"F1 {mean:.3f}", flush=True)
    return results


def report(results: list[dict], fullMean: float, retention: float, title: str) -> dict | None:
    """크기순으로 가장 작은 '쓸 만한' 집합을 찾는다."""
    threshold = fullMean * retention
    print(f"\n━━━ {title} ━━━")
    print(f"전체 집합(f1·f2·f3·f4·f6) = {fullMean:.3f} / 보존 기준 {retention:.0%} → {threshold:.3f}\n")

    print(f"{'크기':<5}{'슬롯':<34}{'F1':>7}{'표준편차':>9}{'열':>5}")
    bySize: dict[int, list[dict]] = {}
    for row in results:
        bySize.setdefault(row["size"], []).append(row)
    for size in sorted(bySize):
        best = max(bySize[size], key=lambda r: (not np.isnan(r["mean"]), r["mean"]))
        mark = "✅" if best["mean"] >= threshold else "  "
        slots = "+".join(best["slots"])
        print(f"{size:<5}{slots:<34}{best['mean']:>7.3f}{best['std']:>9.3f}"
              f"{best['columns']:>5} {mark}  (크기 {size} 중 최고)")

    passing = [r for r in results if not np.isnan(r["mean"]) and r["mean"] >= threshold]
    if not passing:
        print(f"\n⚠️ 기준 {threshold:.3f} 를 넘는 부분집합이 없다. "
              "전체 집합이 필요하다는 뜻이거나 기준이 과하다")
        return None
    minimal = min(passing, key=lambda r: (r["size"], -r["mean"]))
    print(f"\n**최소 관측 집합 = {'+'.join(minimal['slots'])}** "
          f"({len(minimal['slots'])}종, F1 {minimal['mean']:.3f}, "
          f"전체의 {minimal['mean'] / fullMean:.1%})")
    for slot in minimal["slots"]:
        print(f"    {slot}  {SLOT_LABELS[slot]}")
    return minimal


def standardOnlyContrast(results: list[dict], fullMean: float, title: str) -> None:
    """표준 지표(f4·f6)만 쓴 집합과, 거기에 f2 를 더한 집합을 직접 대조한다.

    H5b 가 *"표준 지표로는 S1·S3 구분 불가"* 라고 했으므로 이 두 줄이 그 주장의
    정량적 형태다.
    """
    lookup = {row["slots"]: row for row in results}
    standard = lookup.get(STANDARD_ONLY)
    withPool = lookup.get(tuple(sorted(STANDARD_ONLY + ("f2",), key=SLOT_NAMES.index)))
    print(f"\n━━━ {title} ━━━")
    if standard is None or withPool is None:
        print("대조할 부분집합을 찾지 못했다")
        return
    print(f"표준 지표만      f4+f6        {standard['mean']:.3f} ± {standard['std']:.3f}")
    print(f"+ 풀 획득 대기   f2+f4+f6     {withPool['mean']:.3f} ± {withPool['std']:.3f}")
    delta = withPool["mean"] - standard["mean"]
    print(f"{'기여':<17}{'f2':<13}{delta:+.3f}  (전체 {fullMean:.3f} 대비 "
          f"{delta / fullMean:+.1%})")
    if delta <= 0.01:
        print("⚠️ **f2 의 기여가 보이지 않는다.** H5b 의 350배 차이가 이 과제에서는 "
              "구분력으로 이어지지 않는다는 뜻이므로, 결론을 뒤집기 전에 라벨 정의와 "
              "f2 의 분산을 먼저 확인해야 한다")


def confirmAtFullEnsemble(matrix: np.ndarray, labels: np.ndarray, groups: np.ndarray,
                          minimal: dict | None, arguments, title: str) -> None:
    """탐색은 N=1 로 했으니 결론 두 줄만 N=5 로 다시 잰다."""
    if minimal is None:
        return
    print(f"\n  ── {title}: N={arguments.ensembleSize} 재확인 ──")
    for name, slots in (("전체 집합", COLLECTABLE), ("최소 집합", minimal["slots"])):
        mean, std = crossValidatedF1(matrix[:, columnsFor(slots)], labels, groups,
                                     arguments.folds, arguments.ensembleSize, "macro")
        print(f"  {name:<10}{'+'.join(slots):<20}{mean:>7.3f} ± {std:.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="최소 관측 집합 전수 탐색 (H7)")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    parser.add_argument("--horizonSeconds", type=float, default=10.0)
    parser.add_argument("--precursorWindowSeconds", type=float, default=30.0)
    parser.add_argument("--latencyMultiplier", type=float, default=2.0)
    parser.add_argument("--errorRateThreshold", type=float, default=0.05)
    parser.add_argument("--precursorMultiplier", type=float, default=1.5)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--ensembleSize", type=int, default=5,
                        help="전체·최소 집합 확인에 쓰는 앙상블 크기")
    # 31개×2과제를 N=5 로 돌면 77분, N=1 이면 15분이다. 전체 집합 F1 이 N=1·N=5
    # 모두 0.888 로 같았으므로 **순위를 가리는 데에는 N=1 로 충분하다.**
    parser.add_argument("--sweepEnsembleSize", type=int, default=1,
                        help="전수 탐색에 쓰는 앙상블 크기")
    parser.add_argument("--retention", type=float, default=0.95,
                        help="전체 집합 성능의 몇 %% 를 지키면 '쓸 만하다' 고 볼 것인가")
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
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
    matrix = np.concatenate([np.stack(selfFeatures), np.stack(neighbourFeatures)], axis=1)
    labelArray = np.array(labels)
    groupArray = np.array(groups)

    values, counts = np.unique(labelArray, return_counts=True)
    print(f"에피소드 {len(episodes)}개 / 표본 {len(labelArray)}개 / {matrix.shape[1]}차원")
    print("클래스 분포: " + ", ".join(
        f"{v} {c}({c / len(labelArray):.1%})" for v, c in zip(values, counts)))
    print(f"앙상블 탐색 N={arguments.sweepEnsembleSize} / 확인 N={arguments.ensembleSize}, "
          f"에피소드 단위 GroupKFold(k={arguments.folds})")
    verifyLayout(matrix)
    print(f"\n탐색 공간: 수집 가능 슬롯 {len(COLLECTABLE)}종 → 부분집합 "
          f"{2 ** len(COLLECTABLE) - 1}개 (전수)")
    print("⚠️ f5(자원 사용률)는 미수집이라 탐색에서 제외된다 — 게이트 ② 에서 채워진다")

    fullColumns = columnsFor(COLLECTABLE)

    # ── 과제 1: 3클래스 ────────────────────────────────────────────
    fullMean, fullStd = crossValidatedF1(matrix[:, fullColumns], labelArray, groupArray,
                                         arguments.folds, arguments.ensembleSize, "macro")
    print(f"\n전체 집합 3클래스 macro F1 = {fullMean:.3f} ± {fullStd:.3f}")
    print(f"\n전수 탐색 중 (N={arguments.sweepEnsembleSize})...")
    threeClass = sweepSubsets(matrix, labelArray, groupArray,
                              arguments.folds, arguments.sweepEnsembleSize, "macro")
    minimalThree = report(threeClass, fullMean, arguments.retention,
                          "과제 1 — 3클래스 (정상/S1/S3)")
    confirmAtFullEnsemble(matrix, labelArray, groupArray, minimalThree, arguments, "3클래스")

    # ── 과제 2: S1 vs S3 이진 (H7 의 직접 답) ──────────────────────
    faultMask = np.isin(labelArray, ("S1", "S3"))
    binaryLabels = labelArray[faultMask]
    binaryGroups = groupArray[faultMask]
    binaryMatrix = matrix[faultMask]
    binaryValues, binaryCounts = np.unique(binaryLabels, return_counts=True)
    print(f"\n\nS1·S3 표본 {faultMask.sum()}개 — " + ", ".join(
        f"{v} {c}" for v, c in zip(binaryValues, binaryCounts)))
    if len(binaryValues) < 2:
        print("⚠️ S1 과 S3 가 모두 있지 않아 이진 과제가 성립하지 않는다")
        return 0

    binaryFull, binaryFullStd = crossValidatedF1(
        binaryMatrix[:, fullColumns], binaryLabels, binaryGroups,
        arguments.folds, arguments.ensembleSize, "macro")
    print(f"전체 집합 S1vsS3 macro F1 = {binaryFull:.3f} ± {binaryFullStd:.3f}")
    print(f"\n전수 탐색 중 (N={arguments.sweepEnsembleSize})...")
    binary = sweepSubsets(binaryMatrix, binaryLabels, binaryGroups,
                          arguments.folds, arguments.sweepEnsembleSize, "macro")
    minimalBinary = report(binary, binaryFull, arguments.retention,
                           "과제 2 — S1 vs S3 (H7 의 직접 답)")
    standardOnlyContrast(binary, binaryFull, "H5b 대조 — 표준 지표만으로 S1·S3 가 갈리는가")
    confirmAtFullEnsemble(binaryMatrix, binaryLabels, binaryGroups, minimalBinary,
                          arguments, "S1 vs S3")

    print("\n⚠️ 이 결과는 Docker Compose 데이터다. HPA 가 없고 f5 가 결손이다. "
          "게이트 ② 에서 재검증한다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
