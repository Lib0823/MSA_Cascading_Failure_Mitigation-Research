"""실시간 지표 → 학습과 **같은 feature 공간**.

폐루프에서 가장 조용히 틀리는 지점이다. 학습은 에피소드 전체를 본 뒤 기준선을
잡지만, 추론은 **미래를 모른 채** 같은 feature 를 만들어야 한다.

학습과 추론이 어긋나는 세 지점
==============================

======================  ==========================================  =============
항목                     학습 (buildDataset)                         추론 (여기)
======================  ==========================================  =============
기준선 (f3·f4 의 분모)   에피소드의 **정상 구간** 중앙값·최대         **워밍업 구간**
                                                                    에서 확정하고
                                                                    이후 고정
윈도우 통계             인덱스 기반 과거 10 표본                     링 버퍼의 과거
                                                                    10 표본
이웃 집계               같은 시각에 가장 가까운 표본                 **같은 틱**의
                                                                    최신값
======================  ==========================================  =============

**기준선을 추론 중에 갱신하면 안 된다.** 장애가 시작되면 기준선이 함께 올라가
위반이 위반으로 보이지 않는다 — 학습 때 같은 이유로 정상 구간에서만 잡았다.
따라서 워밍업이 끝나는 시점에 **고정**하고 그 뒤로 건드리지 않는다.
"""

from __future__ import annotations

import collections
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "model"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "chain"))

import buildDataset  # noqa: E402


class NodeFeatureBuilder:
    """한 노드의 링 버퍼. 슬롯 시계열과 윈도우 통계를 유지한다."""

    def __init__(self, windowSamples: int) -> None:
        self.windowSamples = windowSamples
        # 윈도우 통계는 **현재 표본을 제외한 과거**로 만든다. 학습이 그렇게 했고,
        # 현재 값을 섞으면 통계가 현재를 포함해 slope 의 의미가 달라진다.
        self.history: collections.deque = collections.deque(maxlen=windowSamples)
        self.latestSlots: np.ndarray | None = None
        self.baseline: dict = {"latencyP95": float("nan"), "maxThroughput": float("nan")}
        self.warmupLatency: list[float] = []
        self.warmupThroughput: list[float] = []
        self.baselineFixed = False

    def observeWarmup(self, record: dict) -> None:
        """워밍업 구간. 기준선 재료만 모으고 feature 는 만들지 않는다."""
        latency = record.get("latencyP95", float("nan"))
        throughput = record.get("throughputRps", float("nan"))
        if isinstance(latency, (int, float)) and not math.isnan(latency):
            self.warmupLatency.append(float(latency))
        if isinstance(throughput, (int, float)) and not math.isnan(throughput):
            self.warmupThroughput.append(float(throughput))

    def fixBaseline(self) -> bool:
        """워밍업이 끝나면 기준선을 고정한다. 이후 갱신하지 않는다."""
        if not self.warmupLatency or not self.warmupThroughput:
            return False
        self.baseline = {
            "latencyP95": float(np.median(self.warmupLatency)),
            "maxThroughput": float(max(self.warmupThroughput)),
        }
        self.baselineFixed = True
        return True

    def observe(self, record: dict) -> np.ndarray | None:
        """한 틱을 받아 self feature 를 돌려준다. 기준선 미확정이면 None."""
        if not self.baselineFixed:
            self.observeWarmup(record)
            return None
        slots = buildDataset.toSlots(record, self.baseline)
        windowStats = buildDataset.windowStatistics(list(self.history))
        self.history.append(slots)
        self.latestSlots = slots
        return np.concatenate([slots, windowStats])


class ChainFeatureBuilder:
    """사슬 전체. 이웃 집계까지 묶어 학습과 같은 174차원을 만든다."""

    def __init__(self, nodes: list[str], callEdges: list[list[str]],
                 windowSamples: int = buildDataset.WINDOW_SAMPLES,
                 hops: int = buildDataset.AGGREGATION_HOPS) -> None:
        self.nodes = nodes
        self.callEdges = callEdges
        self.hops = hops
        self.builders = {node: NodeFeatureBuilder(windowSamples) for node in nodes}
        self.upstreamByHop = buildDataset.collectUpstreamByHop(callEdges, nodes, hops)
        self.downstreamOf = {node: [caller for caller, callee in callEdges if callee == node]
                             for node in nodes}
        self.selfFeatures: dict[str, np.ndarray] = {}

    def fixBaselines(self) -> list[str]:
        """기준선을 고정하고, 재료가 부족해 실패한 노드 목록을 돌려준다."""
        failed = [node for node, builder in self.builders.items() if not builder.fixBaseline()]
        return failed

    def observeTick(self, records: dict[str, dict]) -> None:
        for node, record in records.items():
            builder = self.builders.get(node)
            if builder is None:
                continue
            vector = builder.observe(record)
            if vector is not None:
                self.selfFeatures[node] = vector

    def buildFeatures(self, node: str) -> np.ndarray | None:
        """학습과 같은 배치로 174차원을 만든다. 준비가 안 되면 None."""
        selfVector = self.selfFeatures.get(node)
        if selfVector is None:
            return None

        hopVectors = []
        for hop in range(self.hops):
            gathered = [self.selfFeatures[n] for n in self.upstreamByHop[node][hop]
                        if n in self.selfFeatures]
            if gathered:
                stacked = np.stack(gathered)
                hopVectors.append(np.concatenate(
                    [stacked.mean(axis=0), stacked.max(axis=0), stacked.sum(axis=0)]))
            else:
                hopVectors.append(np.zeros(buildDataset.SELF_DIM * 3, dtype=np.float32))

        downstreamValues = [self.builders[caller].latestSlots[[1, 3]]
                            for caller in self.downstreamOf.get(node, [])
                            if self.builders.get(caller) is not None
                            and self.builders[caller].latestSlots is not None]
        downstreamVector = (np.stack(downstreamValues).mean(axis=0) if downstreamValues
                            else np.zeros(2, dtype=np.float32))

        structural = np.array([
            len(self.upstreamByHop[node][0]),
            len(self.downstreamOf.get(node, [])),
            float(not self.upstreamByHop[node][0]),
            float(not self.downstreamOf.get(node, [])),
        ], dtype=np.float32)

        return np.concatenate([selfVector] + hopVectors + [downstreamVector, structural])
