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

🔴 코호트를 섞지 않는다 (2026-11-02)
=====================================

종전에는 원장의 모든 셀을 `policy` 로만 묶었다. **원장에 부하 수준이나 시나리오가
다른 셀이 함께 있으면 조용히 틀린 숫자가 나온다.**

실제로 2026-11-02 현재 원장에는 두 코호트가 있다.

==========  =====  ======  ==================================
시나리오     워커    반복    비고
==========  =====  ======  ==================================
s3          64     2      기준선 p95 357.9ms — J17 의 오염 수준
s1          32     5      기준선 p95 134.2ms — 예측기 학습 분포
==========  =====  ======  ==================================

`goodput` 은 워커 수에 직접 종속되므로 **두 코호트의 중앙값을 함께 내면 그 값은
아무것도 뜻하지 않는다.** 그래서 `(scenario, workerCount)` 가 둘 이상이면
**기본적으로 거부한다.** 섞어 보려면 `--pool` 로 명시해야 한다
([episodeValidity.py](../model/episodeValidity.py) 의 `--keepInvalid` 와 같은 규칙).
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
# `p_eff` 를 쓰는 정책. 여기서 보류는 **게이팅이 참은 것**이다.
GATED = {"A5", "A6"}
# 규칙 기반. 보류는 **규칙이 발동하지 않은 것**이며 게이팅과 뜻이 다르다 —
# 숨기면 정보를 잃고, 같은 열에 그냥 두면 다른 것을 같다고 읽게 된다. 표식을 붙인다.
RULE_BASED = {"A2"}


def cohortOf(cell: dict) -> tuple:
    """셀이 속한 비교 단위. 이것이 다르면 중앙값을 함께 내면 안 된다.

    🔴 **엔진 파라미터 지문도 코호트다** (2026-11-02). `Dₐ` 를 배선하고 A5 를 다시
    돌리면 `cellKey` 가 같아서 **원장만 보고는 배선 전 셀과 구분되지 않는다.**
    지문이 없는 셀(배선 이전)은 `"배선전"` 으로 묶는다.
    """
    profiles = cell.get("actionProfiles") or {}
    return (cell.get("scenario"),
            (cell.get("entryLoad") or {}).get("workerCount"),
            profiles.get("digest") or "배선전")


def holdShareOf(cell: dict) -> float:
    summary = cell.get("loopSummary") or {}
    value = summary.get("holdShare")
    return float(value) if isinstance(value, (int, float)) else float("nan")


def appliedOf(cell: dict) -> int:
    outcomes = ((cell.get("loopSummary") or {}).get("outcomeCounts") or {})
    return int(outcomes.get("applied", 0))


