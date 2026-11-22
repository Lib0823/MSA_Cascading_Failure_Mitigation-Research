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
import copy
import datetime as dt
import itertools
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
from cohort import DEFAULTS as COHORT_DEFAULTS, cohortTag  # noqa: E402
from hostLoad import (  # noqa: E402
    ForeignCpuSampler, HostLoadSampler, NodeCpuSampler, foreignCpu, powerSource)

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
RUN_STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

REMEDIATION_NODE = "orders"   # 적용 지점 — 위험 노드(payment)의 호출자 (§2-C)
REMEDIATIONS = ("NONE", "BULKHEAD", "CIRCUIT_BREAKER", "BROWNOUT", "SCALE_OUT")
# SCALE_OUT 은 앱이 아니라 **오케스트레이터가** 거는 조치다 — K8s 백엔드에서만 된다
# (2026-11-22, J30). 증설은 위험 노드 자체에 건다. 앱 레벨 조치는 호출자(orders)에
# 걸지만(§2-C), HPA 가 늘리는 것은 과부하인 서비스 자신이다.
INFRA_REMEDIATIONS = {"SCALE_OUT"}
SCALE_OUT_TARGET = "payment"
# `normal` 은 **장애 없이 조치만** 거는 축이다 — 그것이 `Dₐ`(= H5a, 즉시 비용)의
# 정의다(§4-5). runCell 은 scenario 가 s1·s3 가 아니면 주입을 건너뛰므로 자연히
# "장애 없는데 개입" 이 된다. 아래 주석이 지적한 바로 그 상황을 **의도적으로**
# 만드는 것이다.
SCENARIOS = ("s1", "s3")
KNOWN_SCENARIOS = ("normal", "s1", "s3")
# DB max_connections 기본값. S1 의 점유 세션 수(24)와 짝을 이루는 값이다.
DEFAULT_MAX_CONNECTIONS = COHORT_DEFAULTS["maxConnections"]
# payment Tomcat 의 maxKeepAliveRequests 기본값(Spring Boot 3.5 기본값과 같다).
DEFAULT_PAYMENT_KEEP_ALIVE = COHORT_DEFAULTS["paymentKeepAlive"]


def cellConfig(arguments: argparse.Namespace) -> dict:
    """셀을 만든 구성. 원장에 남고, 기본값이 아닌 요인은 코호트 태그가 된다(cohort.py).

    🔴 J31 — 예산을 실험 요인으로 돌리기 시작했다. 요인이 키에 없으면 `--resume` 이 다른
    코호트의 셀을 완료로 보고 건너뛰고, `disruptionByAction` 의 기준선(NONE)에 다른
    코호트의 셀이 섞인다(J21·J23·J24 와 같은 계열의 결함).
    """
    return {
        "maxConnections": arguments.maxConnections,
        "paymentKeepAlive": arguments.paymentKeepAlive,
        "hogSessions": arguments.hogSessions, "workers": arguments.workers,
        "warmup": arguments.warmup, "normalSeconds": arguments.normalSeconds,
        "faultSeconds": arguments.faultSeconds,
        "faultDelayMillis": arguments.faultDelayMillis,
        "bulkheadMaxConcurrent": arguments.bulkheadMaxConcurrent,
        "scaleOutReplicas": arguments.scaleOutReplicas,
    }


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


