"""H5b — 조치 오선택 비용 측정.

증상 동형성(S1↔S3)은 이미 확인했다. 여기서 확인하는 것은 나머지 절반, **정답이
상충한다**는 쪽이다.

======  =================  ====================================
조치     S1 (커넥션 고갈)    S3 (다운스트림 지연)
======  =================  ====================================
Bulkhead  **정답**          무효 (근본원인 미해소, 처리량만 깎임)
CB        **역효과**        **정답**
          (재시도 증폭)
======  =================  ====================================

**H5b 가 묻는 것**: 오선택(S1+CB, S3+Bulkhead)이 **무조치보다 나쁜가.**
그냥 "덜 좋다"가 아니라 **무조치보다 나쁜 구간이 실재하는가**가 쟁점이다.
그 구간이 없으면 *"조치를 고르는 것"* 의 가치가 *"아무거나 해도 손해는 없다"* 로
떨어지고, H2(신뢰도 기반 조치 선택)의 실증적 근거가 약해진다.

`mₐ` 는 §4-4 대로 **조치 유무 대조**로 추정한다 — 같은 시나리오의 무조치 칸 대비
장애 구간 goodput·p95 의 회복분이다.

행렬:
    {s1, s3} × {none, bulkhead, circuit_breaker}  = 6 칸 (+ normal 대조)

조치는 장애 주입과 **동시에** 건다. 예측이 맞았다고 가정한 선제 대응 상황이며,
`mₐ` 는 원래 조치 유무 대조로 정의되므로 설계와 정합한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "calibration"))

import loadGen  # noqa: E402
import metricScraper  # noqa: E402
import runChain  # noqa: E402  (사슬 기동·주입 로직을 재사용한다)

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
RUN_STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

REMEDIATION_NODE = "orders"   # 적용 지점 — 위험 노드(payment)의 호출자 (§2-C)
REMEDIATIONS = ("NONE", "BULKHEAD", "CIRCUIT_BREAKER")
SCENARIOS = ("s1", "s3")


def setRemediation(action: str) -> None:
    url = (runChain.NODE_ENDPOINTS[REMEDIATION_NODE] + "/remediation?"
           + urllib.parse.urlencode({"action": action}))
    with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=5) as response:
        response.read()


def readRemediationSnapshot() -> dict:
    try:
        with urllib.request.urlopen(
            runChain.NODE_ENDPOINTS[REMEDIATION_NODE] + "/remediation", timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def runCell(scenario: str, remediation: str, arguments: argparse.Namespace) -> dict:
    environment = runChain.buildEnvironment(scenario, arguments)
    environment["BULKHEAD_MAX_CONCURRENT"] = str(arguments.bulkheadMaxConcurrent)
    tracePath = RESULTS_DIR / f"trace-{scenario}-{remediation.lower()}-{RUN_STAMP}.jsonl"
    RESULTS_DIR.mkdir(exist_ok=True)

    print(f"\n━━━ {scenario} × {remediation} ━━━", flush=True)
    runChain.composeDown(environment)
    hog = runChain.ConnectionHog(arguments.hogSessions, environment)
    injectedAt = None
    loadResult: dict = {}
    try:
        runChain.composeUp(environment)
        setRemediation("NONE")

        def driveLoad() -> None:
            loadResult.update(loadGen.runLoad(
                targets=runChain.ENTRY_TARGETS,
                workerCount=arguments.workers,
                durationSeconds=arguments.normalSeconds + arguments.faultSeconds,
                warmupSeconds=arguments.warmup))

        loadThread = threading.Thread(target=driveLoad, daemon=True)
        with metricScraper.ChainScraper(runChain.NODE_ENDPOINTS, tracePath) as scraper:
            loadThread.start()
            time.sleep(arguments.warmup + arguments.normalSeconds)

            injectedAt = time.time()
            # 장애와 조치를 동시에 건다. 조치를 먼저 걸면 "장애 없는데 개입"이 되어
            # H5a 측정이 되고, 나중에 걸면 과도 구간이 섞인다.
            if remediation != "NONE":
                setRemediation(remediation)
            if scenario == "s1":
                hog.start()
            elif scenario == "s3":
                runChain.setFault("payment", arguments.faultDelayMillis)
            print(f"  ⚡ {scenario} 주입 + 조치 {remediation}", flush=True)

            loadThread.join(timeout=arguments.faultSeconds + 120)
            snapshot = readRemediationSnapshot()
            sampleCount = scraper.sampleCount
        hog.stop()
    finally:
        runChain.composeDown(environment)

    cell = {
        "scenario": scenario, "remediation": remediation,
        "injectedAt": injectedAt, "injectionNode": "payment",
        "remediationNode": REMEDIATION_NODE,
        "tracePath": str(tracePath.relative_to(HERE)),
        "normalSeconds": arguments.normalSeconds, "faultSeconds": arguments.faultSeconds,
        "entryLoad": loadResult, "remediationSnapshot": snapshot,
        "nodeSampleCount": sampleCount,
        "callGraph": json.loads((HERE / "callGraph.json").read_text(encoding="utf-8")),
    }
    print(f"  goodput {loadResult.get('goodputRps', float('nan')):.1f} rps"
          f" / p95 {loadResult.get('latencyP95Millis', float('nan')):.0f} ms"
          f" / err {loadResult.get('errorRate', float('nan')):.1%}", flush=True)
    return cell


def main() -> int:
    parser = argparse.ArgumentParser(description="H5b 조치 오선택 비용 측정")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--remediations", default=",".join(REMEDIATIONS))
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--warmup", type=float, default=60.0)
    parser.add_argument("--normalSeconds", type=float, default=50.0)
    parser.add_argument("--faultSeconds", type=float, default=70.0)
    parser.add_argument("--maxConnections", type=int, default=30)
    parser.add_argument("--hogSessions", type=int, default=24)
    parser.add_argument("--faultDelayMillis", type=int, default=400)
    parser.add_argument("--bulkheadMaxConcurrent", type=int, default=6,
                        help="장애 중 가용 커넥션 수 이하여야 구제가 된다")
    arguments = parser.parse_args()

    scenarios = [s.strip() for s in arguments.scenarios.split(",") if s.strip()]
    remediations = [r.strip().upper() for r in arguments.remediations.split(",") if r.strip()]

    cells = []
    for scenario in scenarios:
        for remediation in remediations:
            cells.append(runCell(scenario, remediation, arguments))

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"remediation-{RUN_STAMP}.json"
    path.write_text(json.dumps(cells, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
