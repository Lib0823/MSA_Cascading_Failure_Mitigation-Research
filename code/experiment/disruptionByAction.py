"""조치별 `Dₐ` 실측 — 장애가 없는데 개입했을 때의 비용.

왜 별도 스크립트인가
====================

[sensitivityTheta.py](sensitivityTheta.py) 의 `measureDisruption` 은 `runBatch` 의
원장을 읽고 **`injectedAt` 이전 구간**(−40~−10초)을 본다. 거기서는 제어 루프가
돌고 있어 A5 가 이미 개입하므로 그 구간이 곧 *"장애 없는데 개입"* 이 된다.

그 경로에는 두 문제가 있다.

1. **자기참조적이다.** `Dₐ` 를 A5 의 선택에서 재는데, A5 의 선택은 `θₐ` 를 거쳐
   `Dₐ` 에 의존한다. `Dₐ` 가 바뀌면 A5 가 다른 조치를 고르고, 그러면 측정 대상이
   바뀐다. 실제로 2026-10-16 실측 당시 A5 는 Bulkhead 를 골랐고 그래서
   `Dₐ = 0.703` 이 **Bulkhead 의 값**이 됐다. 지금 예측기로는 A5 가 CB 를 고른다.
2. **조치별로 분리되지 않는다.** 정책이 고르는 대로 재므로 CB·Brownout·Scale-out
   의 값을 따로 얻을 수 없다.

이 스크립트는 `runRemediation.py --scenarios normal` 이 만든 **강제 조치 셀**을
읽는다. 장애 없이 조치만 걸린 구간이므로 `Dₐ` 의 정의와 정확히 일치하고, 정책의
선택이 개입하지 않으므로 고리가 끊어진다.

구간
====

`runRemediation` 은 조치를 `injectedAt` 에 건다(시나리오가 `normal` 이면 장애는
걸지 않는다). 따라서 보는 구간은 `injectedAt` **이후**다.

```
  warmup 60s │ 정상 50s │ ─── injectedAt ─── │ 조치 적용 70s
                                              └─ 여기를 본다 (+settle ~ +end)
```

`NONE` 셀의 같은 구간이 기준선이다. **같은 구간을 비교하므로** 시간대 효과(워밍업
잔여, 컨테이너 안정화)가 상쇄된다.

⚠️ **이 값은 부하 수준에 종속된다.** 32워커에서 잰 값이며, `Dₐ` 는 처리량 손실
비율이므로 부하가 다르면 달라질 수 있다. 측정 조건을 함께 기록한다.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics

CHAIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "chain"
DEFAULT_LEDGER = CHAIN_DIR / "results" / "remediation-ledger.jsonl"
ENTRY_NODE = "gateway"


def loadCells(ledgerPath: pathlib.Path) -> list[dict]:
    """원장을 읽는다. 깨진 줄은 건너뛴다 — kill 당한 순간의 반쪽 줄일 수 있다."""
    cells = []
    if not ledgerPath.exists():
        return cells
    for line in ledgerPath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            cells.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return cells


def windowValues(cell: dict, node: str, low: float, high: float, key: str) -> list[float]:
    """`injectedAt` 기준 상대 구간의 값들."""
    tracePath = CHAIN_DIR / cell["tracePath"]
    if not tracePath.exists():
        return []
    injected = cell["injectedAt"]
    if injected is None:
        return []
    values = []
    for line in tracePath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("node") != node:
            continue
        offset = row["timestamp"] - injected
        if not (low <= offset < high):
            continue
        value = row.get(key)
        if isinstance(value, (int, float)) and not math.isnan(value):
            values.append(float(value))
    return values


def measure(cells: list[dict], settleSeconds: float, endSeconds: float) -> dict:
    """조치별 처리량 중앙값과 `NONE` 대비 손실."""
    byAction: dict[str, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        if cell.get("scenario") != "normal":
            continue   # 장애가 섞인 셀은 Dₐ 가 아니다
        byAction[cell["remediation"]].append(cell)

    measured = {}
    for action, actionCells in byAction.items():
        throughput, latency = [], []
        for cell in actionCells:
            values = windowValues(cell, ENTRY_NODE, settleSeconds, endSeconds, "throughputRps")
            if values:
                throughput.append(statistics.median(values))
            p95 = windowValues(cell, ENTRY_NODE, settleSeconds, endSeconds, "latencyP95")
            if p95:
                latency.append(statistics.median(p95))
        if throughput:
            measured[action] = {
                "cells": len(actionCells),
                "throughput": statistics.median(throughput),
                "latencyP95": statistics.median(latency) if latency else float("nan"),
            }
    if "NONE" not in measured:
        return {}
    baseline = measured["NONE"]["throughput"]
    for action, row in measured.items():
        row["disruption"] = max(0.0, 1.0 - row["throughput"] / baseline)
    return measured


def thresholdFor(disruption: float, mitigation: float, reversibility: float,
                 availabilityLoss: float) -> float:
    """`θₐ = Dₐ·Rₐ / (mₐ·L − Dₐ(1−Rₐ))`. 분모 ≤ 0 이면 조치가 자동 배제된다."""
    denominator = mitigation * availabilityLoss - disruption * (1.0 - reversibility)
    if denominator <= 0:
        return float("inf")
    return disruption * reversibility / denominator


def main() -> int:
    parser = argparse.ArgumentParser(description="조치별 Dₐ 실측")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--settleSeconds", type=float, default=10.0,
                        help="조치 적용 직후 과도 구간을 뺀다")
    parser.add_argument("--endSeconds", type=float, default=70.0)
    parser.add_argument("--reversibility", type=float, default=0.1,
                        help="Rₐ — 정책 파라미터다. 실측 대상이 아니다(§4-5)")
    parser.add_argument("--availabilityLoss", type=float, default=1.0, help="L")
    arguments = parser.parse_args()

    cells = loadCells(pathlib.Path(arguments.ledger))
    normalCells = [c for c in cells if c.get("scenario") == "normal"]
    print(f"원장 {len(cells)} 셀 / 그중 normal {len(normalCells)} 셀")
    if not normalCells:
        print("❌ `normal` 셀이 없다. 먼저 돌려야 한다:")
        print("   .venv/bin/python code/chain/runRemediation.py --scenarios normal \\")
        print("     --remediations NONE,BULKHEAD,CIRCUIT_BREAKER,BROWNOUT --resume")
        return 1

    measured = measure(cells, arguments.settleSeconds, arguments.endSeconds)
    if not measured:
        print("❌ `NONE` 기준선 셀이 없다 — 손실을 계산할 수 없다")
        return 1

    print(f"구간: injectedAt +{arguments.settleSeconds:.0f}s ~ +{arguments.endSeconds:.0f}s "
          f"({ENTRY_NODE} 노드) / 장애 주입 없음\n")
    print(f"{'조치':<18}{'셀':>4}{'처리량 rps':>12}{'p95 ms':>10}{'Dₐ':>9}")
    for action in sorted(measured, key=lambda a: (a != "NONE", a)):
        row = measured[action]
        mark = "  ← 기준선" if action == "NONE" else ""
        print(f"{action:<18}{row['cells']:>4}{row['throughput']:>12.1f}"
              f"{row['latencyP95']:>10.0f}{row['disruption']:>9.3f}{mark}")

    # 가정값과의 대조 — 엔진이 지금 무엇을 쓰고 있는지 함께 보여야 판단이 된다.
    assumed = {"CIRCUIT_BREAKER": 0.15, "BULKHEAD": 0.25, "BROWNOUT": 0.30}
    mitigationS3 = {"CIRCUIT_BREAKER": 0.8, "BULKHEAD": 0.2, "BROWNOUT": 0.2}
    mitigationS1 = {"CIRCUIT_BREAKER": -0.3, "BULKHEAD": 0.8, "BROWNOUT": 0.1}
    print(f"\n{'조치':<18}{'가정 D':>8}{'실측 D':>8}{'θ(S3) 가정':>12}{'θ(S3) 실측':>12}"
          f"{'θ(S1) 가정':>12}{'θ(S1) 실측':>12}")
    for action in sorted(assumed):
        if action not in measured:
            continue
        real = measured[action]["disruption"]
        cells_ = []
        for table in (mitigationS3, mitigationS1):
            for value in (assumed[action], real):
                theta = thresholdFor(value, table[action], arguments.reversibility,
                                     arguments.availabilityLoss)
                cells_.append("∞" if theta == float("inf") else f"{theta:.3f}")
        print(f"{action:<18}{assumed[action]:>8.2f}{real:>8.3f}"
              f"{cells_[0]:>12}{cells_[1]:>12}{cells_[2]:>12}{cells_[3]:>12}")

    print(f"\n⚠️ `Rₐ`={arguments.reversibility} 는 **정책 파라미터**이며 실측 대상이 "
          "아니다(§4-5). 민감도로 보고한다")
    print("⚠️ 이 값은 **부하 수준에 종속된다** — 32워커 기준이다. 처리량 손실 비율이므로 "
          "부하가 다르면 달라질 수 있다")
    print("⚠️ Scale-out 은 K8s 가 필요하므로 여기서 재지 못한다 — 게이트 ② 로 넘긴다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
