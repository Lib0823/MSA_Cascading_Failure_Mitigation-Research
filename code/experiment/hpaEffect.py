"""A1(HPA) 의 비용·효과 — HPA 를 건 셀과 안 건 셀의 대조 (J32).

SCALE_OUT 은 주입 시점에 거는 조치라 같은 코호트 안에서 NONE 과 비교한다
([scaleOutEffect.py](scaleOutEffect.py)). HPA 는 **상시 정책**이라 셀 시작부터 켜져 있다.
그래서 **코호트를 가른다** — 같은 시나리오·같은 조치(NONE)에서 HPA 유무만 다르다.

=======  ==========================================  =====================
값        대조                                         구간·지표
=======  ==========================================  =====================
비용      normal × NONE: hpa1 vs 기준                   Dₐ 구간 처리량
효과      S1 × NONE: hpa1 vs 기준                       mₐ 구간 goodput
=======  ==========================================  =====================

HPA 가 정상 구간에서 이미 증설했다면 그 비용은 주입 **전부터** 생긴다. 구간은
scaleOutEffect 와 같게 두어(주입 기준 +10·+15 ~ +70초) 두 조치의 값을 나란히 놓을 수
있게 한다. 레플리카 시간선(2초)으로 HPA 가 **언제·몇 개로** 늘렸는지 함께 본다.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics

from scaleOutEffect import (BASE_LABEL, DISRUPTION_WINDOW, MITIGATION_WINDOW, POD_WINDOW,
                            RESULTS_DIR, cellId, cohortLabel, describe, entryWindow,
                            podConnections, podRates, relativeEffect)


def metric(cell: dict) -> float:
    """normal 은 Dₐ 구간 처리량, 장애 시나리오는 mₐ 구간 goodput — scaleOutEffect 와 같다."""
    if cell["scenario"] == "normal":
        return entryWindow(cell, *DISRUPTION_WINDOW)["throughputRps"]
    return entryWindow(cell, *MITIGATION_WINDOW)["goodputRps"]


def replicaSummary(cell: dict) -> dict | None:
    """HPA 가 언제·몇 개로 늘렸는가. 시각은 주입 기준(음수 = 주입 전)."""
    hpa = cell.get("hpa") or {}
    timeline = hpa.get("timeline") or []
    if not timeline:
        return None
    injected = cell["injectedAt"]
    firstScale = next((row["timestamp"] - injected for row in timeline if row["spec"] > 1), None)
    atInjection = min(timeline, key=lambda row: abs(row["timestamp"] - injected))
    return {
        "firstScaleUpSeconds": firstScale,
        "specAtInjection": atInjection["spec"], "readyAtInjection": atInjection["ready"],
        "maxSpec": max(row["spec"] for row in timeline),
        "readyAtEnd": timeline[-1]["ready"],
        "final": hpa.get("final"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A1(HPA) 비용·효과 (K8s)")
    parser.add_argument("--cells", required=True, action="append",
                        help="k8s-remediation-*.json (여러 번 줄 수 있다)")
    parser.add_argument("--treatment", default="hpa1", help="HPA 를 건 코호트의 이름")
    parser.add_argument("--control", default=BASE_LABEL, help="대조 코호트의 이름")
    parser.add_argument("--output", help="요약 JSON. 기본은 results/hpaEffect-<배치 스탬프>.json")
    arguments = parser.parse_args()

    cells = []
    for path in arguments.cells:
        cells.extend(c for c in json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
                     if c.get("backend") == "k8s" and c.get("remediation") == "NONE")
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        groups[(cohortLabel(cell), cell["scenario"])].append(cell)

    report: dict = {"effects": {}, "conditions": {}, "cells": {}}
    print(f"{'조건':<20}{'n':>3}{'평균':>9}{'sd':>7}{'최소':>8}{'최대':>8}")
    for (label, scenario), group in sorted(groups.items()):
        row = describe([metric(c) for c in group])
        report["conditions"][f"{label}|{scenario}"] = row
        print(f"{label + '|' + scenario:<20}{row['n']:>3}{row['mean']:>9.1f}{row['sd']:>7.1f}"
              f"{row['min']:>8.1f}{row['max']:>8.1f}")

    print(f"\n━━━ {arguments.treatment} vs {arguments.control} (NONE, 평균 기준, Welch) ━━━")
    for scenario in sorted({s for _, s in groups}):
        control = [metric(c) for c in groups.get((arguments.control, scenario), [])]
        treatment = [metric(c) for c in groups.get((arguments.treatment, scenario), [])]
        if len(control) < 2 or len(treatment) < 2:
            continue
        effect = relativeEffect(treatment, control)
        report["effects"][scenario] = effect
        low, high = effect["relativeCi95"]
        label = "비용 (Dₐ 에 해당)" if scenario == "normal" else "효과 (mₐ 에 해당)"
        print(f"{scenario:<8}{label}: {effect['relative']:+.1%} [{low:+.1%}, {high:+.1%}]"
              f"  Welch p={effect['welchP']:.2g} · 정확 MW p={effect['mannWhitneyExactP']:.3f}"
              f"  {'(±10% 동등)' if effect['equivalent'] else ''}")

    print("\n━━━ HPA 셀 — 레플리카 시간선과 파드별 처리율 (+20~+70초) ━━━")
    for (label, scenario), group in sorted(groups.items()):
        if label != arguments.treatment:
            continue
        for cell in sorted(group, key=lambda c: c.get("repeat") or 0):
            summary = replicaSummary(cell)
            rates = podRates(cell, *POD_WINDOW)
            connections = podConnections(cell, *POD_WINDOW)
            report["cells"][cellId(cell)] = {"scenario": scenario, "metric": metric(cell),
                                             "replicas": summary, "podRates": rates,
                                             "podConnections": connections}
            if summary is None:
                print(f"{scenario} r{cell.get('repeat')}: 레플리카 시간선 없음")
                continue
            first = summary["firstScaleUpSeconds"]
            pods = " / ".join(f"{rate:5.1f}" + (f"(DB {connections[pod]:.0f})" if pod in connections else "")
                              for pod, rate in sorted(rates.items()))
            final = summary.get("final") or {}
            print(f"{scenario} r{cell.get('repeat')}: 첫 증설 "
                  f"{'없음' if first is None else f'{first:+.0f}초'} · 주입 시 {summary['specAtInjection']}개"
                  f" · 최대 {summary['maxSpec']}개 · CPU {final.get('cpuUtilizationPercent')}%"
                  f" | {pods}" + (f" | 점유 {connections['local']:.0f}" if "local" in connections else ""))

    output = pathlib.Path(arguments.output) if arguments.output else None
    if output is None:
        stamp = pathlib.Path(arguments.cells[0]).stem.removeprefix("k8s-remediation-")
        output = RESULTS_DIR / f"hpaEffect-{stamp}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
