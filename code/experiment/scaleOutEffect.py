"""Scale-out 의 `Dₐ`·`mₐ`·`ℓₐ` — K8s 반복 측정 분석 (게이트 ②, J31).

왜 별도 스크립트인가
====================

`Dₐ` 는 [disruptionByAction.py](disruptionByAction.py) 가, `mₐ` 는
[compareRemediation.py](../chain/compareRemediation.py) 가 잰다. 둘 다 **조건마다 셀이
하나**라는 전제로 짜여 있다 — 후자는 `(시나리오, 조치)` 로 사전을 만들어 반복이 있으면
마지막 셀만 남긴다. K8s 배치는 조건마다 반복이 있으므로 산포와 구간을 함께 내야 한다.

구간은 두 스크립트와 **같게** 둔다 — Compose 측 값과 비교 가능해야 한다.

====  ===================  ==============================================
값     구간 (injectedAt+)    지표 (gateway)
====  ===================  ==============================================
Dₐ    +10 ~ +70 s          throughputRps 중앙값 (disruptionByAction 과 동일)
mₐ    +15 ~ +70 s          goodput = throughput × (1 − errorRate)
====  ===================  ==============================================

⚠️ `mₐ` 구간에 **상한을 둔다**(compareRemediation 은 상한이 없다). S1×SCALE_OUT 셀은
부하가 끝난 뒤에도 5~8초 동안 2rps 안팎의 꼬리 행이 트레이스에 남는다 — 상한 없이
재면 그 행이 중앙값을 끌어내린다.

기전 증거
=========

노드별 트레이스는 NodePort 로 긁으므로 레플리카가 여럿이면 **파드 하나만** 본다. 증설의
기전은 `podTraffic`(파드 안에서 직접 센 `/work` 수, 상태코드 무관)으로 본다. 신규 파드는
DB 커넥션 예산의 **남은 슬롯**만 쥘 수 있고, 포화된 파드의 처리량은 쥔 커넥션 수 ×
커넥션당 처리율이므로 처리량에서 커넥션 수를 역산할 수 있다. 커넥션당 처리율은 같은
배치의 S1×NONE 에서 얻는다 — 그 셀에서 payment 가 쥘 수 있는 슬롯은 산술로 정해진다
(`max_connections − superuser_reserved − 점유 세션`).

통계
====

조건마다 n≈5 다. 평균 차이의 Welch t 구간을 대조 평균으로 나눠 상대 효과의 구간으로
쓴다(대조의 변동계수를 함께 출력한다 — 작으면 분모의 불확실성은 무시할 만하다).
"효과 없음"은 p 값이 아니라 **동등성 검정(TOST)** 으로 판정한다 — 90% 구간이
±`EQUIVALENCE_MARGIN` 안에 들어야 한다. 마진은 compareRemediation 의
`EFFECT_THRESHOLD`(의미 있는 차이의 하한, 0.10)와 같다.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics

from scipy import stats

from disruptionByAction import CHAIN_DIR, ENTRY_NODE, loadCells, thresholdFor, windowValues

RESULTS_DIR = pathlib.Path(__file__).resolve().parent / "results"
DEFAULT_LEDGER = CHAIN_DIR / "results" / "remediation-ledger.jsonl"

DISRUPTION_WINDOW = (10.0, 70.0)    # disruptionByAction 기본값과 같다
MITIGATION_WINDOW = (15.0, 70.0)    # compareRemediation 의 +15s, 상한은 부하 종료 시점
POD_WINDOW = (20.0, 70.0)           # 증설 후 레플리카가 Ready 가 되고 연결이 재분배된 뒤
EQUIVALENCE_MARGIN = 0.10           # compareRemediation.EFFECT_THRESHOLD 와 같다
ONSET_RPS = 5.0                     # 신규 파드 합계가 이것을 넘으면 "효과가 닿기 시작"

# runRemediation 이 cell["config"] 를 남기기 전의 셀은 기본값으로 돌았다.
DEFAULT_CONFIG = {"maxConnections": 30, "hogSessions": 24}
# deploy/k8s/parity/compose-parity.yaml 의 postgres 인자와 같아야 한다.
SUPERUSER_RESERVED = 3
# 앱 풀 최대 — compose-parity.yaml HIKARI_MAX_POOL_SIZE 기본값.
POOL_MAX = 20


def configOf(cell: dict) -> dict:
    return {**DEFAULT_CONFIG, **(cell.get("config") or {})}


def entryWindow(cell: dict, low: float, high: float) -> dict:
    """입구 노드의 구간 지표. compareRemediation 과 같이 중앙값끼리 곱해 goodput 을 낸다."""
    def median(key: str) -> float:
        values = windowValues(cell, ENTRY_NODE, low, high, key)
        return statistics.median(values) if values else float("nan")

    throughput = median("throughputRps")
    errorRate = median("errorRate")
    return {"throughputRps": throughput, "errorRate": errorRate,
            "goodputRps": throughput * (1.0 - errorRate),
            "latencyP95Millis": median("latencyP95")}


def describe(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    half = stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "mean": mean, "sd": sd, "cv": sd / mean if mean else float("nan"),
            "median": statistics.median(values), "min": min(values), "max": max(values),
            "ci95": [mean - half, mean + half], "values": values}


def relativeEffect(treatment: list[float], control: list[float]) -> dict:
    """(평균_처치 − 평균_대조) / 평균_대조 와 그 Welch 구간·p·동등성 판정."""
    n1, n2 = len(treatment), len(control)
    m1, m2 = statistics.fmean(treatment), statistics.fmean(control)
    v1 = statistics.variance(treatment) if n1 > 1 else 0.0
    v2 = statistics.variance(control) if n2 > 1 else 0.0
    se = math.sqrt(v1 / n1 + v2 / n2)
    diff = m1 - m2
    if se == 0.0:
        ci95 = ci90 = (diff, diff)
        pValue = 0.0 if diff else 1.0
        df = float("inf")
    else:
        df = (v1 / n1 + v2 / n2) ** 2 / (
            ((v1 / n1) ** 2 / (n1 - 1) if n1 > 1 else 0.0)
            + ((v2 / n2) ** 2 / (n2 - 1) if n2 > 1 else 0.0))
        t95, t90 = stats.t.ppf(0.975, df), stats.t.ppf(0.95, df)
        ci95 = (diff - t95 * se, diff + t95 * se)
        ci90 = (diff - t90 * se, diff + t90 * se)
        pValue = float(stats.ttest_ind(treatment, control, equal_var=False).pvalue)
    exact = stats.mannwhitneyu(treatment, control, alternative="two-sided", method="exact")
    # scipy 가 numpy 스칼라를 돌려준다 — JSON 에 쓰려면 파이썬 값이어야 한다.
    relative90 = [float(c / m2) for c in ci90]
    return {
        "relative": diff / m2, "relativeCi95": [float(c / m2) for c in ci95],
        "relativeCi90": relative90, "welchDf": float(df), "welchP": pValue,
        "mannWhitneyExactP": float(exact.pvalue),
        # TOST: 90% 구간이 ±마진 안 → α=0.05 에서 "마진보다 큰 효과는 없다"
        "equivalentWithin": EQUIVALENCE_MARGIN,
        "equivalent": bool(-EQUIVALENCE_MARGIN < relative90[0]
                           and relative90[1] < EQUIVALENCE_MARGIN),
    }


def originalPods(cell: dict) -> set[str]:
    """주입(=증설) 직전 표본에 있던 파드 — 그 뒤에 나타난 파드가 신규다."""
    before = [s for s in cell.get("podTraffic") or [] if s["timestamp"] < cell["injectedAt"]]
    return set(before[-1]["rps"]) if before else set()


def podRates(cell: dict, low: float, high: float) -> dict[str, float]:
    samples = [s for s in cell.get("podTraffic") or []
               if low <= s["timestamp"] - cell["injectedAt"] < high]
    pods = {p for s in samples for p in s["rps"]}
    return {p: statistics.median([s["rps"].get(p, 0.0) for s in samples]) for p in pods}


def podConnections(cell: dict, low: float, high: float) -> dict[str, float]:
    """파드별 DB 커넥션 수 중앙값 — `dbConnections` 가 있는 셀만(J31 이후)."""
    samples = [s for s in cell.get("dbConnections") or []
               if low <= s["timestamp"] - cell["injectedAt"] < high]
    keys = {k for s in samples for k in s["connections"]}
    return {k: statistics.median([s["connections"].get(k, 0) for s in samples]) for k in keys}


def effectOnset(cell: dict) -> float | None:
    """증설 명령부터 신규 파드 합계 처리량이 `ONSET_RPS` 를 넘은 첫 표본까지(초).

    `ℓₐ` 의 정의는 *"조치 트리거 → 효과 발현"* 이다(§2-B). `leadTimeSeconds` 는 Ready 까지만
    잰다 — Ready 여도 트래픽이 닿지 않으면 효과는 없다. 표본 주기가 5초이므로 해상도도 5초다.
    """
    scaledAt = (cell.get("scaleOut") or {}).get("scaledAt")
    if scaledAt is None:
        return None
    original = originalPods(cell)
    for sample in cell.get("podTraffic") or []:
        if sample["timestamp"] <= scaledAt:
            continue
        fresh = sum(v for p, v in sample["rps"].items() if p not in original)
        if fresh > ONSET_RPS:
            return sample["timestamp"] - scaledAt
    return None


def spearman(x: list[float], y: list[float]) -> dict:
    if len(x) < 3:
        return {"rho": float("nan"), "p": float("nan"), "n": len(x)}
    result = stats.spearmanr(x, y)
    return {"rho": float(result.statistic), "p": float(result.pvalue), "n": len(x)}


def analyze(cells: list[dict], maxForeignCpu: float) -> dict:
    byCondition: dict[tuple, list[dict]] = collections.defaultdict(list)
    for cell in cells:
        budget = configOf(cell)["maxConnections"]
        byCondition[(budget, cell["scenario"], cell["remediation"])].append(cell)

    perCell = {}
    for key, group in byCondition.items():
        for cell in group:
            perCell[cell["cellKey"] + f"|mc{key[0]}"] = {
                "condition": list(key), "repeat": cell.get("repeat"),
                "disruptionWindow": entryWindow(cell, *DISRUPTION_WINDOW),
                "mitigationWindow": entryWindow(cell, *MITIGATION_WINDOW),
                "foreignCpuMedian": (cell.get("foreignCpu") or {}).get("percentMedian"),
                "hostLoadMedian": (cell.get("hostLoad") or {}).get("loadMedian"),
                "hostSaturatedShare": (cell.get("hostLoad") or {}).get("saturatedShare"),
                "nodeCpuMedianCores": (cell.get("nodeCpu") or {}).get("usageMedianCores"),
                "readyLeadSeconds": (cell.get("scaleOut") or {}).get("leadTimeSeconds"),
                "effectOnsetSeconds": effectOnset(cell),
                "podRates": podRates(cell, *POD_WINDOW),
                "podConnections": podConnections(cell, *POD_WINDOW),
                "originalPods": sorted(originalPods(cell)),
            }

    def metric(entry: dict, scenario: str) -> float:
        # normal 은 Dₐ(처리량), 장애 시나리오는 mₐ(goodput) 의 지표를 쓴다.
        return (entry["disruptionWindow"]["throughputRps"] if scenario == "normal"
                else entry["mitigationWindow"]["goodputRps"])

    def values(budget: int, scenario: str, remediation: str, clean: bool = False) -> list[float]:
        out = []
        for entry in perCell.values():
            if entry["condition"] != [budget, scenario, remediation]:
                continue
            if clean and (entry["foreignCpuMedian"] or 0.0) > maxForeignCpu:
                continue
            out.append(metric(entry, scenario))
        return out

    report: dict = {"conditions": {}, "effects": {}, "cells": perCell}
    for (budget, scenario, remediation) in sorted(byCondition):
        vals = values(budget, scenario, remediation)
        entries = [e for e in perCell.values() if e["condition"] == [budget, scenario, remediation]]
        window = "disruptionWindow" if scenario == "normal" else "mitigationWindow"
        p95 = [e[window]["latencyP95Millis"] for e in entries]
        errors = [e[window]["errorRate"] for e in entries]
        report["conditions"][f"mc{budget}|{scenario}|{remediation}"] = {
            "metric": "throughputRps" if scenario == "normal" else "goodputRps",
            **describe(vals),
            "latencyP95MedianMillis": statistics.median(p95),
            "errorRateMedian": statistics.median(errors),
        }

    budgets = sorted({k[0] for k in byCondition})
    for budget in budgets:
        for scenario in sorted({k[1] for k in byCondition if k[0] == budget}):
            control = values(budget, scenario, "NONE")
            treatment = values(budget, scenario, "SCALE_OUT")
            if len(control) < 2 or len(treatment) < 2:
                continue
            effect = relativeEffect(treatment, control)
            cleanControl = values(budget, scenario, "NONE", clean=True)
            cleanTreatment = values(budget, scenario, "SCALE_OUT", clean=True)
            if len(cleanControl) >= 2 and len(cleanTreatment) >= 2:
                cleanEffect = relativeEffect(cleanTreatment, cleanControl)
                effect["cleanSubset"] = {
                    "maxForeignCpu": maxForeignCpu, "nControl": len(cleanControl),
                    "nTreatment": len(cleanTreatment), "relative": cleanEffect["relative"],
                    "relativeCi95": cleanEffect["relativeCi95"]}
            if scenario == "normal":
                effect["disruption"] = max(0.0, -effect["relative"])
                # disruptionByAction 과 같은 방식(조건별 중앙값의 비)도 함께 남긴다.
                effect["disruptionMedianBased"] = max(
                    0.0, 1.0 - statistics.median(treatment) / statistics.median(control))
            else:
                effect["mitigation"] = effect["relative"]
                effect["mitigationMedianBased"] = (
                    statistics.median(treatment) / statistics.median(control) - 1.0)
            report["effects"][f"mc{budget}|{scenario}"] = effect

        # 커넥션당 처리율 — S1×NONE 의 payment 가 쥔 슬롯은 산술로 정해진다.
        s1None = [e for e in perCell.values() if e["condition"] == [budget, "s1", "NONE"]]
        hog = DEFAULT_CONFIG["hogSessions"]
        s1Slots = budget - SUPERUSER_RESERVED - hog
        if s1None and s1Slots > 0:
            podTotals = [sum(e["podRates"].values()) for e in s1None if e["podRates"]]
            if podTotals:
                perConnection = statistics.median(podTotals) / s1Slots
                report.setdefault("mechanism", {})[f"mc{budget}"] = {
                    "s1AppSlots": s1Slots, "perConnectionRps": perConnection,
                    "leftoverSlotsForNewPods": budget - SUPERUSER_RESERVED - POOL_MAX,
                }

    # 오염 점검 — 조건 중앙값에서 벗어난 정도가 외부 CPU 와 함께 움직이는가.
    deviations, foreign, load = [], [], []
    for entry in perCell.values():
        budget, scenario, remediation = entry["condition"]
        center = statistics.median(values(budget, scenario, remediation))
        if not center or entry["foreignCpuMedian"] is None:
            continue
        deviations.append(metric(entry, scenario) / center - 1.0)
        foreign.append(entry["foreignCpuMedian"])
        load.append(entry["hostLoadMedian"] or float("nan"))
    report["contamination"] = {
        "foreignCpuVsDeviation": spearman(foreign, deviations),
        "hostLoadVsDeviation": spearman(load, deviations),
        "foreignCpuRange": [min(foreign), max(foreign)] if foreign else None,
    }
    return report


def composeBaseline(ledgerPath: pathlib.Path) -> dict | None:
    """같은 구간의 Compose normal×NONE — 백엔드 등가성 확인용."""
    for cell in loadCells(ledgerPath):
        if (cell.get("backend", "compose") == "compose" and cell.get("scenario") == "normal"
                and cell.get("remediation") == "NONE"):
            window = entryWindow(cell, *DISRUPTION_WINDOW)
            return {"cellKey": cell.get("cellKey"), "throughputRps": window["throughputRps"],
                    "latencyP95Millis": window["latencyP95Millis"],
                    "goodputWholeCell": (cell.get("entryLoad") or {}).get("goodputRps")}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Scale-out Dₐ·mₐ·ℓₐ (K8s 반복 측정)")
    parser.add_argument("--cells", required=True, action="append",
                        help="runRemediation --backend k8s 가 남긴 k8s-remediation-*.json (여러 번 줄 수 있다)")
    parser.add_argument("--maxForeignCpu", type=float, default=100.0,
                        help="민감도 분석 — 셀 중앙 외부 CPU 가 이보다 큰 셀을 뺀 부분집합")
    parser.add_argument("--reversibility", type=float, default=0.7,
                        help="Rₐ — 정책 파라미터. 엔진의 SCALE_OUT 가정값(0.7)이 기본이다")
    parser.add_argument("--availabilityLoss", type=float, default=1.0, help="L")
    parser.add_argument("--composeLedger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", help="요약 JSON. 기본은 results/scaleOutEffect-<배치 스탬프>.json")
    arguments = parser.parse_args()

    cells = []
    for path in arguments.cells:
        loaded = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        cells.extend(c for c in loaded if c.get("backend") == "k8s")
    if not cells:
        print("❌ K8s 셀이 없다")
        return 1
    report = analyze(cells, arguments.maxForeignCpu)
    report["inputs"] = [str(pathlib.Path(p).resolve().relative_to(CHAIN_DIR.parent.parent))
                        if pathlib.Path(p).resolve().is_relative_to(CHAIN_DIR.parent.parent)
                        else p for p in arguments.cells]
    report["windows"] = {"disruption": DISRUPTION_WINDOW, "mitigation": MITIGATION_WINDOW,
                         "pods": POD_WINDOW}
    report["composeBaseline"] = composeBaseline(pathlib.Path(arguments.composeLedger))

    print(f"셀 {len(cells)}개 / 구간 Dₐ +{DISRUPTION_WINDOW[0]:.0f}~+{DISRUPTION_WINDOW[1]:.0f}s"
          f" · mₐ +{MITIGATION_WINDOW[0]:.0f}~+{MITIGATION_WINDOW[1]:.0f}s (gateway)\n")
    print(f"{'조건':<26}{'n':>3}{'평균':>9}{'sd':>7}{'CV':>7}{'최소':>8}{'최대':>8}{'p95':>7}")
    for name, row in report["conditions"].items():
        print(f"{name:<26}{row['n']:>3}{row['mean']:>9.1f}{row['sd']:>7.1f}{row['cv']:>7.1%}"
              f"{row['min']:>8.1f}{row['max']:>8.1f}{row['latencyP95MedianMillis']:>7.0f}")

    baseline = report["composeBaseline"]
    k8sNone = report["conditions"].get("mc30|normal|NONE")
    if baseline and k8sNone:
        gap = k8sNone["mean"] / baseline["throughputRps"] - 1.0
        print(f"\n등가성: Compose normal×NONE {baseline['throughputRps']:.1f} rps (같은 구간, 셀 1개)"
              f" vs K8s 평균 {k8sNone['mean']:.1f} → {gap:+.1%}")

    print("\n━━━ 효과 (SCALE_OUT vs NONE, 평균 기준, Welch) ━━━")
    for name, effect in report["effects"].items():
        low, high = effect["relativeCi95"]
        label = "Dₐ" if "disruption" in effect else "mₐ"
        value = effect.get("disruption", effect.get("mitigation"))
        line = (f"{name:<14}{label} = {value:+.3f}  (상대 변화 {effect['relative']:+.1%},"
                f" 95% [{low:+.1%}, {high:+.1%}], Welch p={effect['welchP']:.2g},"
                f" 정확 MW p={effect['mannWhitneyExactP']:.3f})")
        print(line)
        if "mitigation" in effect:
            verdict = "✅ 동등" if effect["equivalent"] else "— 동등 판정 불가"
            low90, high90 = effect["relativeCi90"]
            print(f"{'':<14}TOST ±{EQUIVALENCE_MARGIN:.0%}: 90% [{low90:+.1%}, {high90:+.1%}] {verdict}")
        clean = effect.get("cleanSubset")
        if clean:
            low, high = clean["relativeCi95"]
            print(f"{'':<14}외부 CPU ≤ {clean['maxForeignCpu']:.0f}% 셀만"
                  f" (대조 {clean['nControl']}/처치 {clean['nTreatment']}):"
                  f" {clean['relative']:+.1%} [{low:+.1%}, {high:+.1%}]")

    print(f"\n━━━ θ (R={arguments.reversibility}, L={arguments.availabilityLoss}) ━━━")
    for budget in sorted({e["condition"][0] for e in report["cells"].values()}):
        normal = report["effects"].get(f"mc{budget}|normal")
        if not normal:
            continue
        for scenario in ("s1", "s3"):
            fault = report["effects"].get(f"mc{budget}|{scenario}")
            if not fault:
                continue
            theta = thresholdFor(normal["disruption"], fault["mitigation"],
                                 arguments.reversibility, arguments.availabilityLoss)
            needed = normal["disruption"] * (1.0 - arguments.reversibility) / arguments.availabilityLoss
            # 분모 mₐL − Dₐ(1−R) > 0 ⇔ R > 1 − mₐL/Dₐ. Rₐ 는 정책 파라미터라(§4-5) 이 경계를 함께 낸다.
            reversibilityNeeded = (1.0 - fault["mitigation"] * arguments.availabilityLoss
                                   / normal["disruption"]) if normal["disruption"] > 0 else None
            fault["theta"] = None if math.isinf(theta) else theta
            fault["mitigationNeededForFiniteTheta"] = needed
            fault["reversibilityNeededForFiniteTheta"] = reversibilityNeeded
            shown = "∞ (분모 ≤ 0 → 자동 배제)" if math.isinf(theta) else f"{theta:.3f}"
            print(f"mc{budget} {scenario}: Dₐ {normal['disruption']:.3f} · mₐ {fault['mitigation']:+.3f}"
                  f" → θ = {shown}")
            if reversibilityNeeded is not None:
                print(f"{'':<10}θ 가 유한하려면 mₐ > {needed:.3f} (이 R 에서)"
                      f" 또는 R > {reversibilityNeeded:.3f} (이 mₐ 에서)")

    print("\n━━━ ℓₐ — Ready 까지 vs 트래픽이 닿기까지 ━━━")
    for name, entry in report["cells"].items():
        if entry["condition"][2] != "SCALE_OUT":
            continue
        onset = entry["effectOnsetSeconds"]
        shown = f"{onset:5.1f}s" if onset is not None else "  없음 (창 안에 트래픽 0)"
        print(f"{name:<34} Ready {entry['readyLeadSeconds']}s / 트래픽 {shown}")

    mechanism = report.get("mechanism", {})
    print("\n━━━ 기전 — 파드별 처리율 (+20~+70s 중앙값) ━━━")
    for name, entry in report["cells"].items():
        if entry["condition"][2] != "SCALE_OUT":
            continue
        info = mechanism.get(f"mc{entry['condition'][0]}")
        original = set(entry["originalPods"])
        parts, implied = [], 0.0
        measured = entry["podConnections"]
        for pod, rate in sorted(entry["podRates"].items(), key=lambda kv: (kv[0] not in original, kv[0])):
            tag = "원" if pod in original else "신"
            db = f" (DB {measured[pod]:.0f})" if pod in measured else ""
            parts.append(f"{tag} {rate:5.1f}{db}")
            if pod not in original and info:
                implied += rate / info["perConnectionRps"]
        tail = (f" | 신규 합계 ≈ 커넥션 {implied:.1f}개 (남은 슬롯 {info['leftoverSlotsForNewPods']})"
                if info else "")
        if "local" in measured:
            tail += f" | 점유 세션 {measured['local']:.0f}"
        print(f"{name:<34} {' / '.join(parts)}{tail}")
    for budget, info in mechanism.items():
        print(f"  {budget}: 커넥션당 {info['perConnectionRps']:.1f} rps"
              f" (S1×NONE payment 처리율 ÷ 슬롯 {info['s1AppSlots']})")

    contamination = report["contamination"]
    fc, hl = contamination["foreignCpuVsDeviation"], contamination["hostLoadVsDeviation"]
    print("\n━━━ 오염 점검 — 조건 중앙값 대비 편차 ━━━")
    print(f"외부 CPU {contamination['foreignCpuRange'][0]:.0f}~{contamination['foreignCpuRange'][1]:.0f}%"
          f" vs 편차: Spearman ρ={fc['rho']:+.2f} (p={fc['p']:.2f}, n={fc['n']})")
    print(f"호스트 부하 vs 편차:             Spearman ρ={hl['rho']:+.2f} (p={hl['p']:.2f}, n={hl['n']})")

    output = pathlib.Path(arguments.output) if arguments.output else None
    if output is None:
        stamp = pathlib.Path(arguments.cells[0]).stem.removeprefix("k8s-remediation-")
        output = RESULTS_DIR / f"scaleOutEffect-{stamp}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
