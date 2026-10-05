"""S1↔S3 증상 동형성 판정 (H5b) + 최소 관측 집합 예비 확인 (H7).

연구가 주장하는 것은 두 개이고, 둘은 같은 데이터에서 **동시에** 지지되어야 한다.

**H5b (증상 동형)** — S1 과 S3 는 표준 지표로 구분되지 않는다. 구분된다면
*"진단이 원리적으로 어려운 구간"* 이라는 전제가 깨지고, 신뢰도 기반 조치 선택이라는
기여 자체가 약해진다. 즉 **동형성은 좋은 소식이다.**

**H7 (최소 관측 집합)** — 그러나 어떤 지표를 추가하면 구분된다. 구분되지 않으면
*"측정했다"* 에서 멈추고 실무 처방이 나오지 않는다.

이 둘이 함께 성립해야 §4-4 의 서술 — *"표준 지표로는 못 가르고, 풀 획득 대기시간과
하류 자체 처리시간을 보면 갈린다"* — 이 실측으로 뒷받침된다.

**두 지표군을 서로 다른 노드에서 본다.** 이것이 분석의 핵심이자, 처음에 틀렸던 지점이다.

- **표준 지표는 관측 노드(orders)** 에서 본다. 진단이 어려운 쪽은 증상을 **관측하는**
  쪽이기 때문이다.
- **추가 지표는 이웃 노드(payment)** 에서 본다. 커넥션 풀은 DB 를 쓰는 노드에만 있고,
  passthrough 인 orders 에는 **아예 존재하지 않는다**(전부 NaN 이었다).

이 비대칭이 우연이 아니다. **구분자가 관측 노드에 없고 이웃에 있다**는 사실은
§2-A 의 이웃 집계 feature 가 왜 필요한지에 대한 직접적 근거다 — 자기 지표만 보면
S1 과 S3 가 같아 보이고, 이웃의 내부 지표를 끌어와야 갈린다.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics

# 표준 지표 — 어느 운영 환경에나 있다. 여기서 갈리면 H5b 가 깨진다.
STANDARD_METRICS = ("latencyP95", "errorRate", "throughputRps")
# 추가 지표 — §4-4 가 최소 관측 집합 후보로 지목한 것들.
ADDITIONAL_METRICS = ("poolAcquireMillis", "poolUsageMillis", "poolPending")

# 두 분포가 "구분된다"고 볼 최소 중앙값 비율 차이.
SEPARATION_RATIO = 1.5


def loadFaultWindow(tracePath: pathlib.Path, injectedAt: float, node: str,
                    settleSeconds: float) -> dict[str, list[float]]:
    """주입 이후 구간의 노드별 지표를 모은다.

    주입 직후 ``settleSeconds`` 는 버린다 — 커넥션 점유든 필터 지연이든 효과가
    전 구간에 퍼지는 데 시간이 걸리고, 그 과도 구간을 섞으면 두 시나리오의 차이가
    과도 응답 차이에 묻힌다.
    """
    collected: dict[str, list[float]] = {name: [] for name in STANDARD_METRICS + ADDITIONAL_METRICS}
    for line in tracePath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["node"] != node:
            continue
        if record["timestamp"] < injectedAt + settleSeconds:
            continue
        for name in collected:
            value = record.get(name, float("nan"))
            if isinstance(value, (int, float)) and not math.isnan(value):
                collected[name].append(float(value))
    return collected


def median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def separationRatio(left: float, right: float) -> float:
    """두 중앙값의 비율. 1.0 이면 완전히 같고, 크면 갈린다."""
    if math.isnan(left) or math.isnan(right):
        return float("nan")
    low, high = sorted((abs(left), abs(right)))
    if low <= 1e-9:
        return float("inf") if high > 1e-9 else 1.0
    return high / low


def main() -> int:
    parser = argparse.ArgumentParser(description="S1↔S3 증상 동형성 판정")
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--node", default="orders",
                        help="표준 지표를 관측하는 노드. 기본은 주입 지점의 직접 호출자")
    parser.add_argument("--neighborNode", default="payment",
                        help="추가 지표를 보는 이웃 노드. 커넥션 풀은 여기에만 있다")
    parser.add_argument("--settleSeconds", type=float, default=15.0)
    arguments = parser.parse_args()

    episodesPath = pathlib.Path(arguments.episodes).resolve()
    episodes = json.loads(episodesPath.read_text(encoding="utf-8"))
    here = episodesPath.parent.parent

    windows, neighborWindows = {}, {}
    for episode in episodes:
        windows[episode["scenario"]] = loadFaultWindow(
            here / episode["tracePath"], episode["injectedAt"],
            arguments.node, arguments.settleSeconds)
        neighborWindows[episode["scenario"]] = loadFaultWindow(
            here / episode["tracePath"], episode["injectedAt"],
            arguments.neighborNode, arguments.settleSeconds)

    missing = [name for name in ("s1", "s3") if name not in windows]
    if missing:
        print(f"시나리오 누락: {missing} — normal,s1,s3 를 모두 돌려야 한다")
        return 1

    print(f"표준 지표 관측 노드: {arguments.node} (주입 지점의 직접 호출자)")
    print(f"추가 지표 관측 노드: {arguments.neighborNode} (커넥션 풀이 있는 이웃)")
    print(f"주입 후 {arguments.settleSeconds:.0f}초는 과도 구간으로 버림\n")

    header = f"{'지표':<20}{'정상':>12}{'S1':>12}{'S3':>12}{'S1/S3 비율':>14}  판정"
    print("━━━ 표준 지표 — 여기서 갈리면 H5b 가 깨진다 ━━━")
    print(header)
    standardSeparated = []
    for name in STANDARD_METRICS:
        normalValue = median(windows.get("normal", {}).get(name, []))
        s1Value = median(windows["s1"][name])
        s3Value = median(windows["s3"][name])
        ratio = separationRatio(s1Value, s3Value)
        separated = not math.isnan(ratio) and ratio >= SEPARATION_RATIO
        standardSeparated.append((name, separated, ratio))
        print(f"{name:<20}{normalValue:>12.2f}{s1Value:>12.2f}{s3Value:>12.2f}"
              f"{ratio:>14.2f}  {'⚠️ 갈림' if separated else '동형'}")

    print(f"\n━━━ 추가 지표 ({arguments.neighborNode}) — 여기서 갈려야 H7 이 성립한다 ━━━")
    print(header)
    additionalSeparated = []
    for name in ADDITIONAL_METRICS:
        normalValue = median(neighborWindows.get("normal", {}).get(name, []))
        s1Value = median(neighborWindows["s1"][name])
        s3Value = median(neighborWindows["s3"][name])
        ratio = separationRatio(s1Value, s3Value)
        separated = not math.isnan(ratio) and ratio >= SEPARATION_RATIO
        additionalSeparated.append((name, separated, ratio))
        print(f"{name:<20}{normalValue:>12.2f}{s1Value:>12.2f}{s3Value:>12.2f}"
              f"{ratio:>14.2f}  {'✅ 갈림' if separated else '못 가름'}")

    homomorphic = not any(flag for _, flag, _ in standardSeparated)
    discriminators = [name for name, flag, _ in additionalSeparated if flag]

    print("\n━━━ 판정 ━━━")
    if homomorphic and discriminators:
        verdict = "h5b-and-h7-supported"
        print("✅ H5b·H7 동시 지지")
        print("   표준 지표로는 S1 과 S3 가 구분되지 않는데, 추가 지표로는 구분된다.")
        print(f"   구분자: {', '.join(discriminators)} (모두 {arguments.neighborNode} 의 지표)")
        print("   → §4-4 의 '최소 추가 관측 집합' 서술이 실측으로 뒷받침된다.")
        print(f"   ⚠️ 구분자가 관측 노드({arguments.node})에 없고 이웃({arguments.neighborNode})에")
        print("      있다는 점이 중요하다. 자기 지표만으로는 갈리지 않으므로, 이것이")
        print("      §2-A 이웃 집계 feature 의 직접적 근거가 된다.")
    elif homomorphic:
        verdict = "h5b-only"
        print("⚠️ H5b 는 지지되나 H7 은 아니다 — 추가 지표로도 구분되지 않는다.")
        print("   구분 가능한 지표를 더 찾거나, 못 찾으면 H7(25h)의 산출이 '측정했다'에서 멈춘다.")
    else:
        separatedNames = [name for name, flag, _ in standardSeparated if flag]
        verdict = "h5b-violated"
        print(f"❌ H5b 미지지 — 표준 지표 {separatedNames} 가 이미 S1 과 S3 를 가른다.")
        print("   '진단이 원리적으로 어려운 구간'이라는 전제가 이 구성에서는 성립하지 않는다.")
        print("   주입 강도를 맞춰 두 시나리오의 표준 지표 수준을 같게 만든 뒤 다시 본다 —")
        print("   동형성은 자연히 성립하는 성질이 아니라 **설계로 맞춰야 하는 조건**이다.")

    payload = {
        "verdict": verdict, "observerNode": arguments.node,
        "standard": {name: {"separated": flag, "ratio": ratio}
                     for name, flag, ratio in standardSeparated},
        "additional": {name: {"separated": flag, "ratio": ratio}
                       for name, flag, ratio in additionalSeparated},
        "neighborNode": arguments.neighborNode,
        "medians": {scenario: {name: median(values) for name, values in window.items()}
                    for scenario, window in windows.items()},
        "neighborMedians": {scenario: {name: median(values) for name, values in window.items()}
                            for scenario, window in neighborWindows.items()},
    }
    outputPath = episodesPath.with_name(episodesPath.name.replace("episodes-", "symptoms-"))
    outputPath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