def runCell(scenario: str, remediation: str, repeat: int,
            arguments: argparse.Namespace) -> dict:
    environment = runChain.buildEnvironment(scenario, arguments)
    environment["BULKHEAD_MAX_CONCURRENT"] = str(arguments.bulkheadMaxConcurrent)
    # 요청 단위 분산을 흉내 낸다(J32) — 1 이면 payment 가 응답마다 연결을 닫아 orders 가
    # 요청마다 새 연결을 열고, kube-proxy 가 요청마다 파드를 고른다.
    environment["PAYMENT_MAX_KEEP_ALIVE_REQUESTS"] = str(arguments.paymentKeepAlive)
    backend = runChain.BACKEND
    config = cellConfig(arguments)
    tag = cohortTag(config)
    # 🔴 파일명에 백엔드·코호트·반복을 넣는다. 같은 RUN_STAMP 로 반복을 돌리면 경로가 겹쳐
    #    덮어쓰고(J24), 백엔드가 없으면 Compose 셀과 구분되지 않는다.
    if backend == "compose" and repeat == 1 and not tag:
        stem = f"{scenario}-{remediation.lower()}-{RUN_STAMP}"   # 종전 형식 유지
    else:
        stem = "-".join([backend, *([tag] if tag else []), scenario,
                         remediation.lower(), f"r{repeat}", RUN_STAMP])
    tracePath = RESULTS_DIR / f"trace-{stem}.jsonl"
    RESULTS_DIR.mkdir(exist_ok=True)

    tagLabel = f" × {tag}" if tag else ""
    print(f"\n━━━ [{backend}] {scenario} × {remediation}{tagLabel} × r{repeat} ━━━", flush=True)
    runChain.composeDown(environment)
    hog = runChain.ConnectionHog(arguments.hogSessions, environment)
    injectedAt = None
    loadResult: dict = {}
    snapshot: dict = {}
    sampleCount = 0
    cluster = None
    watcher = None
    scaledAt = None
    podTraffic = None
    dbConnections = None
    hostLoad = HostLoadSampler()
    foreign = ForeignCpuSampler()
    nodeCpu = NodeCpuSampler() if backend == "k8s" else None
    samplersStarted = False
    try:
        runChain.composeUp(environment)
        if backend == "k8s":
            import k8sBackend   # noqa: PLC0415
            cluster = k8sBackend.clusterFingerprint()
            # 증설 여부와 무관하게 모든 K8s 셀에서 돈다 — 측정 부하를 대칭으로 둔다.
            podTraffic = k8sBackend.PodTrafficSampler(SCALE_OUT_TARGET).start()
            dbConnections = k8sBackend.DbConnectionSampler(environment).start()
        setRemediation("NONE")
        hostLoad.__enter__()
        foreign.__enter__()
        if nodeCpu is not None:
            nodeCpu.__enter__()
        samplersStarted = True

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
            if remediation == "SCALE_OUT":
                import k8sBackend   # noqa: PLC0415
                watcher = k8sBackend.ReplicaWatcher(SCALE_OUT_TARGET).start()
                k8sBackend.scale(SCALE_OUT_TARGET, arguments.scaleOutReplicas)
                scaledAt = time.time()
            elif remediation != "NONE":
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
        # 기동이 실패했으면 샘플러는 시작하지 않았다 — 시작 안 한 스레드는 join 할 수 없다.
        if samplersStarted:
            hostLoad.__exit__(None, None, None)
            foreign.__exit__(None, None, None)
            if nodeCpu is not None:
                nodeCpu.__exit__(None, None, None)
        if watcher is not None:
            watcher.stop()
        if podTraffic is not None:
            podTraffic.stop()
        if dbConnections is not None:
            dbConnections.stop()
        runChain.composeDown(environment)

    scaleOut = None
    if watcher is not None:
        scaleOut = {
            "target": SCALE_OUT_TARGET, "replicas": arguments.scaleOutReplicas,
            "scaledAt": scaledAt,
            # `ℓₐ` — 증설 명령부터 목표 레플리카가 전부 Ready 가 되기까지.
            "leadTimeSeconds": watcher.leadTimeSeconds(scaledAt, arguments.scaleOutReplicas),
            "timeline": watcher.timeline,
        }
    cell = {
        "backend": backend, "repeat": repeat, "powerSource": powerSource(),
        "hostLoad": hostLoad.summary(),
        # 실험과 무관한 프로세스의 CPU — 셀을 사후에 판정한다(J30).
        "foreignCpu": foreign.summary(),
        "nodeCpu": nodeCpu.summary() if nodeCpu is not None else None,
        "cluster": cluster, "scaleOut": scaleOut,
        "podTraffic": podTraffic.rates() if podTraffic is not None else None,
        # 파드별 DB 커넥션 수(J31). 증설의 기전을 추정 없이 본다.
        "dbConnections": dbConnections.samples if dbConnections is not None else None,
        # 🔴 셀을 만든 구성. 이것이 없던 셀은 기본값으로 돌았다(cohort.DEFAULTS).
        "config": config,
        "scenario": scenario, "remediation": remediation,
        "injectedAt": injectedAt, "injectionNode": "payment",
        "remediationNode": REMEDIATION_NODE,
        # 결과 디렉터리가 사슬 밖(스모크 테스트)이면 상대 경로를 만들 수 없다.
        "tracePath": (str(tracePath.relative_to(HERE)) if tracePath.is_relative_to(HERE)
                      else str(tracePath)),
        "normalSeconds": arguments.normalSeconds, "faultSeconds": arguments.faultSeconds,
        "entryLoad": loadResult, "remediationSnapshot": snapshot,
        "nodeSampleCount": sampleCount,
        "callGraph": json.loads((HERE / "callGraph.json").read_text(encoding="utf-8")),
    }
    host = cell["hostLoad"]
    node = cell["nodeCpu"] or {}
    extra = ""
    if scaleOut is not None:
        extra = f" / ℓₐ {scaleOut['leadTimeSeconds']}s"
    print(f"  goodput {loadResult.get('goodputRps', float('nan')):.1f} rps"
          f" / p95 {loadResult.get('latencyP95Millis', float('nan')):.0f} ms"
          f" / err {loadResult.get('errorRate', float('nan')):.1%}"
          f" / 호스트포화 {host.get('saturatedShare', float('nan')):.0%}"
          f" / 외부CPU {cell['foreignCpu'].get('percentMedian', float('nan'))}%"
          + (f" / 노드포화 {node.get('saturatedShare', float('nan')):.0%}"
             f" (CPU {node.get('usageMedianCores', float('nan'))}/{node.get('allocatableCores')})"
             if node else "") + extra, flush=True)
    return cell


