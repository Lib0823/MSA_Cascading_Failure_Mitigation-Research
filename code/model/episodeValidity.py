"""에피소드 유효성 — 기준선이 참조로 쓸 만한가.

왜 필요한가 (2026-11-01 발견)
=============================

`buildDataset` 은 `f3`(처리량)·`f4`(지연)를 **에피소드 자기 정상 구간 기준선**으로
정규화한다. 그 기준선이 오염되면 두 feature 가 조용히 틀린다.

실제로 34 에피소드가 세 부하 수준에 걸쳐 있었다.

==  =====  ==============  ===========
워커  개수   기준선 p95       중앙값 대비
==  =====  ==============  ===========
32  18     134.2ms         1.00
64  10     357.9ms         2.67
128 6      604.0ms         4.50
==  =====  ==============  ===========

**부하 수준이 다른 것 자체는 결함이 아니다** — 에피소드별 정규화는 설계 의도이고
(모델이 절대값이 아니라 상대 열화를 배우게 한다), 64워커 스윕 안에서 지연 비율은
강도에 단조 증가했다(100ms 0.69 → 800ms 2.50).

**결함인 것은 장애 구간이 정상 구간보다 빠른 에피소드다.**

==========  =====  =============  ==========
시나리오     워커    기준선 → 장애    비
==========  =====  =============  ==========
s3 100ms    64     357.9 → 246.1  0.69
s3 200ms    64     357.9 → 357.9  1.00
s1 hog10    64     357.9 → 268.4  0.75
s1 hog14    64     357.9 → 357.9  1.00
==========  =====  =============  ==========

장애를 주입했는데 **느려지지 않았다면** 그 정상 구간은 깨끗한 참조가 아니다.
흡수된 장애(비 > 1 이지만 임계 미달)와는 다르다 — 그쪽은 기준선이 방향으로는
정상이므로 *"장애가 흡수됐다"* 가 사실이고 라벨이 정상인 것도 맞다.

왜 라벨 수로 걸러지지 않는가
============================

*"장애 라벨이 0개"* 를 기준으로 쓰면 **흡수된 장애까지 버린다.** 그것은 버릴
데이터가 아니다. 반대로 비 ≤ 1 인 에피소드는 라벨이 아니라 **feature 가**
오염되므로, 라벨을 정상으로 바꿔 쓰는 것으로도 구제되지 않는다. 그래서 판정
기준을 **기준선의 참조 적격성**에 둔다.

이 모듈은 판정만 하고 버리지 않는다 — 제외는 호출자가 명시적으로 한다.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

# 기준선이 참조로 쓸 수 없다고 보는 경계. 1.0 은 "장애가 전혀 느리지 않았다" 다.
# 여유를 두지 않는다 — 1.0 을 넘기면 방향은 맞으므로 흡수된 장애로 본다.
INVALID_RATIO = 1.0


def episodeRecords(chainDir: pathlib.Path) -> list[dict]:
    """`loadAllEpisodes` 와 **같은 발견 규칙**을 쓴다.

    규칙이 어긋나면 분석이 보는 집합과 검증이 보는 집합이 달라져, 검증을
    통과한 것처럼 보이면서 오염된 에피소드가 분석에 남는다.
    """
    found = []
    for path in sorted((chainDir / "results").glob("*.json")):
        if not (path.name.startswith("episodes-") or path.name.startswith("remediation-")):
            continue
        for record in json.loads(path.read_text(encoding="utf-8")):
            if record.get("injectedAt") is None:
                continue
            if not (chainDir / record["tracePath"]).exists():
                continue   # 중단된 실행이 남긴 고아 기록
            found.append(record)
    return found


def windowMedians(chainDir: pathlib.Path, record: dict) -> tuple[float, float]:
    """정상 구간과 장애 구간의 p95 중앙값. 노드 전체를 합쳐 본다."""
    tracePath = chainDir / record["tracePath"]
    injectedAt = record["injectedAt"]
    normalStart = injectedAt - float(record.get("normalSeconds", 40.0))
    normal, fault = [], []
    for line in tracePath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        value = sample.get("latencyP95", float("nan"))
        if value is None or math.isnan(value):
            continue
        if normalStart <= sample["timestamp"] < injectedAt:
            normal.append(value)
        elif sample["timestamp"] >= injectedAt:
            fault.append(value)
    if not normal or not fault:
        return float("nan"), float("nan")
    return float(np.median(normal)), float(np.median(fault))


def judge(chainDir: pathlib.Path) -> list[dict]:
    """에피소드별 판정. `valid=False` 는 기준선을 참조로 쓸 수 없다는 뜻이다."""
    verdicts = []
    for record in episodeRecords(chainDir):
        baseline, faultLevel = windowMedians(chainDir, record)
        scenario = record.get("scenario")
        ratio = (faultLevel / baseline) if baseline else float("nan")
        if scenario == "normal":
            valid, reason = True, "대조군 — 주입이 없어 비를 보지 않는다"
        elif math.isnan(ratio):
            valid, reason = False, "정상 또는 장애 구간에 표본이 없다"
        elif ratio <= INVALID_RATIO:
            valid, reason = False, f"장애가 느려지지 않았다(비 {ratio:.2f}) — 기준선이 참조로 부적격"
        else:
            valid, reason = True, f"비 {ratio:.2f}"
        verdicts.append({
            "tracePath": record["tracePath"], "scenario": scenario,
            "intensityLabel": record.get("intensityLabel"),
            "workerCount": (record.get("entryLoad") or {}).get("workerCount"),
            "baseline": baseline, "faultLevel": faultLevel, "ratio": ratio,
            "valid": valid, "reason": reason,
        })
    return verdicts


def invalidTracePaths(chainDir: pathlib.Path) -> set[str]:
    """제외 대상의 `tracePath` 집합. 분석 쪽에서 쓰기 쉬운 형태로 준다."""
    return {v["tracePath"] for v in judge(chainDir) if not v["valid"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="에피소드 유효성 판정")
    parser.add_argument("--chainDir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "chain"))
    arguments = parser.parse_args()

    chainDir = pathlib.Path(arguments.chainDir).resolve()
    verdicts = judge(chainDir)
    if not verdicts:
        print("에피소드가 없다.")
        return 1

    print(f"에피소드 {len(verdicts)}개\n")
    print(f"{'시나리오':<9}{'강도':<9}{'워커':>5}{'기준선':>10}{'장애':>10}{'비':>7}  판정")
    for verdict in sorted(verdicts, key=lambda v: (str(v["scenario"]), str(v["intensityLabel"]))):
        mark = "✅" if verdict["valid"] else "🔴"
        print(f"{str(verdict['scenario']):<9}{str(verdict['intensityLabel'] or '-'):<9}"
              f"{str(verdict['workerCount'] or '-'):>5}{verdict['baseline']:>10.1f}"
              f"{verdict['faultLevel']:>10.1f}{verdict['ratio']:>7.2f}  {mark} {verdict['reason']}")

    invalid = [v for v in verdicts if not v["valid"]]
    print(f"\n유효 {len(verdicts) - len(invalid)} / 무효 {len(invalid)}")
    if invalid:
        print("\n🔴 **무효 에피소드는 기준선이 참조로 부적격이다.** 라벨만 틀린 것이 "
              "아니라 `f3`·`f4` 가 그 기준선으로 정규화되므로 **feature 가 틀린다.**")
        for verdict in invalid:
            print(f"    {verdict['tracePath']}")

    # 부하 수준 분포 — 무효가 특정 수준에 쏠렸는지 본다.
    print(f"\n{'워커':<7}{'개수':>5}{'기준선 중앙값':>14}{'무효':>6}")
    levels: dict = {}
    for verdict in verdicts:
        levels.setdefault(verdict["workerCount"], []).append(verdict)
    allBaselines = [v["baseline"] for v in verdicts if not math.isnan(v["baseline"])]
    corpusMedian = float(np.median(allBaselines)) if allBaselines else float("nan")
    for workers, group in sorted(levels.items(), key=lambda kv: (kv[0] is None, kv[0])):
        baselines = [v["baseline"] for v in group if not math.isnan(v["baseline"])]
        broken = sum(1 for v in group if not v["valid"])
        print(f"{str(workers):<7}{len(group):>5}{np.median(baselines):>14.1f}{broken:>6}")
    print(f"코퍼스 기준선 중앙값 {corpusMedian:.1f}ms")
    print("\n⚠️ **부하 수준이 다른 것 자체는 결함이 아니다** — 에피소드별 정규화가 "
          "설계 의도다. 결함은 위 🔴 뿐이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
