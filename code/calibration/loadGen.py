"""게이트 ①.5 전용 폐루프 부하기.

Locust 를 쓰지 않는 이유는 두 가지다. (1) 의존성이 늘지 않는다 — 표준 라이브러리만
쓴다. (2) 이 게이트에서 필요한 것은 **인스턴스별 균등 분배가 보장된** 부하인데,
로드밸런서를 끼우면 그 프로세스 자신이 호스트 CPU 를 먹어 대조군의 타당성을 깎는다.
따라서 부하기가 대상 포트 목록을 직접 알고 라운드로빈한다.

폐루프(closed loop)인 이유: 열린 루프로 고정 RPS 를 밀면 포화 시점에 큐가 무한히
쌓여 지연이 발산하고 조건 간 비교가 무의미해진다. 동시 사용자 수를 고정하면
포화는 처리량 저하로 나타나므로 2→4 비교가 성립한다.
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field

REQUEST_PATH = "/work"
REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass
class LoadResult:
    """한 조건에서 측정된 부하 결과."""

    targets: list[str]
    workerCount: int
    durationSeconds: float
    latenciesMillis: list[float] = field(default_factory=list)
    errorCount: int = 0
    statusCounts: dict[str, int] = field(default_factory=dict)

    def summarize(self) -> dict:
        total = len(self.latenciesMillis) + self.errorCount
        ordered = sorted(self.latenciesMillis)

        def percentile(fraction: float) -> float:
            if not ordered:
                return float("nan")
            index = min(len(ordered) - 1, int(fraction * len(ordered)))
            return ordered[index]

        return {
            "targets": self.targets,
            "workerCount": self.workerCount,
            "durationSeconds": round(self.durationSeconds, 2),
            "requestCount": total,
            "successCount": len(ordered),
            "errorCount": self.errorCount,
            "errorRate": (self.errorCount / total) if total else float("nan"),
            "throughputRps": total / self.durationSeconds if self.durationSeconds else 0.0,
            "goodputRps": len(ordered) / self.durationSeconds if self.durationSeconds else 0.0,
            "latencyMeanMillis": statistics.fmean(ordered) if ordered else float("nan"),
            "latencyP50Millis": percentile(0.50),
            "latencyP95Millis": percentile(0.95),
            "latencyP99Millis": percentile(0.99),
            "statusCounts": dict(sorted(self.statusCounts.items())),
        }


def runLoad(
    targets: list[str],
    workerCount: int,
    durationSeconds: float,
    warmupSeconds: float = 10.0,
) -> dict:
    """대상들에 폐루프 부하를 걸고 워밍업 이후 구간만 집계해 돌려준다.

    Args:
        targets: ``"127.0.0.1:18081"`` 형태의 대상 목록.
        workerCount: 동시 가상 사용자 수. 이 값이 부하 수준이다.
        durationSeconds: 워밍업을 제외한 측정 시간.
        warmupSeconds: JIT·커넥션풀 예열 구간. 이 구간의 표본은 버린다.

    Returns:
        ``LoadResult.summarize()`` 의 결과 dict.
    """
    result = LoadResult(targets=targets, workerCount=workerCount, durationSeconds=durationSeconds)
    resultLock = threading.Lock()
    measuringFlag = threading.Event()
    stopFlag = threading.Event()

    def worker(workerIndex: int) -> None:
        # 워커마다 시작 대상을 어긋나게 잡아, 동시 진입 시점에도 분배가 치우치지 않게 한다.
        cursor = workerIndex % len(targets)
        connections: dict[str, http.client.HTTPConnection] = {}
        localLatencies: list[float] = []
        localErrors = 0
        localStatuses: dict[str, int] = {}

        while not stopFlag.is_set():
            target = targets[cursor % len(targets)]
            cursor += 1
            startedAt = time.perf_counter()
            statusKey = "error"
            failed = True
            try:
                connection = connections.get(target)
                if connection is None:
                    host, _, port = target.partition(":")
                    connection = http.client.HTTPConnection(
                        host, int(port), timeout=REQUEST_TIMEOUT_SECONDS
                    )
                    connections[target] = connection
                connection.request("GET", REQUEST_PATH)
                response = connection.getresponse()
                response.read()
                statusKey = str(response.status)
                # 503 은 포화 신호이므로 성공이 아니다. 지연 분포에 넣으면
                # 빨리 실패한 요청이 p95 를 낮춰 포화를 가려 버린다.
                failed = response.status >= 400
            except Exception as exception:  # noqa: BLE001 - 네트워크 실패 전부를 에러로 센다
                statusKey = type(exception).__name__
                connections.pop(target, None)
            elapsedMillis = (time.perf_counter() - startedAt) * 1000.0

            if measuringFlag.is_set():
                localStatuses[statusKey] = localStatuses.get(statusKey, 0) + 1
                if failed:
                    localErrors += 1
                else:
                    localLatencies.append(elapsedMillis)

        for connection in connections.values():
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

        with resultLock:
            result.latenciesMillis.extend(localLatencies)
            result.errorCount += localErrors
            for key, count in localStatuses.items():
                result.statusCounts[key] = result.statusCounts.get(key, 0) + count

    threads = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(workerCount)
    ]
    for thread in threads:
        thread.start()

    time.sleep(warmupSeconds)
    measuringFlag.set()
    measureStartedAt = time.perf_counter()
    time.sleep(durationSeconds)
    measuringFlag.clear()
    result.durationSeconds = time.perf_counter() - measureStartedAt
    stopFlag.set()

    for thread in threads:
        thread.join(timeout=REQUEST_TIMEOUT_SECONDS + 5)

    return result.summarize()


def main() -> int:
    parser = argparse.ArgumentParser(description="게이트 ①.5 폐루프 부하기")
    parser.add_argument("--targets", required=True, help="쉼표로 구분한 host:port 목록")
    parser.add_argument("--workers", type=int, default=32, help="동시 가상 사용자 수")
    parser.add_argument("--duration", type=float, default=60.0, help="측정 시간(초)")
    parser.add_argument("--warmup", type=float, default=10.0, help="워밍업 시간(초)")
    arguments = parser.parse_args()

    summary = runLoad(
        targets=[target.strip() for target in arguments.targets.split(",") if target.strip()],
        workerCount=arguments.workers,
        durationSeconds=arguments.duration,
        warmupSeconds=arguments.warmup,
    )
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