def waitForQuietHost(maxLoadRatio: float, timeoutSeconds: float,
                     maxForeignCpu: float | None = None) -> bool:
    """호스트가 조용해질 때까지 기다린다. §4-1 의 측정 유효성 프로토콜.

    **이것이 J17 의 재발 방지다.** 정상 구간 기준선이 호스트 경쟁으로 밀려 올라가면
    `f3`·`f4` 가 그 기준선으로 정규화되어 **feature 가 조용히 틀린다.** Spotlight
    재인덱싱이나 다른 빌드가 도는 중에 재면 쓸 수 없는 데이터가 쌓인다.

    `os.getloadavg()` 를 쓴다 — 의존성이 없고 macOS·Linux 모두에서 돈다.
    """
    coreCount = os.cpu_count() or 1
    ceiling = coreCount * maxLoadRatio
    deadline = time.time() + timeoutSeconds
    while True:
        load = os.getloadavg()[0]
        # 🔴 부하만으로는 부족하다(J30) — 실험 부하에 데몬 1~2 코어가 얹혀도 부하는
        #    상한 아래일 수 있다. 실험과 무관한 프로세스의 CPU 도 함께 본다.
        foreign, top = foreignCpu() if maxForeignCpu is not None else (0.0, [])
        foreignOk = maxForeignCpu is None or foreign <= maxForeignCpu
        if load <= ceiling and foreignOk:
            extra = f" / 외부 CPU {foreign:.0f}% ≤ {maxForeignCpu:.0f}%" if maxForeignCpu else ""
            print(f"✅ 호스트 부하 {load:.2f} / 상한 {ceiling:.2f}"
                  f" ({coreCount}코어 × {maxLoadRatio}){extra} — 측정 시작", flush=True)
            return True
        remaining = deadline - time.time()
        if remaining <= 0:
            print(f"🔴 호스트 부하가 {timeoutSeconds:.0f}초 동안 내려오지 않았다 "
                  f"(현재 {load:.2f} > 상한 {ceiling:.2f}).", flush=True)
            print("   이 상태로 재면 정상 구간 기준선이 오염된다 "
                  "(research/challenges.md J17).", flush=True)
            print("   무엇이 쓰고 있는지 보라: ps -Ao %cpu,comm -r | head", flush=True)
            print("   그래도 돌리려면 --maxLoadRatio 를 올리거나 --skipLoadCheck 를 쓴다.",
                  flush=True)
            return False
        reason = (f"부하 {load:.2f} > {ceiling:.2f}" if load > ceiling
                  else f"외부 CPU {foreign:.0f}% > {maxForeignCpu:.0f}% "
                       f"({', '.join(f'{n} {u:.0f}%' for n, u in top[:3])})")
        print(f"⏳ {reason} — 대기 (남은 {remaining:.0f}초)", flush=True)
        time.sleep(10.0)


