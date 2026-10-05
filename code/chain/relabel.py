"""자동 역라벨링 + 전파성 판정 (§4-2).

기록된 **미래** 지표로부터 각 (스냅샷, 노드)의 라벨을 프로그램으로 역산한다.
수작업 주석이 없으므로 재현 가능하다.

§4-2 가 확정한 규칙을 그대로 옮긴다.

.. code-block:: text

    positive(v, t) ⟺ v 가 [t, t+Δ] 구간에 SLO 위반
                     ∧ ∃ u ∈ 1-hop 상류 : u 가 [t−W, t] 구간에 선행 열화

    선행 열화   = u 의 p95 지연 또는 에러율이 정상 구간 기준선 대비 임계 초과
    허용 시간창 = 상류 열화 시점 이후 30초 이내
    hop 범위    = 1-hop 직접 상류

**두 가지를 틀리기 쉬우므로 명시한다.**

1. **상류의 방향.** 호출 간선은 호출자→피호출자로 저장하지만 전파는 그 반대로 흐른다.
   따라서 v 의 1-hop 상류는 **v 가 호출하는 쪽**이다 — orders 의 상류는 payment 다.
   방향을 뒤집으면 라벨이 통째로 뒤집힌다(``code/model/smokeTest.py`` C7 이 실증).
2. **1차 장애는 positive 가 아니다.** 주입 지점(payment)은 외생적으로 열화하므로
   상류 선행 열화가 없고, 규칙상 자동으로 제외된다. 이 제외가 실제로 일어나는지가
   규칙이 작동한다는 가장 직접적인 증거다.

hop 범위가 feature 의 2-hop 과 다른 이유도 §4-2 에 있다 — feature 는 정보를 넓게
모을수록 유리하지만 **라벨 판정은 좁고 엄격해야 한다.** 넓히면 우연한 동시 열화를
전파로 오인해 라벨이 오염된다.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
from dataclasses import dataclass, field

# §4-2 가 "실측으로 조정할 값"으로 남긴 파라미터들. 기본값은 이 사슬 기준이다.
DEFAULT_HORIZON_SECONDS = 10.0          # Δ — 예측 지평
DEFAULT_PRECURSOR_WINDOW_SECONDS = 30.0  # W — 상류 선행 열화를 찾는 창
DEFAULT_LATENCY_MULTIPLIER = 2.0         # SLO 위반: 기준선의 배수
DEFAULT_ERROR_RATE_THRESHOLD = 0.05
DEFAULT_PRECURSOR_MULTIPLIER = 1.5       # 선행 열화: SLO 위반보다 느슨해야 한다

NORMAL = "정상"


@dataclass
class NodeSeries:
    """한 노드의 시계열."""

    name: str
    timestamps: list[float] = field(default_factory=list)
    latencyP95: list[float] = field(default_factory=list)
    errorRate: list[float] = field(default_factory=list)
    poolAcquireMillis: list[float] = field(default_factory=list)
    poolUsageMillis: list[float] = field(default_factory=list)

    def baselineLatency(self, start: float, until: float) -> float:
        """기준선은 **정상 구간에서만** 잡는다.

        주입 이전 전체를 쓰면 JVM 워밍업 구간의 지연 급등이 섞여 기준선이 끌려
        올라가고, 그러면 진짜 위반이 위반으로 보이지 않는다. 반대로 워밍업 구간을
        평가 대상에 남겨 두면 그 급등 자체가 위반으로 집힌다.
        """
        values = [v for t, v in zip(self.timestamps, self.latencyP95)
                  if start <= t < until and not math.isnan(v)]
        return statistics.median(values) if values else float("nan")

    def valueAt(self, series: list[float], timestamp: float) -> float:
        """가장 가까운 표본. 1Hz 수집이므로 보간하지 않는다."""
        best, bestGap = float("nan"), float("inf")
        for t, v in zip(self.timestamps, series):
            gap = abs(t - timestamp)
            if gap < bestGap:
                best, bestGap = v, gap
        return best

    def anyInWindow(self, series: list[float], start: float, end: float, threshold: float) -> bool:
        return any(start <= t <= end and not math.isnan(v) and v > threshold
                   for t, v in zip(self.timestamps, series))


def loadTrace(path: pathlib.Path) -> dict[str, NodeSeries]:
    series: dict[str, NodeSeries] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        node = record["node"]
        target = series.setdefault(node, NodeSeries(name=node))
        target.timestamps.append(record["timestamp"])
        target.latencyP95.append(record.get("latencyP95", float("nan")))
        target.errorRate.append(record.get("errorRate", float("nan")))
        target.poolAcquireMillis.append(record.get("poolAcquireMillis", float("nan")))
        target.poolUsageMillis.append(record.get("poolUsageMillis", float("nan")))
    return series


def upstreamOf(node: str, callEdges: list[list[str]]) -> list[str]:
    """v 의 1-hop 상류 = v 가 호출하는 쪽.

    호출 간선은 (호출자, 피호출자)로 저장된다. 전파는 피호출자 → 호출자로 흐르므로
    전파 관점의 '상류'는 피호출자다.
    """
    return [callee for caller, callee in callEdges if caller == node]


def labelEpisode(episode: dict, tracePath: pathlib.Path, arguments: argparse.Namespace) -> dict:
    series = loadTrace(tracePath)
    callEdges = episode["callGraph"]["callEdges"]
    injectedAt = episode["injectedAt"]
    scenario = episode["scenario"]
    injectionNode = episode["injectionNode"]

    # 기준선·평가 모두 정상 구간부터다. 워밍업은 양쪽에서 뺀다.
    normalStart = injectedAt - float(episode.get("normalSeconds", 40.0))
    baselines = {name: node.baselineLatency(normalStart, injectedAt)
                 for name, node in series.items()}

    labels = []
    for name, node in series.items():
        baseline = baselines.get(name, float("nan"))
        if math.isnan(baseline) or baseline <= 0:
            continue
        violationThreshold = baseline * arguments.latencyMultiplier
        upstreams = upstreamOf(name, callEdges)

        for timestamp in node.timestamps:
            if timestamp < normalStart:
                continue  # 워밍업 구간은 라벨 대상이 아니다
            # (1) v 가 [t, t+Δ] 안에 SLO 위반에 빠지는가
            violates = node.anyInWindow(
                node.latencyP95, timestamp, timestamp + arguments.horizonSeconds,
                violationThreshold)
            violates = violates or node.anyInWindow(
                node.errorRate, timestamp, timestamp + arguments.horizonSeconds,
                arguments.errorRateThreshold)

            # (2) 1-hop 상류에 [t−W, t] 선행 열화가 있는가
            precursorNode = None
            for upstreamName in upstreams:
                upstream = series.get(upstreamName)
                upstreamBaseline = baselines.get(upstreamName, float("nan"))
                if upstream is None or math.isnan(upstreamBaseline) or upstreamBaseline <= 0:
                    continue
                degraded = upstream.anyInWindow(
                    upstream.latencyP95, timestamp - arguments.precursorWindowSeconds, timestamp,
                    upstreamBaseline * arguments.precursorMultiplier)
                degraded = degraded or upstream.anyInWindow(
                    upstream.errorRate, timestamp - arguments.precursorWindowSeconds, timestamp,
                    arguments.errorRateThreshold)
                if degraded:
                    precursorNode = upstreamName
                    break

            # ⚠️ 규칙은 **모든 시나리오에 동일하게** 적용한다. 정상 시나리오에서만
            # 라벨을 정상으로 못박으면 대조군이 대조군 역할을 못 한다 — 규칙이 주입
            # 없는 계에서 몇 건을 잘못 집는지가 바로 대조군이 답해야 할 질문이다.
            isPropagated = violates and precursorNode is not None
            # 병목 유형은 주입 로그가 그대로 알려준다 — §4-2 의 "추가 비용은 사실상 없다".
            # 다만 정상 시나리오에서 전파로 판정되면 그것은 유형이 아니라 **오탐**이다.
            labels.append({
                "node": name,
                "timestamp": timestamp,
                "secondsFromInjection": timestamp - injectedAt,
                "label": (scenario.upper() if isPropagated else NORMAL),
                "isPropagated": isPropagated,
                "isFalsePositive": isPropagated and scenario == "normal",
                "violates": violates,
                "precursor": precursorNode,
                "isInjectionPoint": name == injectionNode,
            })

    return {"scenario": scenario, "baselines": baselines, "labels": labels}


def summarize(result: dict) -> dict:
    labels = result["labels"]
    byNode: dict[str, dict] = {}
    for entry in labels:
        row = byNode.setdefault(entry["node"], {
            "total": 0, "violating": 0, "positive": 0,
            "isInjectionPoint": entry["isInjectionPoint"]})
        row["total"] += 1
        row["violating"] += int(entry["violates"])
        row["positive"] += int(entry["isPropagated"])
    return byNode


def main() -> int:
    parser = argparse.ArgumentParser(description="자동 역라벨링 + 전파성 판정 (§4-2)")
    parser.add_argument("--episodes", required=True, help="runChain.py 가 남긴 episodes-*.json")
    parser.add_argument("--horizonSeconds", type=float, default=DEFAULT_HORIZON_SECONDS)
    parser.add_argument("--precursorWindowSeconds", type=float,
                        default=DEFAULT_PRECURSOR_WINDOW_SECONDS)
    parser.add_argument("--latencyMultiplier", type=float, default=DEFAULT_LATENCY_MULTIPLIER)
    parser.add_argument("--errorRateThreshold", type=float, default=DEFAULT_ERROR_RATE_THRESHOLD)
    parser.add_argument("--precursorMultiplier", type=float, default=DEFAULT_PRECURSOR_MULTIPLIER)
    arguments = parser.parse_args()

    episodesPath = pathlib.Path(arguments.episodes).resolve()
    episodes = json.loads(episodesPath.read_text(encoding="utf-8"))
    here = episodesPath.parent.parent

    allResults = []
    print(f"Δ={arguments.horizonSeconds}s  W={arguments.precursorWindowSeconds}s  "
          f"위반={arguments.latencyMultiplier}×기준선  선행열화={arguments.precursorMultiplier}×기준선\n")

    for episode in episodes:
        tracePath = here / episode["tracePath"]
        result = labelEpisode(episode, tracePath, arguments)
        allResults.append(result)
        byNode = summarize(result)

        isControl = episode["scenario"] == "normal"
        print(f"━━━ {episode['scenario']}{' (대조군 — 여기의 positive 는 전부 오탐이다)' if isControl else ''} ━━━")
        print(f"{'노드':<10}{'표본':>6}{'위반':>8}{'전파 positive':>16}{'비율':>8}  비고")
        for name, row in byNode.items():
            note = "← 1차 장애 주입 지점" if row["isInjectionPoint"] else ""
            if isControl and row["positive"] > 0:
                note = ("⚠️ 오탐 " + note) if not row["isInjectionPoint"] else note
            share = row["positive"] / row["total"] if row["total"] else 0.0
            print(f"{name:<10}{row['total']:>6}{row['violating']:>8}{row['positive']:>16}"
                  f"{share:>8.0%}  {note}")
        print()

    controlPositives = sum(
        1 for result in allResults if result["scenario"] == "normal"
        for entry in result["labels"] if entry["isPropagated"])
    controlTotal = sum(
        len(result["labels"]) for result in allResults if result["scenario"] == "normal")
    injectionPositives = sum(
        1 for result in allResults if result["scenario"] != "normal"
        for entry in result["labels"] if entry["isPropagated"] and entry["isInjectionPoint"])

    print("━━━ 규칙 판정 ━━━")
    if controlTotal:
        falsePositiveRate = controlPositives / controlTotal
        print(f"대조군 오탐율 {falsePositiveRate:.1%} ({controlPositives}/{controlTotal})")
        if falsePositiveRate > 0.05:
            print("  ❌ 주입이 없는 계에서도 전파 positive 가 쏟아진다. 이 임계값으로")
            print("     라벨을 만들면 학습 데이터의 상당 부분이 틀린 positive 가 된다.")
            print("     Δ·W·배수를 조이거나 지표 안정성을 먼저 확보해야 한다.")
        else:
            print("  ✅ 대조군이 깨끗하다. 주입 없는 계에서 전파를 거의 집지 않는다.")
    print(f"1차 장애 지점의 positive {injectionPositives} 건")
    if injectionPositives == 0:
        print("  ✅ 외생적 1차 장애가 규칙상 자동 제외된다 (상류 선행 열화 없음).")
    else:
        print("  ❌ 주입 지점이 전파로 집혔다. 상류 방향이 뒤집혔거나 임계가 느슨하다.")
    print()

    outputPath = episodesPath.with_name(episodesPath.name.replace("episodes-", "labels-"))
    outputPath.write_text(json.dumps(allResults, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
