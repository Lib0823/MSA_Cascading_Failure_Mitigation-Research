"""노드별 지표 시계열 수집기.

부하기는 입구 노드(gateway)만 본다. 그런데 §4-2 의 전파성 판정은 "상류가 **먼저**
열화했는가"라는 **순서** 문제이므로 사슬 중간 노드 각각의 시계열이 있어야 한다.
그래서 각 노드의 ``/actuator/prometheus`` 를 1Hz 로 긁어 둔다.

수집하는 것은 §4-4 가 "최소 추가 관측 집합" 후보로 지목한 지표들이다.

===========================  ====================================================
지표                         S1 과 S3 를 가르는 역할
===========================  ====================================================
``latencyP95``               **못 가른다.** 둘 다 오른다 — 이것이 H5b 의 전제다
                             (누적 버킷 차분으로 구간별 참 분위수를 계산한다)
``errorRate``                **못 가른다.** 둘 다 오를 수 있다
``poolAcquireMillis``        **S1 에서만** 오른다 (풀 획득 대기)
``poolUsageMillis``          **S3 에서만** 오른다 (커넥션 보유시간)
``poolPending``              **S1 에서만** 오른다 (풀 대기 중인 스레드 수)
===========================  ====================================================

앞의 둘로 구분되지 않고 뒤의 셋으로 구분되면, H5b(증상 동형)와 H7(최소 관측 집합)이
같은 데이터에서 함께 지지된다.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
import urllib.error
import urllib.request

SCRAPE_TIMEOUT_SECONDS = 2.0

# Prometheus 노출 이름 → 우리가 쓰는 이름.
# 카운터는 누적값이므로 수집 후 차분해 비율로 바꾼다.
COUNTER_METRICS = {
    "http_server_requests_seconds_count": "requestCount",
    "http_server_requests_seconds_sum": "requestSecondsSum",
}
GAUGE_METRICS = {
    "hikaricp_connections_pending": "poolPending",
    "hikaricp_connections_active": "poolActive",
    "hikaricp_connections": "poolTotal",
}
TIMER_METRICS = {
    "hikaricp_connections_acquire_seconds": "poolAcquire",
    "hikaricp_connections_usage_seconds": "poolUsage",
}


def parsePrometheusText(body: str) -> dict:
    """노출 텍스트에서 필요한 계열만 뽑는다.

    http_server_requests 는 라벨별(엔드포인트·상태코드)로 여러 줄이 나오므로
    ``/work`` 요청만 골라 상태코드별로 합산한다. ``/actuator/*`` 요청까지 섞이면
    수집기 자신의 트래픽이 지표를 오염시킨다.
    """
    buckets: dict[float, float] = {}
    sample = {
        "requestCount": 0.0,
        "requestSecondsSum": 0.0,
        "errorCount": 0.0,
        "latencyP95": float("nan"),
        "latencyP99": float("nan"),
    }
    for metricName in list(GAUGE_METRICS.values()) + ["poolAcquireSum", "poolAcquireCount",
                                                      "poolUsageSum", "poolUsageCount"]:
        sample[metricName] = float("nan")

    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition("{")
        if not rest:
            name, _, valueText = line.partition(" ")
            labels = ""
        else:
            labels, _, valueText = rest.partition("} ")
        try:
            value = float(valueText.strip())
        except ValueError:
            continue

        if name in ("http_server_requests_seconds_count", "http_server_requests_seconds_sum"):
            if 'uri="/work"' not in labels:
                continue
            key = COUNTER_METRICS[name]
            sample[key] += value
            if name == "http_server_requests_seconds_count" and 'outcome="SERVER_ERROR"' in labels:
                sample["errorCount"] += value
        elif name == "http_server_requests_seconds_bucket":
            # 누적 히스토그램. 구간 차분으로 참 분위수를 만든다.
            if 'uri="/work"' not in labels:
                continue
            bound = None
            for part in labels.split(","):
                if part.strip().startswith('le="'):
                    bound = part.strip()[4:].rstrip('"')
                    break
            if bound is None:
                continue
            edge = float("inf") if bound in ("+Inf", "Inf") else float(bound)
            buckets[edge] = buckets.get(edge, 0.0) + value
        elif name in GAUGE_METRICS:
            sample[GAUGE_METRICS[name]] = value
        elif name.endswith("_sum") and name[:-4] in TIMER_METRICS:
            sample[TIMER_METRICS[name[:-4]] + "Sum"] = value
        elif name.endswith("_count") and name[:-6] in TIMER_METRICS:
            sample[TIMER_METRICS[name[:-6]] + "Count"] = value

    sample["buckets"] = buckets
    return sample


def quantileFromBucketDelta(previous: dict, current: dict, quantile: float) -> float:
    """두 스크레이프 사이 **그 구간에서만** 발생한 요청의 분위수를 구한다.

    누적 버킷의 차분을 쓰므로 과거가 섞이지 않는다. Micrometer 의 감쇠 윈도우
    추정값과 달리 시점 해상도가 수집 간격과 같아지며, 이것이 전파성 판정에서
    onset 순서를 집을 수 있게 하는 조건이다.

    버킷 경계 내 분포는 알 수 없으므로 **경계값을 그대로 돌려준다**(상한 추정).
    절대값이 아니라 기준선 대비 배수로 쓰이므로 이 거칠기는 문제되지 않는다.
    """
    edges = sorted(set(previous) | set(current))
    deltas = [(edge, current.get(edge, 0.0) - previous.get(edge, 0.0)) for edge in edges]
    deltas = [(edge, max(0.0, delta)) for edge, delta in deltas]
    if not deltas:
        return float("nan")
    total = deltas[-1][1]
    if total <= 0:
        return float("nan")
    target = quantile * total
    for edge, cumulative in deltas:
        if cumulative >= target:
            return float("nan") if edge == float("inf") else edge * 1000.0
    return float("nan")


def scrapeNode(baseUrl: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            baseUrl + "/actuator/prometheus", timeout=SCRAPE_TIMEOUT_SECONDS
        ) as response:
            return parsePrometheusText(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def deriveRates(previous: dict | None, current: dict, elapsedSeconds: float) -> dict:
    """누적 카운터를 구간 비율로 바꾼다.

    누적값을 그대로 쓰면 어떤 노드든 단조 증가라 "열화 시점"을 집을 수 없다.
    전파성 판정이 요구하는 것은 수준이 아니라 **변화**다.
    """
    derived = dict(current)
    if previous is None or elapsedSeconds <= 0:
        derived.update({"throughputRps": float("nan"), "errorRate": float("nan"),
                        "meanLatencyMillis": float("nan"), "latencyP95": float("nan"),
                        "latencyP99": float("nan"),
                        "poolAcquireMillis": float("nan"), "poolUsageMillis": float("nan")})
        derived.pop("buckets", None)
        return derived

    requestDelta = current["requestCount"] - previous["requestCount"]
    errorDelta = current["errorCount"] - previous["errorCount"]
    secondsDelta = current["requestSecondsSum"] - previous["requestSecondsSum"]

    derived["latencyP95"] = quantileFromBucketDelta(
        previous.get("buckets", {}), current.get("buckets", {}), 0.95)
    derived["latencyP99"] = quantileFromBucketDelta(
        previous.get("buckets", {}), current.get("buckets", {}), 0.99)
    derived["throughputRps"] = requestDelta / elapsedSeconds
    derived["errorRate"] = (errorDelta / requestDelta) if requestDelta > 0 else 0.0
    derived["meanLatencyMillis"] = (secondsDelta / requestDelta * 1000.0) if requestDelta > 0 else float("nan")

    for prefix, outputName in (("poolAcquire", "poolAcquireMillis"), ("poolUsage", "poolUsageMillis")):
        countDelta = current.get(prefix + "Count", float("nan")) - previous.get(prefix + "Count", float("nan"))
        sumDelta = current.get(prefix + "Sum", float("nan")) - previous.get(prefix + "Sum", float("nan"))
        derived[outputName] = (sumDelta / countDelta * 1000.0) if countDelta and countDelta > 0 else float("nan")

    derived.pop("buckets", None)
    return derived


class ChainScraper:
    """사슬 전 노드를 1Hz 로 긁어 JSONL 로 남긴다."""

    def __init__(self, nodes: dict[str, str], outputPath: pathlib.Path,
                 intervalSeconds: float = 1.0) -> None:
        self.nodes = nodes
        self.outputPath = outputPath
        self.intervalSeconds = intervalSeconds
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.sampleCount = 0

    def loop(self) -> None:
        previous: dict[str, dict] = {}
        previousAt: dict[str, float] = {}
        with self.outputPath.open("a", encoding="utf-8") as stream:
            while not self.stopFlag.wait(self.intervalSeconds):
                now = time.time()
                for nodeName, baseUrl in self.nodes.items():
                    raw = scrapeNode(baseUrl)
                    if raw is None:
                        continue
                    elapsed = now - previousAt.get(nodeName, now)
                    record = deriveRates(previous.get(nodeName), raw, elapsed)
                    record["node"] = nodeName
                    record["timestamp"] = now
                    previous[nodeName] = raw
                    previousAt[nodeName] = now
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    self.sampleCount += 1
                stream.flush()

    def __enter__(self) -> "ChainScraper":
        self.thread.start()
        return self

    def __exit__(self, *exceptionInfo: object) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=10)