def cellKey(scenario: str, remediation: str, repeat: int = 1,
            backend: str = "compose", tag: str = "") -> str:
    """원장의 키. **백엔드·반복·코호트가 키에 들어가야** `--resume` 이 셀을 혼동하지 않는다.

    Compose 의 첫 반복은 종전 형식(`scenario|remediation`)을 유지한다 — 기존 원장의
    완료 셀을 `--resume` 이 그대로 알아보게 하기 위해서다. 코호트도 기본값(빈 태그)이면
    키에 넣지 않는다 — 같은 이유다. 태그는 cohort.cohortTag 가 만든다.
    """
    if backend == "compose" and repeat == 1 and not tag:
        return f"{scenario}|{remediation}"
    key = f"{backend}|{scenario}|{remediation}|r{repeat}"
    return f"{key}|{tag}" if tag else key


def readLedger(path: pathlib.Path) -> dict[str, dict]:
    """완료 기록을 읽는다. 깨진 줄은 건너뛴다 — kill 당한 순간의 반쪽 줄일 수 있다."""
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("cellKey"):
            done[entry["cellKey"]] = entry
    return done


def appendLedger(path: pathlib.Path, entry: dict) -> None:
    """완료 즉시 append + flush + fsync. 중간에 죽어도 끝낸 셀은 남는다."""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    # runCell 이 모듈 수준 RESULTS_DIR 를 쓴다 — --resultsDir 가 그것을 바꾼다.
    global RESULTS_DIR
    parser = argparse.ArgumentParser(description="H5b 조치 오선택 비용 측정")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--remediations", default=",".join(REMEDIATIONS))
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--warmup", type=float, default=60.0)
    parser.add_argument("--normalSeconds", type=float, default=50.0)
    parser.add_argument("--faultSeconds", type=float, default=70.0)
    parser.add_argument("--maxConnections", default=str(DEFAULT_MAX_CONNECTIONS),
                        help="DB max_connections. 쉼표로 여럿(예: 30,70)을 주면 예산이 실험 "
                             "요인이 되어 셀 순서와 함께 섞인다(J31)")
    parser.add_argument("--paymentKeepAlive", default=str(DEFAULT_PAYMENT_KEEP_ALIVE),
                        help="payment Tomcat maxKeepAliveRequests. 1 이면 연결 재사용이 꺼져 "
                             "요청 단위 분산이 된다. 쉼표로 여럿을 주면 요인이 된다(J32)")
    parser.add_argument("--hogSessions", type=int, default=24)
    parser.add_argument("--faultDelayMillis", type=int, default=400)
    # 호스트가 포화인 상태로 재면 기준선이 오염된다(J17). 기본으로 막는다.
    parser.add_argument("--maxLoadRatio", type=float, default=0.5,
                        help="허용 load average = 코어 수 × 이 값")
    parser.add_argument("--loadWaitSeconds", type=float, default=300.0,
                        help="부하가 내려오기를 기다리는 시간")
    parser.add_argument("--skipLoadCheck", action="store_true",
                        help="부하 점검을 건너뛴다 — 오염을 감수한다는 뜻이다")
    parser.add_argument("--maxForeignCpu", type=float, default=100.0,
                        help="셀 시작 시 실험과 무관한 프로세스 CPU 합(%%)의 상한")
    parser.add_argument("--resume", action="store_true",
                        help="원장에 있는 셀은 건너뛴다 — 중단된 실행을 이어 받는다")
    parser.add_argument("--bulkheadMaxConcurrent", type=int, default=6,
                        help="장애 중 가용 커넥션 수 이하여야 구제가 된다")
    parser.add_argument("--backend", choices=runChain.BACKENDS, default="compose",
                        help="사슬을 띄울 곳. k8s 는 deploy/k8s/parity 의 Compose 등가 토폴로지다")
    parser.add_argument("--repeats", type=int, default=1,
                        help="셀마다 반복 횟수. 반복이 있어야 산포를 잴 수 있다")
    parser.add_argument("--scaleOutReplicas", type=int, default=3,
                        help="SCALE_OUT 이 payment 를 몇 레플리카로 늘리는가")
    parser.add_argument("--seed", type=int, default=20261122,
                        help="셀 순서 무작위화 — 시간 흐름의 드리프트가 한 조치에 쏠리지 않게")
    parser.add_argument("--allowBattery", action="store_true",
                        help="🔴 배터리 전원에서도 돈다. 기존 셀(AC)과 비교할 수 없다")
    parser.add_argument("--resultsDir", default=str(RESULTS_DIR),
                        help="원장·트레이스를 쓸 곳. 스모크 테스트는 여기를 바꿔 진짜 원장을 "
                             "건드리지 않는다 — 원장에 남은 셀은 --resume 이 완료로 본다")
    arguments = parser.parse_args()
    RESULTS_DIR = pathlib.Path(arguments.resultsDir).resolve()

    scenarios = [s.strip() for s in arguments.scenarios.split(",") if s.strip()]
    remediations = [r.strip().upper() for r in arguments.remediations.split(",") if r.strip()]
    budgets = [int(b) for b in arguments.maxConnections.split(",") if b.strip()]
    keepAlives = [int(k) for k in arguments.paymentKeepAlive.split(",") if k.strip()]

    unknownScenario = [s for s in scenarios if s not in KNOWN_SCENARIOS]
    unknownRemediation = [r for r in remediations if r not in REMEDIATIONS]
    if unknownScenario or unknownRemediation:
        print(f"❌ 모르는 이름 — 시나리오 {unknownScenario} / 조치 {unknownRemediation}")
        print(f"   가능: 시나리오 {list(KNOWN_SCENARIOS)} / 조치 {list(REMEDIATIONS)}")
        return 1
    infra = [r for r in remediations if r in INFRA_REMEDIATIONS]
    if infra and arguments.backend != "k8s":
        print(f"❌ {infra} 는 오케스트레이터가 거는 조치다 — --backend k8s 에서만 된다")
        return 1
    if "SCALE_OUT" in remediations and "s3" in scenarios:
        # setFault 는 주입 시점의 파드에만 걸린다. 증설로 새로 뜬 파드는 장애 없이 돌아
        # 증설이 인위적으로 "효과 있음" 으로 나온다. S1 은 DB 측 제약이라 모든 파드에
        # 똑같이 걸리므로 문제가 없다.
        print("❌ S3 × SCALE_OUT 은 아직 막아 둔다 — 새로 뜬 파드에 장애가 전파되지 않는다")
        return 1

    power = powerSource()
    if power == "Battery" and not arguments.allowBattery:
        print("🔴 배터리 전원이다. 처리량을 재는 실험이고, 기존 셀은 AC 에서 측정됐다 —")
        print("   전원을 꽂거나, 비교 불가를 감수한다면 --allowBattery 를 쓴다.")
        return 1
    if power == "Battery":
        print("⚠️ 배터리 전원으로 돈다(--allowBattery) — 이 셀은 AC 셀과 비교하지 않는다")

    runChain.useBackend(arguments.backend)
    if arguments.backend == "k8s":
        print(f"백엔드 k8s — 진입점 {runChain.ENTRY_TARGETS[0]}", flush=True)

    if arguments.skipLoadCheck:
        print("⚠️ 부하 점검을 건너뛴다(--skipLoadCheck) — 기준선 오염을 감수한다")
    elif not waitForQuietHost(arguments.maxLoadRatio, arguments.loadWaitSeconds,
                              arguments.maxForeignCpu):
        return 1

    RESULTS_DIR.mkdir(exist_ok=True)
    ledgerPath = RESULTS_DIR / "remediation-ledger.jsonl"
    done = readLedger(ledgerPath)
    def argumentsFor(budget: int, keepAlive: int) -> argparse.Namespace:
        """셀마다 요인만 바꾼 사본 — runChain.buildEnvironment 와 runCell 이 이 값을 읽는다."""
        cellArguments = copy.copy(arguments)
        cellArguments.maxConnections = budget
        cellArguments.paymentKeepAlive = keepAlive
        return cellArguments

    plan = list(itertools.product(scenarios, remediations, budgets, keepAlives,
                                  range(1, arguments.repeats + 1)))
    pending = [(s, r, b, a, k) for s, r, b, a, k in plan
               if cellKey(s, r, k, arguments.backend, cohortTag(cellConfig(argumentsFor(b, a))))
               not in done]
    if arguments.resume and len(pending) < len(plan):
        print(f"원장에서 완료 {len(plan) - len(pending)} 건 확인 → 남은 {len(pending)} 셀")
    elif not arguments.resume:
        pending = plan
    # 순서를 섞는다 — 반복을 조치별로 몰아 돌리면 시간 흐름의 드리프트(발열·전원)가
    # 한 조치에 쏠린다. 시드를 고정해 재현 가능하게 한다(runBatch 와 같은 규칙).
    import random   # noqa: PLC0415
    random.Random(arguments.seed).shuffle(pending)
    print(f"셀 {len(pending)}개 / 셀당 약 "
          f"{(arguments.warmup + arguments.normalSeconds + arguments.faultSeconds) / 60 + 1:.1f}분"
          f" → 예상 {len(pending) * ((arguments.warmup + arguments.normalSeconds + arguments.faultSeconds) / 60 + 1) / 60:.1f}시간\n")

    cells = []
    for scenario, remediation, budget, keepAlive, repeat in pending:
        cellArguments = argumentsFor(budget, keepAlive)
        tag = cohortTag(cellConfig(cellArguments))
        label = f"{scenario}×{remediation}×r{repeat}" + (f"×{tag}" if tag else "")
        # 🔴 셀마다 확인한다(J30). 배치 시작 때 한 번만 보면 90분 사이에 끼어든 데몬을
        #    막지 못한다. 조용해지지 않으면 이 셀을 건너뛴다 — 원장에 남지 않으므로
        #    --resume 이 나중에 다시 돈다.
        if not arguments.skipLoadCheck and not waitForQuietHost(
                arguments.maxLoadRatio, arguments.loadWaitSeconds, arguments.maxForeignCpu):
            print(f"  ⏭ {label} 건너뜀 — 호스트가 조용해지지 않았다", flush=True)
            continue
        try:
            cell = runCell(scenario, remediation, repeat, cellArguments)
        except Exception as error:   # 한 셀의 실패가 나머지를 죽이지 않게 한다
            print(f"  ❌ {label} 실패: {error}", flush=True)
            continue
        cell["cellKey"] = cellKey(scenario, remediation, repeat, arguments.backend, tag)
        cells.append(cell)
        # **셀마다 기록한다.** 종전에는 전부 돌린 뒤 한 번에 썼으므로 중간에
        # 멈추면 그때까지의 작업이 전부 사라졌다(노트북 sleep·kill·전원).
        appendLedger(ledgerPath, cell)

    # 🔴 `remediation-*.json` 은 에피소드 코퍼스 로더(compareTiers.loadAllEpisodes·
    #    episodeValidity.episodeRecords)가 읽는다. K8s 셀이 같은 이름이면 **Compose
    #    코퍼스에 조용히 섞인다.** 접두어를 가르고, 로더도 백엔드를 걸러낸다.
    prefix = "remediation" if arguments.backend == "compose" else f"{arguments.backend}-remediation"
    path = RESULTS_DIR / f"{prefix}-{RUN_STAMP}.json"
    path.write_text(json.dumps(cells, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(cells)} 셀 완료. 기록: {path}")
    print(f"원장(누적): {ledgerPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