def spread(values: list[float]) -> str:
    """반복이 여러 번이면 산포를 함께 본다 — 중앙값 하나는 결과가 아니다."""
    if len(values) < 2:
        return "단일"
    return f"{min(values):.0f}~{max(values):.0f}"


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
    parser.add_argument("--scenario", default=None, help="이 시나리오만 본다 (예: s1)")
    parser.add_argument("--workers", type=int, default=None, help="이 부하 수준만 본다")
    parser.add_argument("--digest", default=None,
                        help="이 엔진 파라미터 지문만 본다. '배선전' 은 지문 없는 셀")
    parser.add_argument("--pool", action="store_true",
                        help="🔴 코호트가 섞여도 강행한다. goodput 중앙값이 무의미해진다")
    arguments = parser.parse_args()

    ledgerPath = pathlib.Path(arguments.ledger)
    if not ledgerPath.exists():
        print(f"원장이 없다: {ledgerPath}")
        return 1
    cells = loadLedger(ledgerPath)
    if not cells:
        print("완료된 셀이 없다.")
        return 1

    # 🔒 코호트 먼저. 섞인 중앙값은 조용히 틀린다.
    if arguments.scenario:
        cells = [c for c in cells if c.get("scenario") == arguments.scenario]
    if arguments.workers is not None:
        cells = [c for c in cells
                 if (c.get("entryLoad") or {}).get("workerCount") == arguments.workers]
    if arguments.digest:
        cells = [c for c in cells if cohortOf(c)[2] == arguments.digest]
    if not cells:
        print("그 조건에 맞는 셀이 없다.")
        return 1

    cohorts: dict[tuple, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        cohorts[cohortOf(cell)].append(cell)
    print(f"{'시나리오':<10}{'워커':>6}{'엔진 지문':<14}{'셀':>5}  정책")
    for (scenario, workers, digest), group in sorted(cohorts.items(), key=lambda kv: str(kv[0])):
        policies = ",".join(sorted({c["policy"] for c in group}))
        print(f"{str(scenario):<10}{str(workers):>6}  {str(digest):<12}{len(group):>5}  {policies}")
    if len(cohorts) > 1 and not arguments.pool:
        print("\n🔴 **코호트가 둘 이상이다.** `goodput` 은 워커 수에 직접 종속되므로")
        print("   이들의 중앙값을 함께 내면 그 값은 아무것도 뜻하지 않는다.")
        print("   **엔진 지문이 다른 것은 더 심각하다** — 같은 `cellKey` 인데 다른 `Dₐ`")
        print("   로 만들어진 셀이므로 `θ` 가 다르다. 보류율을 함께 내면 안 된다.")
        print("   하나를 고르거나, 섞는 것을 감수한다고 명시하라 —")
        print("     --scenario s1 --workers 32 --digest <지문>")
        print("     --pool            (🔴 섞음을 감수한다)")
        return 1
    if len(cohorts) > 1:
        print("\n⚠️ `--pool` — 코호트를 섞었다. goodput·p95 의 중앙값은 **읽지 않는다.**")
    print()

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
    print(f"{'정책':<6}{'셀':>4}{'정상 결정':>10}{'조치 비율':>11}{'장애 결정':>10}"
          f"{'조치 비율':>11}{'보류중앙':>9}{'적용':>7}  보류 분포(반복별)")
    report: dict[str, dict] = {}
    for policy in sorted(byPolicy):
        normalAll, faultAll = [], []
        for cell in byPolicy[policy]:
            normal, fault = splitDecisions(cell, arguments.settleSeconds)
            normalAll.extend(normal)
            faultAll.extend(fault)
        normalShare = actionShare(normalAll)
        faultShare = actionShare(faultAll)
        holds = [h for h in (holdShareOf(c) for c in byPolicy[policy]) if h == h]
        holdMedian = statistics.median(holds) if holds else float("nan")
        appliedTotal = sum(appliedOf(c) for c in byPolicy[policy])
        report[policy] = {"normalDecisions": len(normalAll), "normalActionShare": normalShare,
                          "faultDecisions": len(faultAll), "faultActionShare": faultShare,
                          "holdShareMedian": None if holdMedian != holdMedian else holdMedian,
                          "appliedTotal": appliedTotal}
        marker = "  (수동)" if policy in PASSIVE else ""
        # 보류율은 `p_eff` 를 쓰는 정책에서만 뜻을 갖는다. 나머지는 가리지 않고 비운다.
        if holdMedian != holdMedian or policy in PASSIVE:
            holdCell = f"{'—':>9}"
        else:
            suffix = "†" if policy in RULE_BASED else " "
            holdCell = f"{holdMedian:>8.1%}{suffix}"
        # 🔴 반복이 여러 번이면 보류율도 산포를 봐야 한다. 5회 중 2회만 참았는데
        #    중앙값이 0 이면 "한 번도 참지 않았다" 로 읽힌다 — 실제와 다르다.
        holdSpread = ("[" + " ".join(f"{h:.0%}" for h in sorted(holds)) + "]") if holds else ""
        report[policy]["holdShareValues"] = sorted(holds)
        print(f"{policy:<6}{len(byPolicy[policy]):>4}{len(normalAll):>10}"
              f"{normalShare:>11.1%}{len(faultAll):>10}{faultShare:>11.1%}"
              f"{holdCell}{appliedTotal:>7}  {holdSpread}{marker}")

    print("\n━━━ 성능 (장애 구간 포함, 입구 기준) ━━━")
    print(f"{'정책':<6}{'goodput':>10}{'(범위)':>12}{'p95':>9}{'(범위)':>12}{'에러율':>9}")
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
        report[policy]["goodputSpread"] = spread(goodputs)
        report[policy]["p95Spread"] = spread(p95s)
        print(f"{policy:<6}{statistics.median(goodputs):>10.1f}{spread(goodputs):>12}"
              f"{statistics.median(p95s):>9.0f}{spread(p95s):>12}"
              f"{statistics.median(errors):>9.1%}")

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

    a5Hold = a5.get("holdShareMedian")
    a6Hold = a6.get("holdShareMedian")
    print(f"  정상 구간 조치 비율 — A5 {a5Normal:.1%} vs A6 {a6Normal:.1%}")
    if a5Hold is not None and a6Hold is not None:
        print(f"  보류율           — A5 {a5Hold:.1%} vs A6 {a6Hold:.1%}  "
              f"(차이 {a5Hold - a6Hold:+.1%})")
        a5Values = a5.get("holdShareValues") or []
        a6Values = a6.get("holdShareValues") or []
        a5Held = sum(1 for v in a5Values if v > 0)
        a6Held = sum(1 for v in a6Values if v > 0)
        print(f"  참은 반복 수       — A5 {a5Held}/{len(a5Values)} vs A6 {a6Held}/{len(a6Values)}")
        if a5Held == 0 and a6Held == 0:
            print("  🔴 **어느 반복에서도 보류가 없다.** 게이팅이 아무것도 하지 않았다 —")
            print("     H5a 를 보류로 논증할 수 없다.")
        elif a5Held > a6Held and a5Hold == 0.0:
            print(f"  ⚠️ **방향은 맞으나 재현되지 않는다.** A5 는 {a5Held}/{len(a5Values)} 회만")
            print("     참았고 중앙값은 0 이다. **중앙값만 보면 \"참지 않았다\" 로 읽힌다.**")
            print("     효과의 부호는 예측과 같으나 크기가 반복 간 산포 안이다 —")
            print("     **이 부하 수준에서는 게이팅이 실증되지 않았다.**")
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
