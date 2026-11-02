"""결정 로그에 **노드 단위 정답 라벨**을 붙인다.

왜 필요했는가
=============

[[research/challenges.md]] J25·J26 에서 `κ` 와 주변화를 평가할 때 `p` 의 정답을
**에피소드 단위 창**(주입 전/후)으로 대체했다. 그것은 과대평가다 — **장애 구간이라도
그 노드가 영향받지 않았을 수 있다.** 실제로 `p̄ ∈ [0, 0.1)` 결정의 41.8% 가 장애
구간이었는데, 그 노드들이 진짜로 영향받지 않았다면 낮은 `p̄` 가 맞다. 그래서 그
수치를 쓰지 못했고, *"노드 단위 정답 라벨이 필요하다"* 를 게이트 ② 로 넘겼다.

**그런데 이미 구현돼 있었다.** [relabel.py](../chain/relabel.py) `labelEpisode` 가
노드·시각마다 라벨을 낸다.

    label = BOTTLENECK_BY_SCENARIO[scenario]  if isPropagated else 정상
    isPropagated = violates and precursor is not None

`violates` 는 `[t, t+Δ]` 안에 SLO 위반에 빠지는가, `precursor` 는 1-hop 상류에
`[t−W, t]` 선행 열화가 있는가다. **학습 라벨을 만든 바로 그 규칙**이므로 의사결정
계층을 평가하는 기준으로 내부적으로 일관된다.

`runBatch` 의 원장 항목은 `labelEpisode` 가 요구하는 필드를 **그대로** 갖고 있다
(`callGraph`·`injectedAt`·`scenario`·`injectionNode`·`normalSeconds`·`tracePath`).
그래서 셀마다 규칙을 다시 돌려 결정 로그와 접합할 수 있다. **머신도 재실행도
필요 없다.**

⚠️ **이것은 오라클이 아니라 규칙**이다. `latencyMultiplier`·`precursorMultiplier`
같은 임계가 들어 있고, 그 임계가 바뀌면 라벨이 바뀐다. 따라서 *"정답"* 은 **§4-2
가 정의한 `positive(v,t)` 에 대한 정답**이라는 뜻이며, 그 정의 자체의 타당성은
별개 문제다. 다만 **학습과 평가가 같은 정의를 쓴다**는 것이 중요하다 — 다른 정의를
쓰면 모델을 그 어긋남으로 벌주게 된다.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "chain"))

import comparePolicies as cmp  # noqa: E402
import relabel  # noqa: E402

# 결정 틱과 트레이스 표본의 시각이 정확히 같지 않다. 틱 주기가 5초이므로 그 절반을
# 허용 오차로 쓴다 — 더 넓히면 다른 틱의 라벨을 끌어온다.
MATCH_TOLERANCE_SECONDS = 2.5

NORMAL = relabel.NORMAL


def labelingArguments(**overrides) -> argparse.Namespace:
    """라벨링 파라미터. **`minimalObservation.py`·`marginalizeBottleneck.py` 와 같은
    기본값**을 쓴다 — 어긋나면 분석마다 다른 집합을 보게 된다."""
    values = {"horizonSeconds": 10.0, "precursorWindowSeconds": 30.0,
              "latencyMultiplier": 2.0, "errorRateThreshold": 0.05,
              "precursorMultiplier": 1.5}
    values.update(overrides)
    return argparse.Namespace(**values)


def truthIndex(cell: dict, arguments: argparse.Namespace) -> dict[str, tuple[list, list]]:
    """셀의 트레이스에 규칙을 돌려 `{노드: (시각들, 라벨들)}` 로 만든다."""
    tracePath = cmp.HERE / cell["tracePath"]
    if not tracePath.exists():
        return {}
    episode = {"callGraph": cell["callGraph"], "injectedAt": cell["injectedAt"],
               "scenario": cell["scenario"], "injectionNode": cell.get("injectionNode"),
               "normalSeconds": cell.get("normalSeconds", 40.0)}
    result = relabel.labelEpisode(episode, tracePath, arguments)
    byNode: dict[str, list[tuple[float, str]]] = collections.defaultdict(list)
    for row in result["labels"]:
        byNode[row["node"]].append((row["timestamp"], row["label"]))
    index = {}
    for node, rows in byNode.items():
        rows.sort()
        index[node] = ([r[0] for r in rows], [r[1] for r in rows])
    return index


def lookup(index: dict, node: str, timestamp: float) -> str | None:
    """가장 가까운 시각의 라벨. 허용 오차를 넘으면 `None` — 추측하지 않는다."""
    entry = index.get(node)
    if entry is None:
        return None
    times, labels = entry
    position = bisect.bisect_left(times, timestamp)
    best, bestGap = None, MATCH_TOLERANCE_SECONDS
    for candidate in (position - 1, position):
        if 0 <= candidate < len(times):
            gap = abs(times[candidate] - timestamp)
            if gap <= bestGap:
                best, bestGap = labels[candidate], gap
    return best


def annotate(cell: dict, arguments: argparse.Namespace) -> list[dict]:
    """셀의 결정마다 `truth` 를 붙인다. 접합이 안 된 결정은 `truth=None` 이다."""
    index = truthIndex(cell, arguments)
    rows = []
    for entry in cmp.allDecisions(cell):
        rows.append({**entry, "truth": lookup(index, entry.get("node"), entry["timestamp"])})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="결정 로그에 노드 단위 정답 라벨 접합")
    parser.add_argument("--ledger", default=str(cmp.RESULTS_DIR / "ledger.jsonl"))
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--digest", default=None)
    parser.add_argument("--policy", default=None)
    arguments = parser.parse_args()

    ledgerPath = pathlib.Path(arguments.ledger)
    entries = cmp.loadLedger(ledgerPath)
    latest: dict[str, str] = {}
    for entry in entries:
        path, stamp = entry.get("decisionPath"), entry.get("recordedAt") or ""
        if path and stamp >= latest.get(path, ""):
            latest[path] = stamp
    cells = [e for e in entries
             if e.get("decisionPath")
             and (e.get("recordedAt") or "") == latest.get(e["decisionPath"])]
    if arguments.scenario:
        cells = [c for c in cells if c.get("scenario") == arguments.scenario]
    if arguments.workers is not None:
        cells = [c for c in cells
                 if (c.get("entryLoad") or {}).get("workerCount") == arguments.workers]
    if arguments.digest:
        cells = [c for c in cells if cmp.cohortOf(c)[2] == arguments.digest]
    if arguments.policy:
        cells = [c for c in cells if c["policy"] == arguments.policy]
    if not cells:
        print("그 조건에 맞는, 결정 로그를 소유한 셀이 없다.")
        return 1

    labelArguments = labelingArguments()
    print(f"셀 {len(cells)} / 규칙 파라미터 {vars(labelArguments)}")

    rows = []
    for cell in cells:
        rows.extend(annotate(cell, labelArguments))
    matched = [r for r in rows if r["truth"] is not None]
    print(f"결정 {len(rows)} 건 / 접합 {len(matched)} 건 ({len(matched) / max(1, len(rows)):.1%})")
    if len(matched) < len(rows):
        print(f"  ⚠️ 접합 실패 {len(rows) - len(matched)} 건 — 허용 오차 "
              f"{MATCH_TOLERANCE_SECONDS}초 안에 트레이스 표본이 없었다")

    # ① 창 라벨과 노드 라벨이 얼마나 다른가 — 종전 분석의 오차를 수치로 본다.
    print(f"\n━━━ 창 라벨 vs 노드 라벨 ━━━")
    table = collections.Counter()
    for row in matched:
        cell = next(c for c in cells if row["timestamp"] >= 0)   # 창은 아래에서 다시 계산
        table[row["truth"]] += 1
    print("노드 라벨 분포: " + ", ".join(f"{k} {v}({v / len(matched):.1%})"
                                    for k, v in sorted(table.items())))

    byCell = collections.Counter()
    for cell in cells:
        injected = cell["injectedAt"]
        for row in annotate(cell, labelArguments):
            if row["truth"] is None:
                continue
            offset = row["timestamp"] - injected
            window = "정상창" if offset < -10 else ("장애창" if offset > 10 else "과도")
            byCell[(window, row["truth"])] += 1
    print(f"\n{'창':<8}{'노드=정상':>10}{'노드=S1':>9}{'노드=S3':>9}  해석")
    for window in ("정상창", "장애창"):
        normal = byCell[(window, NORMAL)]
        s1, s3 = byCell[(window, "S1")], byCell[(window, "S3")]
        total = normal + s1 + s3
        if not total:
            continue
        note = ("창 라벨이 과대평가한 부분 = 정상 "
                f"{normal / total:.1%}") if window == "장애창" else ""
        print(f"{window:<8}{normal:>10}{s1:>9}{s3:>9}  {note}")
    print("\n🔴 **장애창의 '노드=정상' 이 종전 분석이 과대평가한 양이다** — 그 결정들은")
    print("   장애 구간에 있었지만 **그 노드는 영향받지 않았다.** 보류가 정답이다.")

    # ② 예측기 캘리브레이션 — 이제 제대로 볼 수 있다.
    print(f"\n━━━ 캘리브레이션 (노드 라벨 기준) ━━━")
    print(f"{'p̄ 구간':<14}{'건':>6}{'실제 위반율':>12}{'p̄ 평균':>9}{'편차':>9}")
    bins = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
    for low, high in bins:
        subset = [r for r in matched if low <= r["meanRisk"] < high]
        if not subset:
            continue
        actual = sum(1 for r in subset if r["truth"] != NORMAL) / len(subset)
        mean = sum(r["meanRisk"] for r in subset) / len(subset)
        print(f"[{low:.2f},{high:.2f})   {len(subset):>6}{actual:>12.1%}{mean:>9.3f}"
              f"{actual - mean:>+9.3f}")
    print("  **편차 > 0 이면 과소평가(실제가 더 위험), < 0 이면 과신**이다.")
    print("  `κ` 는 과신을 보정하는 장치이므로 **θ 위 구간에서 편차가 음수여야** 이득이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
