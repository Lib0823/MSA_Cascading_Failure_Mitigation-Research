"""H5b 판정 — 조치 오선택이 무조치보다 나쁜가.

`mₐ` 는 §4-4 대로 **조치 유무 대조**로 추정한다. 같은 시나리오의 무조치 칸을
기준으로 장애 구간의 회복분을 본다.

    m_a(시나리오) = (조치 적용 시 성능 − 무조치 성능) / 무조치 성능

성능 지표는 두 축을 함께 본다. 어느 하나로는 조치의 성격이 가려지기 때문이다.

- **goodput** — Bulkhead 는 동시 진입을 막으므로 처리량을 깎을 수 있다.
  처리량만 보면 "나쁜 조치"로 오판된다.
- **p95** — CB 는 빠르게 실패시키므로 성공한 호출의 지연은 **오히려 낮아 보인다.**
  지연만 보면 "좋은 조치"로 오판된다.

따라서 **에러율을 반드시 함께 읽는다.** 빠른 실패로 얻은 낮은 지연은 개선이 아니다.

판정의 핵심은 두 가지다.

1. **정답이 상충하는가** — S1 의 최선과 S3 의 최선이 서로 다른 조치인가
2. **오선택이 무조치보다 나쁜가** — 이것이 H5b 다. 단순히 "덜 좋다"가 아니라
   **음수 구간이 실재하는가**를 묻는다. 없으면 *"조치를 고르는 것"* 의 가치가
   *"아무거나 해도 손해는 없다"* 로 떨어진다.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics

# "의미 있는 차이"의 하한. 측정 잡음을 효과로 읽지 않기 위한 값이다.
EFFECT_THRESHOLD = 0.10

EXPECTED = {
    ("s1", "BULKHEAD"): "정답",
    ("s1", "CIRCUIT_BREAKER"): "역효과 예상",
    ("s3", "CIRCUIT_BREAKER"): "정답",
    ("s3", "BULKHEAD"): "무효 예상",
}


def relativeChange(baseline: float, value: float) -> float:
    if not baseline or math.isnan(baseline) or math.isnan(value):
        return float("nan")
    return (value - baseline) / baseline


def nodeWindow(cell: dict, node: str) -> dict:
    """한 노드의 **장애 구간** 지표 중앙값.

    ⚠️ 입구 지표를 ``loadGen`` 집계값으로 쓰면 안 된다. 그것은 정상 구간과 장애
    구간을 **합쳐서** 낸 값이라, 장애만 보는 하류 값과 섞으면 비교가 성립하지
    않는다(재시도 낭비가 음수로 나왔던 원인). 모든 지표를 같은 창에서 뽑는다.

    하류를 따로 보는 이유는 따로 있다.

    무조치에서 payment 가 125.6 rps 를 처리하는데 입구는 109.3 rps 만 성공한 적이
    있다. 차이는 재시도로 낭비된 용량이고, 재시도가 실패를 흡수하므로 **입구
    에러율은 0% 로 보인다.** 조치의 효과를 입구에서만 재면 이 낭비가 통째로 가려진다.
    """
    here = pathlib.Path(__file__).resolve().parent
    rows = [json.loads(line)
            for line in (here / cell["tracePath"]).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    injected = cell["injectedAt"]
    selected = [r for r in rows if r["node"] == node and r["timestamp"] > injected + 15]

    def median(key: str) -> float:
        values = [r.get(key) for r in selected
                  if isinstance(r.get(key), (int, float)) and not math.isnan(r.get(key))]
        return statistics.median(values) if values else float("nan")

    return {"throughputRps": median("throughputRps"), "errorRate": median("errorRate"),
            "latencyP95Millis": median("latencyP95"),
            "poolAcquireMillis": median("poolAcquireMillis"), "poolPending": median("poolPending")}


def main() -> int:
    parser = argparse.ArgumentParser(description="H5b 조치 오선택 비용 판정")
    parser.add_argument("--cells", required=True, help="runRemediation.py 가 남긴 remediation-*.json")
    arguments = parser.parse_args()

    cellsPath = pathlib.Path(arguments.cells).resolve()
    cells = json.loads(cellsPath.read_text(encoding="utf-8"))

    byKey = {(c["scenario"], c["remediation"]): c for c in cells}
    scenarios = sorted({c["scenario"] for c in cells})

    report: dict = {}
    print("━━━ 장애 구간 원시 측정 (입구 노드) ━━━")
    print(f"{'시나리오':<8}{'조치':<18}{'처리량':>10}{'p95':>9}{'에러율':>9}")
    entry: dict = {}
    for scenario in scenarios:
        for remediation in ("NONE", "BULKHEAD", "CIRCUIT_BREAKER"):
            cell = byKey.get((scenario, remediation))
            if cell is None:
                continue
            window = nodeWindow(cell, "gateway")
            entry[(scenario, remediation)] = window
            print(f"{scenario:<8}{remediation:<18}{window['throughputRps']:>10.1f}"
                  f"{window['latencyP95Millis']:>9.0f}{window['errorRate']:>9.1%}")
        print()

    print("━━━ 하류 부하 — 입구에서는 보이지 않는다 ━━━")
    print(f"{'시나리오':<8}{'조치':<18}{'입구 goodput':>14}{'하류 처리량':>13}"
          f"{'재시도 낭비':>13}{'하류 에러율':>13}")
    for scenario in scenarios:
        for remediation in ("NONE", "BULKHEAD", "CIRCUIT_BREAKER"):
            cell = byKey.get((scenario, remediation))
            if cell is None:
                continue
            downstream = nodeWindow(cell, cell["injectionNode"])
            entryWindow = entry[(scenario, remediation)]
            # 성공분만 센다. 실패한 요청은 입구가 얻은 것이 아니다.
            entryGoodput = entryWindow["throughputRps"] * (1.0 - entryWindow["errorRate"])
            downstreamRps = downstream["throughputRps"]
            # 하류가 처리한 양에서 입구가 성공한 양을 뺀 것이 재시도로 버려진 용량이다.
            waste = (downstreamRps - entryGoodput) / downstreamRps if downstreamRps else float("nan")
            report.setdefault(scenario, {}).setdefault(remediation, {})["downstream"] = downstream
            report[scenario][remediation]["retryWasteShare"] = waste
            print(f"{scenario:<8}{remediation:<18}{entryGoodput:>14.1f}{downstreamRps:>13.1f}"
                  f"{waste:>13.1%}{downstream['errorRate']:>13.1%}")
        print()

    print("━━━ mₐ — 무조치 대비 ━━━")
    print(f"{'시나리오':<8}{'조치':<18}{'goodput':>10}{'p95':>10}{'에러율 변화':>13}  기대")
    for scenario in scenarios:
        baseline = byKey.get((scenario, "NONE"))
        if baseline is None:
            continue
        baseLoad = entry[(scenario, "NONE")]
        baseGoodput = baseLoad["throughputRps"] * (1.0 - baseLoad["errorRate"])
        for remediation in ("BULKHEAD", "CIRCUIT_BREAKER"):
            cell = byKey.get((scenario, remediation))
            if cell is None:
                continue
            load = entry[(scenario, remediation)]
            goodput = load["throughputRps"] * (1.0 - load["errorRate"])
            goodputChange = relativeChange(baseGoodput, goodput)
            # p95 는 낮을수록 좋으므로 부호를 뒤집어 "개선이면 양수"로 맞춘다.
            p95Change = -relativeChange(baseLoad["latencyP95Millis"], load["latencyP95Millis"])
            errorDelta = load["errorRate"] - baseLoad["errorRate"]
            report.setdefault(scenario, {}).setdefault(remediation, {}).update({
                "goodputChange": goodputChange, "p95Improvement": p95Change,
                "errorRateDelta": errorDelta,
            })
            print(f"{scenario:<8}{remediation:<18}{goodputChange:>+10.1%}{p95Change:>+10.1%}"
                  f"{errorDelta:>+13.1%}  {EXPECTED.get((scenario, remediation), '')}")
    print()

    # --- 판정 1: 정답이 상충하는가 ---
    def bestFor(scenario: str) -> str | None:
        """유의하게 개선한 조치. 없으면 None.

        ⚠️ 단순히 ``max`` 를 쓰면 안 된다. 모든 조치가 잡음 수준일 때도 그중 하나가
        "최선"으로 뽑혀 **정답이 상충하는 것처럼 보인다.** 실제로 첫 실행에서
        -0.4% 와 -1.0% 의 차이로 그런 오판이 나왔다. 개선이 문턱을 넘는 것만 센다.
        """
        rows = report.get(scenario, {})
        if not rows:
            return None
        # 에러율이 오른 조치는 후보에서 뺀다 — 빠른 실패로 얻은 지연 개선은 개선이 아니다.
        # NONE 은 기준선이므로 후보가 아니다. 하류 부하 표가 report 에 NONE 항목도
        # 넣으므로 mₐ 가 계산된 것만 고른다.
        viable = {name: row for name, row in rows.items()
                  if "errorRateDelta" in row
                  and row["errorRateDelta"] <= EFFECT_THRESHOLD
                  and row["goodputChange"] > EFFECT_THRESHOLD}
        if not viable:
            return None
        return max(viable, key=lambda name: viable[name]["goodputChange"])

    print("━━━ 판정 ━━━")
    bests = {scenario: bestFor(scenario) for scenario in report}
    for scenario, best in bests.items():
        print(f"  {scenario} 의 최선: {best or '없음 (모든 조치가 무조치보다 나쁨)'}")

    named = [b for b in bests.values() if b]
    distinct = len(set(named)) > 1 and len(named) == len(bests)
    if len(named) < len(bests):
        print("  ⚠️ 유의하게 개선하는 조치가 없는 시나리오가 있다.")
        print("     '정답이 상충한다'를 판정할 수 없다 — 먼저 조치가 실제로 효과를")
        print("     내는 구성을 만들어야 한다(주입 기전·조치 파라미터 점검).")
    if distinct:
        print("  ✅ 정답이 상충한다 — 시나리오마다 최선의 조치가 다르다.")
        print("     → 신뢰도만으로는 조치를 고를 수 없고 병목 유형 추정이 필요하다(§2-0).")
    else:
        print("  ⚠️ 정답이 상충하지 않는다 — 같은 조치가 두 시나리오 모두에서 최선이다.")
        print("     이 구성에서는 '조치를 고르는 문제'가 성립하지 않는다. 주입 강도나")
        print("     조치 파라미터(Bulkhead 한도·CB 임계)를 재조정해야 한다.")

    # --- 판정 2: 오선택이 무조치보다 나쁜가 (H5b 본체) ---
    harmful = []
    for (scenario, remediation), expectation in EXPECTED.items():
        row = report.get(scenario, {}).get(remediation)
        if row is None or "goodputChange" not in row:
            continue
        worseThanNothing = (row["goodputChange"] < -EFFECT_THRESHOLD
                            or row["errorRateDelta"] > EFFECT_THRESHOLD)
        if worseThanNothing:
            harmful.append((scenario, remediation, row))

    print()
    if harmful:
        print("  ✅ H5b 지지 — 무조치보다 나쁜 조치가 실재한다.")
        for scenario, remediation, row in harmful:
            print(f"     {scenario} + {remediation}: goodput {row['goodputChange']:+.1%},"
                  f" 에러율 {row['errorRateDelta']:+.1%}")
        print("     → '틀린 조치는 안 하느니만 못하다'가 실측으로 성립한다.")
        print()
        print("  ⚠️ mₐ 를 입구 goodput 하나로 재면 조치의 성격을 놓친다.")
        print("     Bulkhead 의 이득은 호출자 처리량이 아니라 **하류 부하 경감**으로")
        print("     나타난다 — 위의 '하류 부하' 표를 함께 읽어야 한다.")
    else:
        print("  ❌ H5b 미지지 — 어떤 조치도 무조치보다 나쁘지 않다.")
        print("     '조치를 고르는 것'의 가치가 '아무거나 해도 손해는 없다'로 떨어진다.")
        print("     먼저 볼 것: 재시도 설정(증폭이 안 일어나면 CB 역효과가 안 나온다),")
        print("     주입 강도, Bulkhead 한도.")

    payload = {
        "report": report,
        "best": bests,
        "answersConflict": distinct,
        "harmful": [{"scenario": s, "remediation": r, **row} for s, r, row in harmful],
        "h5bSupported": bool(harmful),
    }
    outputPath = cellsPath.with_name(cellsPath.name.replace("remediation-", "h5b-"))
    outputPath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
