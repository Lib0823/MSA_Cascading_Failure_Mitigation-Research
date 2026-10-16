"""A축 정책 비교 — 정상 구간과 장애 구간을 나눠서 본다.

**Compose 에서 측정 가능한 축과 불가능한 축이 다르다.**

=====================  ==========================================  ==========
축                      측정되는가                                   왜
=====================  ==========================================  ==========
완화효과 `mₐ`           ❌ HPA 가 용량을 바꿔야 한다. 폐루프에서     게이트 ②
                       병목 용량이 외생적으로 고정되면 앱 측
                       조치는 대기 위치를 옮길 뿐이다
**오탐 비용 (H5a)**     ✅ **정상 구간에 개입했는가**                지금 가능
=====================  ==========================================  ==========

**A5 와 A6 의 차이가 바로 오탐에서 나온다.** A6 은 `p_eff = p̄` 로 환원되어
보류가 0% 다 — 장애가 없는 구간에서도 조치를 내보낸다. A5 는 불확실성을
할인해 참는다. 그 차이가 H5a 이며 **컷 불가 항목**이다.

H5a 의 정의가 그렇다 (§0-1)
============================

    선제 대응 특유의 제약 (a) 장애 미발생 시 개입 비용
    — 예측이 빗나가면 **아무 문제가 없던 시스템에 조치를 가한 순손실**이 남는다

따라서 **주입 이전 구간**의 조치 횟수가 직접 재료다. 장애 구간의 조치는
오탐이 아니므로 섞으면 안 된다.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

HOLD = "보류"
PASSIVE = {"A1", "A4"}


def loadLedger(path: pathlib.Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [e for e in entries if e["status"] == "done"]


def splitDecisions(cell: dict, settleSeconds: float) -> tuple[list[dict], list[dict]]:
    """결정을 정상 구간과 장애 구간으로 나눈다.

    주입 직후 ``settleSeconds`` 는 양쪽에서 버린다 — 조치 효과가 퍼지는 과도
    구간이고, 그걸 섞으면 오탐과 정당한 조치가 구분되지 않는다.
    """
    if not cell.get("decisionPath"):
        return [], []
    path = HERE / cell["decisionPath"]
    if not path.exists():
        return [], []
    injectedAt = cell["injectedAt"]
    normal, fault = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        offset = entry["timestamp"] - injectedAt
        if offset < -settleSeconds:
            normal.append(entry)
        elif offset > settleSeconds:
            fault.append(entry)
    return normal, fault


def actionShare(decisions: list[dict]) -> float:
    """조치가 나간 비율. 보류는 조치가 아니다."""
    if not decisions:
        return float("nan")
    acted = sum(1 for d in decisions if d["action"] != HOLD)
    return acted / len(decisions)


def main() -> int:
    parser = argparse.ArgumentParser(description="A축 정책 비교 — 오탐 비용 중심")
    parser.add_argument("--ledger", default=str(RESULTS_DIR / "ledger.jsonl"))
    parser.add_argument("--settleSeconds", type=float, default=10.0)
    arguments = parser.parse_args()

    ledgerPath = pathlib.Path(arguments.ledger)
    if not ledgerPath.exists():
        print(f"원장이 없다: {ledgerPath}")
        return 1
    cells = loadLedger(ledgerPath)
    if not cells:
        print("완료된 셀이 없다.")
        return 1

    byPolicy: dict[str, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        byPolicy[cell["policy"]].append(cell)

    print(f"완료 셀 {len(cells)} / 정책 {sorted(byPolicy)}")
    print(f"주입 ±{arguments.settleSeconds:.0f}초는 과도 구간으로 버림\n")

    # 🔒 호스트 포화를 먼저 본다. 포화된 셀의 측정은 신뢰할 수 없다 (§4-1).
    saturations = [c["hostLoad"]["saturatedShare"] for c in cells
                   if c.get("hostLoad") and c["hostLoad"].get("saturatedShare") is not None]
    if saturations:
        worst = max(saturations)
        print(f"호스트 포화 비율 — 중앙값 {statistics.median(saturations):.0%} / 최대 {worst:.0%}")
        if worst > 0.2:
            print("  ⚠️ 포화된 셀이 있다. §4-1 프로토콜상 그 구간의 측정은 신뢰할 수 없다 —")
            print("     팬리스 머신에서 돌린 결과이므로 **예비 수치로만** 읽는다.")
        else:
            print("  ✅ 포화 없음. 적어도 호스트 경합이 결과를 지배하지는 않았다.")
    print()

    print("━━━ 🔴 오탐 비용 (H5a) — 정상 구간에 개입했는가 ━━━")
    print(f"{'정책':<6}{'셀':>4}{'정상 결정':>10}{'조치 비율':>11}{'장애 결정':>10}{'조치 비율':>11}")
    report: dict[str, dict] = {}
    for policy in sorted(byPolicy):
        normalAll, faultAll = [], []
        for cell in byPolicy[policy]:
            normal, fault = splitDecisions(cell, arguments.settleSeconds)
            normalAll.extend(normal)
            faultAll.extend(fault)
        normalShare = actionShare(normalAll)
        faultShare = actionShare(faultAll)
        report[policy] = {"normalDecisions": len(normalAll), "normalActionShare": normalShare,
                          "faultDecisions": len(faultAll), "faultActionShare": faultShare}
        marker = "  (수동)" if policy in PASSIVE else ""
        print(f"{policy:<6}{len(byPolicy[policy]):>4}{len(normalAll):>10}"
              f"{normalShare:>11.1%}{len(faultAll):>10}{faultShare:>11.1%}{marker}")

    print("\n━━━ 성능 (장애 구간 포함, 입구 기준) ━━━")
    print(f"{'정책':<6}{'goodput':>10}{'p95':>9}{'에러율':>9}")
    for policy in sorted(byPolicy):
        goodputs = [c["entryLoad"]["goodputRps"] for c in byPolicy[policy]
                    if c["entryLoad"].get("goodputRps") is not None]
        p95s = [c["entryLoad"]["latencyP95Millis"] for c in byPolicy[policy]
                if c["entryLoad"].get("latencyP95Millis") is not None]
        errors = [c["entryLoad"]["errorRate"] for c in byPolicy[policy]
                  if c["entryLoad"].get("errorRate") is not None]
        if not goodputs:
            continue
        report[policy].update({"goodputMedian": statistics.median(goodputs),
                               "p95Median": statistics.median(p95s),
                               "errorRateMedian": statistics.median(errors)})
        print(f"{policy:<6}{statistics.median(goodputs):>10.1f}"
              f"{statistics.median(p95s):>9.0f}{statistics.median(errors):>9.1%}")

    print("\n━━━ 판정 ━━━")
    a5 = report.get("A5", {})
    a6 = report.get("A6", {})
    if not a5 or not a6:
        print("  A5·A6 을 모두 돌려야 H2 의 비교가 성립한다.")
        return 0

    a5Normal = a5.get("normalActionShare", float("nan"))
    a6Normal = a6.get("normalActionShare", float("nan"))
    if math.isnan(a5Normal) or math.isnan(a6Normal):
        print("  정상 구간 결정이 없다. 워밍업이 정상 구간을 다 먹었는지 확인한다.")
        return 0

    print(f"  정상 구간 조치 비율 — A5 {a5Normal:.1%} vs A6 {a6Normal:.1%}")
    if a6Normal > a5Normal + 0.05:
        print("  ✅ **A5 가 A6 보다 덜 헛발질한다.** 불확실성을 할인해 참은 결과이며,")
        print("     H5a(장애 미발생 시 개입 비용)에서 A5 가 우월하다는 직접 증거다.")
        print("     → *\"신뢰도에 따라 가르는 것이 무시하고 가르는 것보다 낫다\"* 의 한 축.")
    elif a5Normal > a6Normal + 0.05:
        print("  ❌ A5 가 더 많이 개입한다. κ 가 p_eff 를 낮추는데도 그렇다면")
        print("     θₐ 가 너무 낮아 할인이 의미를 갖지 못하는 것이다 — θ 보정이 선행돼야 한다.")
    else:
        print("  ⚠️ 차이가 측정 한계 안이다. 두 가능성이 있다 —")
        print("     (1) 불확실성이 얇아 κ 가 할인할 여지가 적다")
        print("     (2) θₐ 가 0 에 가까워 p_eff 를 깎아도 여전히 임계를 넘는다")
        print("     두 번째가 유력하며, 그것이 Dₐ·Rₐ 실측이 선행돼야 하는 이유다.")

    print("\n  ⚠️ 완화효과(mₐ)는 이 환경에서 측정되지 않는다. 폐루프에서 병목 용량이")
    print("     외생적으로 고정되면 앱 측 조치는 대기 위치를 옮길 뿐 용량을 늘리지")
    print("     못한다 — HPA 가 용량을 바꾸는 게이트 ② 환경이 필요하다.")

    outputPath = RESULTS_DIR / "policyComparison.json"
    outputPath.write_text(json.dumps(
        {"cells": len(cells), "settleSeconds": arguments.settleSeconds,
         "hostSaturationMax": max(saturations) if saturations else None,
         "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
