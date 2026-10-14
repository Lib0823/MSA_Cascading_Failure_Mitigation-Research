"""사슬 트레이스 → 학습 데이터셋.

게이트 ① 은 랜덤 feature 로 모델이 "돈다"는 것만 확인했다. 여기서는
[code/chain/](../chain/) 이 만든 **실제 지표 시계열**에 §4-2 역라벨링을 적용해
①→② 비교에 쓸 데이터를 만든다.

의미 기준 공통 슬롯 6종(§2-A)으로의 사상
=========================================

==  ==========================  ==========================================
슬롯  의미                        이 사슬에서의 원시 지표
==  ==========================  ==========================================
f1  상태성 자원 포화율           ``poolActive / poolTotal``
f2  큐 대기율                   ``poolPending / (poolPending + poolActive)``
f3  처리량 대비 용량비           ``throughputRps / 정상 구간 최대``
f4  지연 분위수(정규화)          ``latencyP95 / 정상 구간 기준선``
f5  자원 사용률                 **결손 — 수집하지 않는다**
f6  에러율                      ``errorRate``
==  ==========================  ==========================================

⚠️ **f5(자원 사용률)가 비어 있다.** 컨테이너 CPU·메모리를 수집하지 않기 때문이며,
K8s 에서는 cAdvisor/metrics-server 가 제공하므로 게이트 ② 에서 채워진다. 지금은
0 으로 채우고 그 사실을 기록한다 — 조용히 빼면 ①②③ 의 입력이 달라져 비교가 오염된다.

⚠️ **passthrough 노드는 f1·f2 가 구조적으로 없다.** 커넥션 풀이 없기 때문이다.
이것은 결손이 아니라 **사실**이며, S1 과 S3 를 자기 지표만으로 구분할 수 없었던
이유와 같다(§4-4). 0 으로 채우되 노드 타입 임베딩이 그 차이를 흡수한다.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "chain"))

import relabel  # noqa: E402

SLOT_NAMES = ("f1", "f2", "f3", "f4", "f5", "f6")
NUM_SLOTS = len(SLOT_NAMES)
WINDOW_SAMPLES = 10          # 1Hz 수집이므로 10초
NUM_WINDOW_STATS = 3         # 평균·slope·표준편차
SELF_DIM = NUM_SLOTS * (1 + NUM_WINDOW_STATS)   # 24
AGGREGATION_HOPS = 2
CLASS_ORDER = ("정상", "S1", "S3")


def safeRatio(numerator: float, denominator: float) -> float:
    if denominator is None or denominator == 0 or math.isnan(denominator):
        return 0.0
    if numerator is None or math.isnan(numerator):
        return 0.0
    return float(np.clip(numerator / denominator, 0.0, 1.0))


def toSlots(record: dict, baseline: dict) -> np.ndarray:
    """한 표본을 공통 슬롯 6종으로 사상한다. 전부 [0,1] 정규화된다."""
    poolActive = record.get("poolActive", float("nan"))
    poolTotal = record.get("poolTotal", float("nan"))
    poolPending = record.get("poolPending", float("nan"))

    f1 = safeRatio(poolActive, poolTotal)
    waiting = (poolPending if not math.isnan(poolPending) else 0.0)
    serving = (poolActive if not math.isnan(poolActive) else 0.0)
    f2 = safeRatio(waiting, waiting + serving)
    f3 = safeRatio(record.get("throughputRps", float("nan")), baseline.get("maxThroughput"))
    # 지연은 기준선 대비 배수이므로 1 을 넘을 수 있다. 4배에서 포화시킨다.
    latency = record.get("latencyP95", float("nan"))
    base = baseline.get("latencyP95")
    f4 = 0.0 if (math.isnan(latency) or not base) else float(np.clip(latency / base / 4.0, 0.0, 1.0))
    f5 = 0.0   # 결손 — 컨테이너 자원 사용률 미수집
    errorRate = record.get("errorRate", float("nan"))
    f6 = 0.0 if math.isnan(errorRate) else float(np.clip(errorRate, 0.0, 1.0))
    return np.array([f1, f2, f3, f4, f5, f6], dtype=np.float32)


def windowStatistics(history: list[np.ndarray]) -> np.ndarray:
    """슬라이딩 윈도우의 평균·slope·표준편차.

    윈도우는 **t 이전 데이터로만** 구성한다 — 라벨 창 ``[t, t+Δ]`` 과 겹치면
    라벨 누수가 된다(§4-2).
    """
    if not history:
        return np.zeros(NUM_SLOTS * NUM_WINDOW_STATS, dtype=np.float32)
    stacked = np.stack(history)
    mean = stacked.mean(axis=0)
    std = stacked.std(axis=0)
    if len(history) >= 2:
        steps = np.arange(len(history), dtype=np.float32)
        centred = steps - steps.mean()
        denominator = (centred ** 2).sum()
        slope = (centred[:, None] * (stacked - mean)).sum(axis=0) / denominator
    else:
        slope = np.zeros(NUM_SLOTS, dtype=np.float32)
    return np.concatenate([mean, slope, std]).astype(np.float32)


def collectUpstreamByHop(callEdges: list[list[str]], nodes: list[str], hops: int
                         ) -> dict[str, list[list[str]]]:
    """v 의 h-hop 상류. 상류 = v 가 호출하는 쪽이다(전파는 그 반대로 흐른다)."""
    direct = {node: [callee for caller, callee in callEdges if caller == node] for node in nodes}
    byHop: dict[str, list[list[str]]] = {node: [] for node in nodes}
    for node in nodes:
        frontier = direct[node]
        for _ in range(hops):
            byHop[node].append(list(frontier))
            frontier = [n for f in frontier for n in direct.get(f, [])]
    return byHop


def buildEpisodeSamples(episode: dict, chainDir: pathlib.Path, arguments) -> dict:
    """에피소드 하나를 (self feature, 이웃 집계, 라벨, 그룹)으로 바꾼다."""
    tracePath = chainDir / episode["tracePath"]
    series = relabel.loadTrace(tracePath)
    callEdges = episode["callGraph"]["callEdges"]
    nodes = episode["callGraph"]["nodes"]
    injectedAt = episode["injectedAt"]
    normalStart = injectedAt - float(episode.get("normalSeconds", 50.0))

    # 기준선은 정상 구간에서만 잡는다. 워밍업이 섞이면 전부 끌려 올라간다.
    baselines: dict[str, dict] = {}
    for name, node in series.items():
        normalIndexes = [i for i, t in enumerate(node.timestamps) if normalStart <= t < injectedAt]
        p95 = [node.latencyP95[i] for i in normalIndexes if not math.isnan(node.latencyP95[i])]
        baselines[name] = {
            "latencyP95": float(np.median(p95)) if p95 else float("nan"),
            "maxThroughput": float("nan"),
        }

    # 원시 레코드를 노드별·시각순으로 다시 읽는다 (NodeSeries 는 일부 필드만 갖는다).
    rawByNode: dict[str, list[dict]] = {name: [] for name in series}
    for line in tracePath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        rawByNode.setdefault(record["node"], []).append(record)
    for name, records in rawByNode.items():
        records.sort(key=lambda r: r["timestamp"])
        normalThroughput = [r.get("throughputRps", float("nan")) for r in records
                            if normalStart <= r["timestamp"] < injectedAt
                            and not math.isnan(r.get("throughputRps", float("nan")))]
        baselines[name]["maxThroughput"] = max(normalThroughput) if normalThroughput else float("nan")

    # 노드별 슬롯 시계열
    slotSeries: dict[str, list[np.ndarray]] = {}
    timeIndex: dict[str, list[float]] = {}
    for name, records in rawByNode.items():
        slotSeries[name] = [toSlots(r, baselines[name]) for r in records]
        timeIndex[name] = [r["timestamp"] for r in records]

    # self feature = 현재 슬롯 + 윈도우 통계
    selfFeatures: dict[str, list[np.ndarray]] = {}
    for name in slotSeries:
        rows = []
        for index in range(len(slotSeries[name])):
            history = slotSeries[name][max(0, index - WINDOW_SAMPLES):index]
            rows.append(np.concatenate([slotSeries[name][index], windowStatistics(history)]))
        selfFeatures[name] = rows

    upstreamByHop = collectUpstreamByHop(callEdges, nodes, AGGREGATION_HOPS)
    downstreamOf = {node: [caller for caller, callee in callEdges if callee == node]
                    for node in nodes}

    labelResult = relabel.labelEpisode(episode, tracePath, arguments)
    labelLookup = {(e["node"], round(e["timestamp"], 3)): e for e in labelResult["labels"]}

    samples = []
    for name in nodes:
        if name not in selfFeatures:
            continue
        for index, timestamp in enumerate(timeIndex[name]):
            entry = labelLookup.get((name, round(timestamp, 3)))
            if entry is None:
                continue   # 워밍업 구간은 라벨 대상이 아니다
            selfVector = selfFeatures[name][index]

            # 이웃 집계 — 같은 시각에 가장 가까운 표본을 쓴다.
            hopVectors = []
            for hop in range(AGGREGATION_HOPS):
                neighbours = upstreamByHop[name][hop]
                gathered = []
                for neighbour in neighbours:
                    if neighbour not in selfFeatures or not timeIndex[neighbour]:
                        continue
                    nearest = min(range(len(timeIndex[neighbour])),
                                  key=lambda i: abs(timeIndex[neighbour][i] - timestamp))
                    gathered.append(selfFeatures[neighbour][nearest])
                if gathered:
                    stacked = np.stack(gathered)
                    hopVectors.append(np.concatenate(
                        [stacked.mean(axis=0), stacked.max(axis=0), stacked.sum(axis=0)]))
                else:
                    hopVectors.append(np.zeros(SELF_DIM * 3, dtype=np.float32))

            # 하류(호출자)의 f2 큐 대기율 · f4 지연 분위수
            downstreamValues = []
            for caller in downstreamOf.get(name, []):
                if caller not in slotSeries or not timeIndex[caller]:
                    continue
                nearest = min(range(len(timeIndex[caller])),
                              key=lambda i: abs(timeIndex[caller][i] - timestamp))
                downstreamValues.append(slotSeries[caller][nearest][[1, 3]])
            downstreamVector = (np.stack(downstreamValues).mean(axis=0) if downstreamValues
                                else np.zeros(2, dtype=np.float32))

            structural = np.array([
                len(upstreamByHop[name][0]),
                len(downstreamOf.get(name, [])),
                float(not upstreamByHop[name][0]),   # 사슬 끝인가
                float(not downstreamOf.get(name, [])),  # 입구인가
            ], dtype=np.float32)

            samples.append({
                "node": name,
                "timestamp": timestamp,
                "self": selfVector,
                "neighbour": np.concatenate(hopVectors + [downstreamVector, structural]),
                "label": entry["label"],
                "isInjectionPoint": entry["isInjectionPoint"],
            })
    return {"samples": samples, "episodeKey": episode["tracePath"]}
