"""`κ` 민감도 — 게이팅 효과는 `θ` 바로 위 **클램프 폭만큼의 창**에서 나온다.

왜 새 실험이 필요 없는가
========================

게이트가 결정을 바꾸는 조건은 정확히 이것이다.

    p_eff < θ <= p̄        (보류로 바뀐다)
    p_eff = max(0, p̄ − κ√u)

부등식을 다시 쓰면 **θ 바로 위, 폭 `κ√u` 인 창**이 된다.

    θ <= p̄ < θ + κ√u

즉 **게이팅 효과 = `P(θ <= p̄ < θ + κ√u)`** 이고, 이것은 근사가 아니라 항등식이다.
따라서 `κ` 를 바꿨을 때의 효과는 **이미 기록된 `(p̄, u, θ, 병목)` 만으로 계산된다.**
`κ` 하나마다 실험을 돌릴 필요가 없다.

세 가지가 따라 나온다.

1. **`θ` 가 `p̄` 분포의 밀집 구간에 있어야** 창에 질량이 실린다. `θ ≈ 0` 이면 창이
   분포 **바닥**에 놓여 효과가 0 이다 — 2026-11-02 실측에서 θ_CB=0.00025 였고
   바뀐 결정이 0 건이었다([[research/challenges.md]] J24).
2. **`κ` 를 키우면 창이 넓어진다.** 다만 넓히면 `p̄` 가 높은 결정까지 보류하게
   되므로 **놓치는 장애(미개입)가 늘어난다.** 공짜가 아니다.
3. 그래서 `κ` 에는 **최적이 있다.** 이 스크립트가 그 곡선을 그린다.

🔴 보류율로는 최적이 안 나온다 — 비용으로 재야 한다
====================================================

보류율은 `κ` 에 단조 증가하므로 *"가장 큰 `κ`"* 가 답이 되어 버린다. **보류는
놓친 장애이기도 하다.** 그래서 §2-B 의 비용함수로 **실현 비용**을 센다.

주입 시점(`injectedAt`)이 결정마다 `p` 의 사실상의 정답을 준다.

    정상 구간 (p=0)   개입 → `Dₐ·Rₐ`          보류 → 0
    장애 구간 (p=1)   개입 → `(1−mₐ)L + Dₐ`   보류 → `L`

이것은 §2-B 의 `E[비용|a]`·`E[비용|보류]` 에 `p=0`·`p=1` 을 대입한 것이며 **새
가정을 넣지 않는다.** `mₐ`·`Dₐ` 는 엔진 프로파일에서 가져온다.

⚠️ **`p=1` 은 과대평가다.** 장애 구간이라도 그 노드가 영향을 받지 않았을 수 있다
(흡수된 장애). 따라서 미개입 비용이 실제보다 크게 잡히고, **최적 `κ` 는 보수적
(작은 쪽)으로 치우친다.** 노드 단위 정답 라벨이 있으면 교정된다 — [[J22]] ④ 가
그것을 게이트 ② 로 넘긴 이유다.

⚠️ **한계**: 궤적을 고정한다. `κ` 를 바꾸면 실제로는 조치가 달라져 다음 틱의
위험도가 달라진다([[research/challenges.md]] J24 가 그것 때문에 A6 을 기준선에서
뺐다). 따라서 이 곡선은 **결정 수준의 반사실**이며, 고른 `κ` 는 폐루프로 확인해야
한다. 곡선의 용도는 **후보를 좁히는 것**이다.
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
sys.path.insert(0, str(HERE))

import comparePolicies as cmp  # noqa: E402

DEFAULT_KAPPAS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)
BASELINE_KAPPA = 1.0   # controlLoop 의 기본값. 로그의 p_eff 가 이 값으로 만들어졌다.


def actionParameters(nodeType: str = "service") -> dict[str, dict]:
    """병목별로 **엔진이 고를 조치**의 `(mₐ, Dₐ, Rₐ)`.

    손으로 적지 않는다 — 배선이 바뀌면 그 숫자가 조용히 낡는다. 엔진이 각 병목에서
    `θ` 가 유한한 조치 중 비용 최소를 고르므로, 여기서도 같은 기준으로 고른다.
    """
    sys.path.insert(0, str(HERE.parent / "model"))
    import policyEngine   # noqa: PLC0415

    chosen: dict[str, dict] = {}
    for bottleneck in ("S1", "S2", "S3"):
        best = None
        for profile in policyEngine.defaultProfiles():
            if profile.nodeType != nodeType:
                continue
            mitigation = profile.mitigationByBottleneck.get(bottleneck, 0.0)
            denominator = mitigation - profile.disruption * (1.0 - profile.reversibility)
            if denominator <= 0:
                continue   # θ = ∞ — 자동 배제
            threshold = profile.disruption * profile.reversibility / denominator
            if best is None or threshold < best["threshold"]:
                best = {"action": profile.name, "threshold": threshold,
                        "mitigation": mitigation, "disruption": profile.disruption,
                        "reversibility": profile.reversibility}
        if best:
            chosen[bottleneck] = best
    return chosen


def realisedCost(rows: list[dict], parameters: dict[str, dict], kappa: float,
                 availabilityLoss: float = 1.0) -> dict | None:
    """§2-B 의 비용함수에 `p=0`(정상 구간)·`p=1`(장애 구간)을 대입한 실현 비용."""
    total = 0.0
    counted = falsePositive = missed = normals = faults = 0
    for row in rows:
        parameter = parameters.get(row["bottleneck"])
        if parameter is None or row["window"] is None:
            continue
        counted += 1
        held = max(0.0, row["meanRisk"] - kappa * row["rootU"]) < parameter["threshold"]
        if row["window"] == "normal":
            normals += 1
            if held:
                continue                       # 비용 0
            total += parameter["disruption"] * parameter["reversibility"]
            falsePositive += 1
        else:
            faults += 1
            if held:
                total += availabilityLoss      # 완화를 놓쳤다
                missed += 1
            else:
                total += ((1.0 - parameter["mitigation"]) * availabilityLoss
                          + parameter["disruption"])
    if not counted:
        return None
    return {"cost": total / counted, "decisions": counted,
            "falsePositiveShare": falsePositive / normals if normals else float("nan"),
            "missedShare": missed / faults if faults else float("nan")}


def ownedCells(ledgerPath: pathlib.Path) -> list[dict]:
    """결정 로그를 **실제로 소유한** 셀만. 덮어써진 셀은 뺀다 (J24)."""
    entries = cmp.loadLedger(ledgerPath)
    latest: dict[str, str] = {}
    for entry in entries:
        path, stamp = entry.get("decisionPath"), entry.get("recordedAt") or ""
        if path and stamp >= latest.get(path, ""):
            latest[path] = stamp
    return [e for e in entries
            if e.get("decisionPath")
            and (e.get("recordedAt") or "") == latest.get(e["decisionPath"])]


def decisionRows(cell: dict, settleSeconds: float = 10.0) -> list[dict]:
    """`(p̄, √u, 병목)` 만 뽑는다. `√u` 는 로그의 클램프에서 역산한다.

    `uncertainty` 필드를 그대로 쓰지 않는 이유: 그것이 `u` 인지 `√u` 인지 규약이
    문서에 두 방식으로 나타난다. **로그가 실제로 쓴 값**은 `p̄ − p_eff` 이므로
    거기서 `√u = (p̄ − p_eff) / κ₀` 로 되돌리면 규약 해석이 끼어들지 않는다.
    단 `p_eff` 가 0 으로 클램프된 결정은 역산이 불가능하므로 표시해 둔다.
    """
    rows = []
    injectedAt = cell.get("injectedAt")
    for entry in cmp.allDecisions(cell):
        mean, effective = entry["meanRisk"], entry["effectiveRisk"]
        # 주입 ±settle 은 과도 구간이므로 라벨을 주지 않는다 — comparePolicies 와 같다.
        window = None
        if injectedAt is not None:
            offset = entry["timestamp"] - injectedAt
            if offset < -settleSeconds:
                window = "normal"
            elif offset > settleSeconds:
                window = "fault"
        rows.append({
            "bottleneck": entry.get("bottleneck"),
            "meanRisk": mean,
            "rootU": (mean - effective) / BASELINE_KAPPA,
            "clamped": effective <= 0.0 < mean,
            "window": window,
        })
    return rows


def holdShareAt(rows: list[dict], thresholds: dict[str, float], kappa: float) -> float | None:
    """그 `κ` 에서 보류가 될 결정의 비율."""
    total = held = 0
    for row in rows:
        threshold = thresholds.get(row["bottleneck"])
        if threshold is None:
            continue
        total += 1
        if max(0.0, row["meanRisk"] - kappa * row["rootU"]) < threshold:
            held += 1
    return held / total if total else None


def main() -> int:
    parser = argparse.ArgumentParser(description="κ 민감도 — 게이팅 효과 곡선")
    parser.add_argument("--ledger", default=str(cmp.RESULTS_DIR / "ledger.jsonl"))
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--digest", default=None)
    parser.add_argument("--policy", default="A5", help="게이팅 정책 하나 (기본 A5)")
    parser.add_argument("--kappas", default=",".join(str(k) for k in DEFAULT_KAPPAS))
    arguments = parser.parse_args()

    ledgerPath = pathlib.Path(arguments.ledger)
    if not ledgerPath.exists():
        print(f"원장이 없다: {ledgerPath}")
        return 1

    cells = ownedCells(ledgerPath)
    if arguments.scenario:
        cells = [c for c in cells if c.get("scenario") == arguments.scenario]
    if arguments.workers is not None:
        cells = [c for c in cells
                 if (c.get("entryLoad") or {}).get("workerCount") == arguments.workers]
    if arguments.digest:
        cells = [c for c in cells if cmp.cohortOf(c)[2] == arguments.digest]
    cells = [c for c in cells if c["policy"] == arguments.policy]
    if not cells:
        print("그 조건에 맞는, 결정 로그를 소유한 셀이 없다.")
        return 1

    cohorts = {cmp.cohortOf(c) for c in cells}
    if len(cohorts) > 1:
        print(f"🔴 코호트가 {len(cohorts)} 개다. `θ` 가 다를 수 있으므로 하나로 좁혀라:")
        for cohort in sorted(cohorts, key=str):
            print(f"     --scenario {cohort[0]} --workers {cohort[1]} --digest {cohort[2]}")
        return 1

    thresholds = cmp.observedThresholds(cells)
    if not thresholds:
        print("🔴 결정 로그에서 `θ` 를 관측할 수 없다 — 보류만 있는 셀인지 확인하라.")
        return 1

    kappas = [float(k) for k in arguments.kappas.split(",")]
    print(f"정책 {arguments.policy} / 셀 {len(cells)} / 코호트 {sorted(cohorts, key=str)[0]}")
    print("관측된 θ: " + " / ".join(f"{b} {t:.5f}" for b, t in sorted(thresholds.items())))

    perCell = [decisionRows(c) for c in cells]
    clampedShare = (sum(1 for rows in perCell for r in rows if r["clamped"])
                    / max(1, sum(len(rows) for rows in perCell)))
    print(f"결정 {sum(len(r) for r in perCell)} 건 / `p_eff` 가 0 으로 포화된 비율 "
          f"{clampedShare:.1%}")
    if clampedShare > 0.05:
        print("  ⚠️ 포화된 결정에서는 `√u` 역산이 하한이므로 큰 `κ` 의 값이 보수적이다")

    baseline = [holdShareAt(rows, thresholds, 0.0) for rows in perCell]
    baseline = [b for b in baseline if b is not None]
    baseMedian = statistics.median(baseline) if baseline else float("nan")
    print(f"\n`κ`=0 (A6 과 같다) 보류율 중앙값 {baseMedian:.1%} — 아래 '게이팅 몫' 의 기준선\n")

    parameters = actionParameters()
    print("엔진이 고르는 조치: " + " / ".join(
        f"{b} {p['action']} (mₐ={p['mitigation']}, Dₐ={p['disruption']}, θ={p['threshold']:.5f})"
        for b, p in sorted(parameters.items()) if b in thresholds))

    print(f"\n{'κ':>6}{'보류율':>8}{'게이팅 몫':>10}{'오탐 개입':>10}{'미개입':>8}"
          f"{'실현 비용':>10}{'반복 간 비용':>14}")
    rowsOut = []
    for kappa in kappas:
        shares = [holdShareAt(rows, thresholds, kappa) for rows in perCell]
        shares = [v for v in shares if v is not None]
        costs = [realisedCost(rows, parameters, kappa) for rows in perCell]
        costs = [c for c in costs if c]
        if not shares or not costs:
            continue
        median = statistics.median(shares)
        costMedian = statistics.median(c["cost"] for c in costs)
        falsePositive = statistics.median(c["falsePositiveShare"] for c in costs)
        missed = statistics.median(c["missedShare"] for c in costs)
        costRange = f"{min(c['cost'] for c in costs):.3f}~{max(c['cost'] for c in costs):.3f}"
        mark = "  ← 현재" if abs(kappa - BASELINE_KAPPA) < 1e-9 else ""
        print(f"{kappa:>6.2f}{median:>8.1%}{median - baseMedian:>+10.1%}"
              f"{falsePositive:>10.1%}{missed:>8.1%}{costMedian:>10.3f}"
              f"{costRange:>14}{mark}")
        rowsOut.append({"kappa": kappa, "holdShare": median,
                        "gatingShare": median - baseMedian,
                        "falsePositiveShare": falsePositive, "missedShare": missed,
                        "realisedCost": costMedian})

    # 병목별 창 해석 — 왜 그 모양인지 설명이 붙어야 곡선이 읽힌다.
    print(f"\n{'병목':<5}{'건':>5}{'θ':>10}{'p̄ p50':>9}{'√u p50':>9}"
          f"{'θ 가 p̄ 분포의':>14}")
    allRows = [r for rows in perCell for r in rows]
    for bottleneck, threshold in sorted(thresholds.items()):
        sub = [r for r in allRows if r["bottleneck"] == bottleneck]
        if not sub:
            continue
        means = sorted(r["meanRisk"] for r in sub)
        roots = sorted(r["rootU"] for r in sub)
        pick = lambda v, q: v[min(len(v) - 1, int(q * len(v)))]
        below = sum(1 for m in means if m < threshold) / len(means)
        print(f"{bottleneck:<5}{len(sub):>5}{threshold:>10.5f}{pick(means, .5):>9.3f}"
              f"{pick(roots, .5):>9.4f}{below:>13.1%} 아래")

    print("\n**게이팅 효과 = `P(θ <= p̄ < θ + κ√u)`** — `θ` 바로 위 폭 `κ√u` 의 창이다.")
    print("`θ` 가 `p̄` 분포의 0% 아래에 있으면 창이 바닥에 놓여 질량이 없다.")
    print("⚠️ 궤적을 고정한 **결정 수준 반사실**이다. 고른 `κ` 는 폐루프로 확인해야 한다.")

    # ━━━ κ 가 이득이 될 조건 — 겨냥하는 띠에 오탐이 있는가 ━━━
    #
    # 보류가 절약하는 것은 `Dₐ·Rₐ`(p=0), 잃는 것은 `mₐL − Dₐ`(p=1) 이고 손익분기가
    # 정확히 `θ` 다. 클램프는 `p̄ ∈ [θ, θ+κ√u)` 인 결정을 보류시키므로, **그 띠에
    # 실제로 오탐이 있어야** κ 가 이득이다. 모델이 그 띠에서 과신해야 한다는 뜻이다.
    labelled = [r for rows in perCell for r in rows if r["window"] is not None]
    if labelled:
        print(f"\n━━━ `κ` 가 겨냥하는 띠에 오탐이 있는가 ━━━")
        print(f"라벨된 결정 {len(labelled)} 건 "
              f"(정상 {sum(1 for r in labelled if r['window'] == 'normal')} / "
              f"장애 {sum(1 for r in labelled if r['window'] == 'fault')})")
        print(f"{'병목':<5}{'θ':>10}{'κ':>6}{'띠':<18}{'건':>5}{'그중 정상':>10}  판정")
        for bottleneck, parameter in sorted(parameters.items()):
            if bottleneck not in thresholds:
                continue
            threshold = parameter["threshold"]
            sub = [r for r in labelled if r["bottleneck"] == bottleneck]
            if not sub:
                continue
            widths = sorted(r["rootU"] for r in sub)
            width = widths[len(widths) // 2]
            for kappa in (BASELINE_KAPPA,):
                band = [r for r in sub
                        if threshold <= r["meanRisk"] < threshold + kappa * width]
                normals = sum(1 for r in band if r["window"] == "normal")
                label = f"[{threshold:.3f},{threshold + kappa * width:.3f})"
                if not band:
                    verdict = "🔴 띠가 비었다 — κ 가 겨냥할 대상이 없다"
                elif normals == 0:
                    verdict = "🔴 전부 장애 — 보류하면 **놓친 장애만** 늘어난다"
                else:
                    verdict = f"✅ 오탐 {normals} 건을 절약할 수 있다"
                print(f"{bottleneck:<5}{threshold:>10.5f}{kappa:>6.2f}{label:<18}"
                      f"{len(band):>5}{normals:>10}  {verdict}")
        print("  ⚠️ **`p=0/1` 라벨은 에피소드 단위 창이다** — 장애 구간이라도 그 노드가")
        print("     영향받지 않았을 수 있다. 따라서 '정상' 로 센 것은 **하한**이고,")
        print("     '전부 장애' 라는 판정은 **그 띠에 절약할 오탐이 없었다**는 뜻으로만")
        print("     읽는다. 캘리브레이션의 정도는 이 라벨로 정할 수 없다 — 노드 단위")
        print("     정답 라벨이 필요하다([[research/challenges.md]] J22 ④).")

    if rowsOut:
        best = min(rowsOut, key=lambda r: r["realisedCost"])
        current = next((r for r in rowsOut if abs(r["kappa"] - BASELINE_KAPPA) < 1e-9), None)
        print(f"\n**비용 최소: κ={best['kappa']:.2f}** — 실현 비용 {best['realisedCost']:.3f} "
              f"(오탐 개입 {best['falsePositiveShare']:.1%} / 미개입 {best['missedShare']:.1%})")
        if current:
            gap = current["realisedCost"] - best["realisedCost"]
            if abs(gap) < 1e-9:
                print(f"현재 κ={BASELINE_KAPPA} 가 이미 최소다.")
            else:
                print(f"현재 κ={BASELINE_KAPPA} 는 {current['realisedCost']:.3f} 로 "
                      f"{gap:+.3f} 만큼 비싸다.")
        print("⚠️ **보류율이 높은 것이 좋은 것은 아니다** — 보류는 놓친 장애이기도 하다.")
        print("   그래서 이 표는 보류율이 아니라 **실현 비용**으로 고른다.")

    outputPath = cmp.RESULTS_DIR / "kappaSensitivity.json"
    outputPath.write_text(json.dumps(
        {"policy": arguments.policy, "cells": len(cells),
         "cohort": list(sorted(cohorts, key=str)[0]), "thresholds": thresholds,
         "baselineHoldShare": baseMedian, "curve": rowsOut},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
