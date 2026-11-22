"""측정 타당성 샘플러 — 호스트 포화와 (K8s 일 때) 노드 내부 포화를 함께 기록한다.

§4-1 의 측정 유효성 프로토콜이다. 포화된 구간의 지연에는 CPU 대기가 섞이므로,
그 구간에서 만든 기준선과 feature 는 조용히 틀린다([[research/challenges.md]] J17).

왜 둘인가 (2026-11-22)
======================

호스트(macOS) 부하만 보면 **VM 내부 포화를 놓친다.** VM 이 6 vCPU 를 다 써도 호스트에
코어가 남으면 호스트 부하는 8 미만이고, 그 사이 VM 안에서는 CPU 대기가 생긴다. 기존
Compose 셀도 Colima VM(4 vCPU) 위였는데 **호스트만 쟀다** — 그 셀들의 노드 내부 포화
여부는 기록이 없다.

K8s 백엔드에서는 metrics-server 의 노드 CPU 사용량을 함께 표집한다. kubectl 만 쓰므로
minikube 든 kubeadm 이든 같다. metrics-server 의 해상도가 15초라 표본이 성기다 —
**포화 여부 판정**에는 충분하지만 순간 스파이크는 못 본다.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading


def powerSource() -> str | None:
    """macOS 의 전원 — `"AC"`·`"Battery"`, 판정 불가면 None.

    처리량을 재는 실험이므로 전원 상태가 바뀌면 그것이 측정값에 섞인다. 그리고 기존
    셀은 AC 에서 측정됐다 — 배터리로 잰 셀은 그것과 비교할 수 없다. 종전에는 이 점검이
    **런처 셸 스크립트에만** 있어서, 스크립트를 거치지 않고 돌리면 막히지 않았다.
    """
    if sys.platform != "darwin":
        return None
    completed = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                               check=False)
    if "AC Power" in completed.stdout:
        return "AC"
    if "Battery Power" in completed.stdout:
        return "Battery"
    return None


class HostLoadSampler:
    """호스트 포화. `os.getloadavg()` 는 의존성이 없고 macOS·Linux 모두에서 돈다.

    load > 코어 수 면 포화이며, 그 구간의 라벨은 신뢰할 수 없다.
    """

    def __init__(self, intervalSeconds: float = 2.0) -> None:
        self.intervalSeconds = intervalSeconds
        self.coreCount = os.cpu_count() or 1
        self.samples: list[float] = []
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stopFlag.wait(self.intervalSeconds):
            try:
                self.samples.append(os.getloadavg()[0])
            except OSError:
                pass

    def __enter__(self) -> "HostLoadSampler":
        self.thread.start()
        return self

    def __exit__(self, *exceptionInfo: object) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=5)

    def summary(self) -> dict:
        if not self.samples:
            return {"coreCount": self.coreCount, "loadSamples": 0}
        saturated = sum(1 for v in self.samples if v > self.coreCount)
        return {
            "coreCount": self.coreCount,
            "loadSamples": len(self.samples),
            "loadMedian": round(statistics.median(self.samples), 2),
            "loadMax": round(max(self.samples), 2),
            # 이 비율이 높으면 해당 셀의 측정을 신뢰하면 안 된다.
            "saturatedShare": round(saturated / len(self.samples), 3),
        }


class NodeCpuSampler:
    """K8s 노드 내부 CPU — metrics-server 의 `usage.cpu` 를 allocatable 과 견준다.

    포화 기준은 **90%** 다. 호스트 쪽(load > 코어 수)과 기준이 다른 이유는 단위가
    달라서다 — load 는 대기 중인 것까지 세는 길이이고, 이쪽은 사용률이다. 사용률이
    90% 를 넘으면 실행 대기가 이미 생기고 있다고 본다.
    """

    SATURATION_RATIO = 0.9

    def __init__(self, intervalSeconds: float = 5.0) -> None:
        self.intervalSeconds = intervalSeconds
        self.samples: list[float] = []
        self.allocatableCores: float | None = None
        self.errors = 0
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    @staticmethod
    def parseCores(quantity: str) -> float:
        """K8s CPU 수량 → 코어. `250m`, `1500000n`, `2`, `0.5` 를 받는다."""
        if quantity.endswith("n"):
            return int(quantity[:-1]) / 1e9
        if quantity.endswith("u"):
            return int(quantity[:-1]) / 1e6
        if quantity.endswith("m"):
            return int(quantity[:-1]) / 1e3
        return float(quantity)

    def readAllocatable(self) -> float | None:
        completed = subprocess.run(["kubectl", "get", "nodes", "-o", "json"],
                                   capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return None
        nodes = json.loads(completed.stdout)["items"]
        return sum(self.parseCores(n["status"]["allocatable"]["cpu"]) for n in nodes)

    def loop(self) -> None:
        self.allocatableCores = self.readAllocatable()
        while not self.stopFlag.wait(self.intervalSeconds):
            completed = subprocess.run(
                ["kubectl", "get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"],
                capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                self.errors += 1
                continue
            try:
                items = json.loads(completed.stdout)["items"]
                self.samples.append(sum(self.parseCores(i["usage"]["cpu"]) for i in items))
            except (KeyError, ValueError, json.JSONDecodeError):
                self.errors += 1

    def __enter__(self) -> "NodeCpuSampler":
        self.thread.start()
        return self

    def __exit__(self, *exceptionInfo: object) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=10)

    def summary(self) -> dict:
        base = {"allocatableCores": self.allocatableCores, "samples": len(self.samples),
                "errors": self.errors, "saturationRatio": self.SATURATION_RATIO}
        if not self.samples or not self.allocatableCores:
            return base
        ceiling = self.allocatableCores * self.SATURATION_RATIO
        return {
            **base,
            "usageMedianCores": round(statistics.median(self.samples), 2),
            "usageMaxCores": round(max(self.samples), 2),
            "saturatedShare": round(sum(1 for v in self.samples if v > ceiling)
                                    / len(self.samples), 3),
        }
