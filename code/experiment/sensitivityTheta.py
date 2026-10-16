"""`θₐ` 민감도 분석 — 과잉 개입이 캘리브레이션 문제인가 구조 문제인가.

폐루프를 돌려 보니 장애가 없는 구간에서도 A5 가 조치를 66.7% 내보냈다. 두
가능성이 있고 **구분이 안 된 상태였다.**

1. **가정값이 나빠서** → 캘리브레이션 문제. 값을 고치면 해소된다
2. **어떤 값을 넣어도 그렇게 되는 구조** → 비용함수 설계 자체의 문제

이것을 가리는 것이 이 분석의 목적이다. 후자면 지금 알아야 한다.

`Dₐ` 는 측정 가능하다 — 문서가 연결을 안 해뒀을 뿐이다
========================================================

§2-B 의 정의가 그렇다.

    Dₐ  = 조치 a 의 disruption — **즉시 비용**(정상 트래픽 손실·자원 소모)
    H5a = **장애 미발생 시 개입 비용** — 아무 문제 없던 시스템에 조치를 가한 순손실

**같은 것이다.** §4-5 가 `mₐ` 를 *"장애 구간에서 조치 유무 대조"* 로 정의했는데,
**같은 실험의 정상 구간이 `Dₐ`** 다. 그런데 §4-4·§4-5 에 `Dₐ` 측정 절이 없다.

`Rₐ` 는 다르다 — 측정 대상이 아니다
=====================================

*"되돌리기 난이도"* 는 물리량이 아니라 **조직의 운영 역량**이다. Scale-out 의
`Rₐ` 가 0.7 인지 0.9 인지는 그 조직에 달렸다. §2-B 가 LLM Brownout 의
`D_downgrade` 를 **운영자 정책 파라미터 + 민감도 분석**으로 처리한 것과 같은
취급이 맞다. 측정하지 않고 **범위를 흔들어 결론의 강건성을 보인다.**
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "model"))

from policyEngine import INFINITE_THRESHOLD, confidenceThreshold, ActionProfile  # noqa: E402

HOLD = "보류"


def loadCells(ledgerPath: pathlib.Path) -> list[dict]:
    cells = [json.loads(line) for line in ledgerPath.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return [c for c in cells if c["status"] == "done"]


def windowMedian(cell: dict, node: str, low: float, high: float, key: str) -> float:
    rows = [json.loads(line) for line
            in (HERE / cell["tracePath"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    injected = cell["injectedAt"]
    values = [r.get(key) for r in rows
              if r["node"] == node and low <= r["timestamp"] - injected < high
              and isinstance(r.get(key), (int, float)) and not math.isnan(r.get(key))]
    return statistics.median(values) if values else float("nan")


def measureDisruption(cells: list[dict], baselinePolicy: str = "A4") -> dict:
    """정상 구간의 처리량 손실로 `Dₐ` 를 실측한다. 이것이 H5a 다.

    장애가 없는 구간에서 조치를 걸었을 때의 손실이므로, `Dₐ` 의 정의
    *"즉시 비용 — 정상 트래픽 손실"* 과 정확히 일치한다.
    """
    byPolicy: dict[str, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        byPolicy[cell["policy"]].append(cell)

    throughput = {}
    for policy, policyCells in byPolicy.items():
        values = [windowMedian(c, "gateway", -40.0, -10.0, "throughputRps") for c in policyCells]
        values = [v for v in values if not math.isnan(v)]
        if values:
            throughput[policy] = statistics.median(values)

    if baselinePolicy not in throughput:
        return {}
    baseline = throughput[baselinePolicy]
    return {policy: {"throughput": value,
                     # 가용성 손실 비율 = Dₐ. L=1 스케일에 그대로 대응한다.
                     "disruption": max(0.0, 1.0 - value / baseline)}
            for policy, value in throughput.items()}


def collectNormalRisks(cells: list[dict], policy: str, settleSeconds: float) -> list[float]:
    """정상 구간의 `p_eff` 분포. 오탐율을 θ 함수로 계산하는 재료다."""
    risks = []
    for cell in cells:
        if cell["policy"] != policy or not cell.get("decisionPath"):
            continue
        path = HERE / cell["decisionPath"]
        if not path.exists():
            continue
        injected = cell["injectedAt"]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry["timestamp"] - injected < -settleSeconds:
                risks.append(entry["effectiveRisk"])
    return risks


def collectFaultRisks(cells: list[dict], policy: str, settleSeconds: float) -> list[float]:
    risks = []
    for cell in cells:
        if cell["policy"] != policy or not cell.get("decisionPath"):
            continue
        path = HERE / cell["decisionPath"]
        if not path.exists():
            continue
        injected = cell["injectedAt"]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry["timestamp"] - injected > settleSeconds:
                risks.append(entry["effectiveRisk"])
    return risks


def main() -> int:
    parser = argparse.ArgumentParser(description="θₐ 민감도 분석")
    parser.add_argument("--ledger", default=str(HERE / "results" / "ledger.jsonl"))
    parser.add_argument("--policy", default="A5", help="p_eff 분포를 가져올 정책")
    parser.add_argument("--settleSeconds", type=float, default=10.0)
    parser.add_argument("--mitigation", type=float, default=0.8,
                        help="mₐ. S1 의 Bulkhead 기대값 (§2-B 표)")
    parser.add_argument("--availabilityLoss", type=float, default=1.0, help="L")
    arguments = parser.parse_args()

    cells = loadCells(pathlib.Path(arguments.ledger))
    if not cells:
        print("완료된 셀이 없다.")
        return 1

    measured = measureDisruption(cells)
    print("━━━ Dₐ 실측 — 정상 구간 처리량 손실 (= H5a) ━━━")
    print(f"{'정책':<6}{'정상 rps':>10}{'Dₐ':>9}")
    for policy in sorted(measured):
        row = measured[policy]
        print(f"{policy:<6}{row['throughput']:>10.1f}{row['disruption']:>9.3f}")
    measuredDisruption = measured.get(arguments.policy, {}).get("disruption", float("nan"))
    print(f"\n  {arguments.policy} 의 실측 Dₐ = {measuredDisruption:.3f}")
    print("  ⚠️ 가정값은 0.25 였다. 조치가 실제로는 훨씬 비싸다.")

    normalRisks = collectNormalRisks(cells, arguments.policy, arguments.settleSeconds)
    faultRisks = collectFaultRisks(cells, arguments.policy, arguments.settleSeconds)
    if not normalRisks or not faultRisks:
        print("\n결정 기록이 부족하다.")
        return 1

    print(f"\n정상 구간 p_eff {len(normalRisks)}개 (중앙값 {statistics.median(normalRisks):.3f})")
    print(f"장애 구간 p_eff {len(faultRisks)}개 (중앙값 {statistics.median(faultRisks):.3f})")

    def shareAbove(values: list[float], threshold: float) -> float:
        if threshold == INFINITE_THRESHOLD:
            return 0.0
        return sum(1 for v in values if v > threshold) / len(values)

    # ── Rₐ 를 흔든다. Dₐ 는 실측값을 쓴다. ────────────────────────────────
    print(f"\n━━━ Rₐ 민감도 (Dₐ={measuredDisruption:.2f} 실측, "
          f"mₐ={arguments.mitigation}, L={arguments.availabilityLoss}) ━━━")
    print(f"{'Rₐ':>6}{'θₐ':>10}{'정상 조치율':>13}{'장애 조치율':>13}  판정")
    rows = []
    # 격자를 촘촘히 한다. 쓸 만한 구간의 **폭**을 알아야 "강건하다"고 말할 수 있다 —
    # 한 점에서만 성립하면 Rₐ 선택에 결과가 좌우된다는 뜻이다.
    grid = [round(0.02 * i, 2) for i in range(1, 16)] + [0.4, 0.5, 0.7, 0.9]
    for reversibility in grid:
        profile = ActionProfile("BULKHEAD", "service", disruption=measuredDisruption,
                                reversibility=reversibility, leadTimeSeconds=0.1,
                                mitigationByBottleneck={"S1": arguments.mitigation})
        threshold = confidenceThreshold(profile, "S1", arguments.availabilityLoss)
        normalShare = shareAbove(normalRisks, threshold)
        faultShare = shareAbove(faultRisks, threshold)
        rows.append({"reversibility": reversibility,
                     "threshold": None if threshold == INFINITE_THRESHOLD else threshold,
                     "normalActionShare": normalShare, "faultActionShare": faultShare})
        label = "∞" if threshold == INFINITE_THRESHOLD else f"{threshold:.3f}"
        # 쓸 만한 영역 = 정상에서 적게 치고 장애에서 많이 치는 곳
        verdict = ("✅ 쓸 만하다" if normalShare < 0.2 and faultShare > 0.5
                   else "전원 보류" if threshold == INFINITE_THRESHOLD or faultShare < 0.1
                   else "과잉 개입" if normalShare > 0.5 else "애매")
        print(f"{reversibility:>6.2f}{label:>10}{normalShare:>13.1%}{faultShare:>13.1%}  {verdict}")

    print("\n━━━ 가정값과의 대조 ━━━")
    assumed = ActionProfile("BULKHEAD", "service", disruption=0.25, reversibility=0.1,
                            leadTimeSeconds=0.1,
                            mitigationByBottleneck={"S1": arguments.mitigation})
    assumedThreshold = confidenceThreshold(assumed, "S1", arguments.availabilityLoss)
    print(f"  가정 (Dₐ=0.25, Rₐ=0.10)  θ={assumedThreshold:.3f}"
          f"  정상 {shareAbove(normalRisks, assumedThreshold):.1%}"
          f"  장애 {shareAbove(faultRisks, assumedThreshold):.1%}")
    measuredProfile = ActionProfile("BULKHEAD", "service", disruption=measuredDisruption,
                                    reversibility=0.1, leadTimeSeconds=0.1,
                                    mitigationByBottleneck={"S1": arguments.mitigation})
    measuredThreshold = confidenceThreshold(measuredProfile, "S1", arguments.availabilityLoss)
    label = "∞" if measuredThreshold == INFINITE_THRESHOLD else f"{measuredThreshold:.3f}"
    print(f"  실측 (Dₐ={measuredDisruption:.2f}, Rₐ=0.10)  θ={label}"
          f"  정상 {shareAbove(normalRisks, measuredThreshold):.1%}"
          f"  장애 {shareAbove(faultRisks, measuredThreshold):.1%}")

    print("\n━━━ 판정 ━━━")
    usable = [r for r in rows if r["normalActionShare"] < 0.2 and r["faultActionShare"] > 0.5]
    if usable:
        print("  ✅ **캘리브레이션 문제다.** 정상에서 적게 치고 장애에서 많이 치는")
        print(f"     Rₐ 영역이 존재한다 — {[r['reversibility'] for r in usable]}")
        print("     즉 비용함수 설계가 아니라 **가정값이 나빴다.** Dₐ 를 실측으로")
        print("     교체하면 과잉 개입이 해소된다.")
    else:
        print("  🔴 **구조 문제일 수 있다.** 어떤 Rₐ 에서도 정상/장애가 분리되지 않는다.")
        print("     두 가능성을 더 가려야 한다 — (1) 예측기가 정상·장애를 구분하지")
        print("     못한다 (2) 비용함수가 그 구분을 조치로 번역하지 못한다.")
        normalMedian = statistics.median(normalRisks)
        faultMedian = statistics.median(faultRisks)
        print(f"     p_eff 중앙값: 정상 {normalMedian:.3f} vs 장애 {faultMedian:.3f}")
        if abs(faultMedian - normalMedian) < 0.1:
            print("     → **(1) 이 유력하다.** 예측기가 두 구간을 거의 같게 본다.")
        else:
            print("     → **(2) 가 유력하다.** 예측은 갈리는데 θ 가 그 사이에 없다.")

    outputPath = HERE / "results" / "sensitivityTheta.json"
    outputPath.write_text(json.dumps(
        {"measuredDisruption": measured, "policy": arguments.policy,
         "normalRiskMedian": statistics.median(normalRisks),
         "faultRiskMedian": statistics.median(faultRisks),
         "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
