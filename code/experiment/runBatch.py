"""무인 야간 배치 — 정책 × 시나리오 × 반복 행렬을 사람 없이 돌린다.

§4-2 가 *"실험 실행은 무인(unattended) 야간 배치로 수행하며, 사람의 시간은
스크립트 작성과 결과 분석에만 든다"* 고 규정한다. wall-clock 과 사람 시간을
분리하는 것이 주 4시간 예산에서 핵심이다.

무인이 되려면 다섯 가지가 필요하다
===================================

=====================  ====================================================
요구                    없으면 무슨 일이 일어나는가
=====================  ====================================================
**재시작 가능**         3시간 배치가 2시간째 죽으면 **처음부터** 다시 돌린다
**실패 격리**           한 셀이 죽으면 나머지 전부 날아간다
**셀 사이 자원 정리**   다음 셀이 이전 상태에 오염된다
**진행 기록**           아침에 뭐가 됐는지 모른다
**호스트 포화 기록**    §4-1 측정 유효성 — 임계 초과 구간을 라벨링에서 빼야
                       "전파에 기인한 위반"과 구분된다
=====================  ====================================================

원장(ledger)으로 재시작을 구현한다
===================================

셀마다 완료 즉시 JSONL 한 줄을 append 하고 flush 한다. ``--resume`` 이면 원장을
읽어 **완료된 셀을 건너뛴다.** 중간에 kill 돼도 이미 끝낸 셀은 보존된다 —
게이트 ①.5 에서 집계 단계 예외로 25분치 측정을 잃은 적이 있고, 그때 배운 것을
여기에 적용한다.

⚠️ 지금 머신에서 **본 배치를 돌리면 안 된다**
==============================================

맥북에어(팬리스)에서 3시간 full load 는 thermal throttling 을 일으키고, 그것이
**지연 측정을 직접 오염시킨다.** 부하기도 같은 머신이라 호스트 포화가 전파와
구분되지 않는다(§4-1 가짜 양성).

**지금 할 수 있는 것은 오케스트레이터 로직 검증이다** — 재시작과 실패 격리가
되는지 짧게 확인하고 측정값은 버린다. 배치 버그를 데스크톱에서 발견하면
밤을 날린다.

Compose 에서 유의미한 정책은 넷이다
====================================

A1(HPA 만)은 HPA 가 K8s 에만 있어 A4 와 구분되지 않고, A3(Scale-out 고정)은
조치가 전부 ``skipped-needs-k8s`` 라 사실상 A4 다. **A4·A5·A6 비교가 H2 의
본체**이므로 그것만으로도 의미가 있다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import pathlib
import random
import statistics
import sys
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "chain"))
sys.path.insert(0, str(HERE.parent / "calibration"))
sys.path.insert(0, str(HERE.parent / "control"))
sys.path.insert(0, str(HERE.parent / "model"))

import controlLoop  # noqa: E402
import loadGen  # noqa: E402
import policyEngine  # noqa: E402
import metricScraper  # noqa: E402
import runChain  # noqa: E402

RESULTS_DIR = HERE / "results"
LEDGER_NAME = "ledger.jsonl"
PREDICTOR = HERE.parent / "control" / "predictor.joblib"

# Compose 에서 의미가 있는 정책. A1·A3 은 K8s 가 필요하다.
COMPOSE_POLICIES = ("A2", "A4", "A5", "A6")
# 주입이 구현된 시나리오. 여기 없는 이름은 **조용히 "주입 없음"으로 돌아간다** —
# --scenarios 오타 하나로 장애 없는 데이터가 유효한 것처럼 쌓이므로 미리 막는다.
KNOWN_SCENARIOS = ("normal", "s1", "s3")


class HostLoadSampler:
    """호스트 포화를 기록한다. §4-1 의 측정 유효성 프로토콜.

    `os.getloadavg()` 를 쓴다 — 의존성이 없고 macOS·Linux 모두에서 돈다.
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


def profileFingerprint() -> dict:
    """셀을 만든 **엔진 파라미터**를 기록한다.

    🔴 2026-11-02 신설. 종전 원장에는 이것이 없었다. `Dₐ` 를 배선하고 A5 를 다시
    돌리면 **배선 전 셀과 구분이 되지 않아** 집계가 두 코호트를 섞는다. `cellKey`
    가 같으므로 원장만 보고는 알 수 없다 — 조용히 틀린 숫자가 나오는 경로다.

    지문은 `(조치, 노드종류, Dₐ, Rₐ, mₐ)` 전체의 해시다. 값 하나만 바뀌어도
    달라지므로 코호트 분리의 기준이 된다.
    """
    profiles = sorted(
        (p.name, p.nodeType, p.disruption, p.reversibility,
         tuple(sorted(p.mitigationByBottleneck.items())))
        for p in policyEngine.defaultProfiles())
    digest = hashlib.sha256(repr(profiles).encode("utf-8")).hexdigest()[:12]
    return {
        "digest": digest,
        "disruption": {f"{p.name}/{p.nodeType}": p.disruption
                       for p in policyEngine.defaultProfiles()},
    }


def cellKey(policy: str, scenario: str, repeat: int) -> str:
    return f"{policy}|{scenario}|{repeat}"


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
        done[entry["cellKey"]] = entry
    return done


def appendLedger(path: pathlib.Path, entry: dict) -> None:
    """완료 즉시 append + flush. 중간에 죽어도 끝낸 셀은 남는다."""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def runCell(policy: str, scenario: str, repeat: int, arguments) -> dict:
    """셀 하나. 기동부터 정리까지 한 번 돌린다."""
    key = cellKey(policy, scenario, repeat)
    environment = runChain.buildEnvironment(scenario, arguments)
    # 🔴 파일명에 엔진 지문을 넣는다 (2026-11-02). 넣지 않으면 `Dₐ` 를 배선하고
    #    다시 돌릴 때 **같은 경로를 덮어쓴다.** 원장의 배선 전 셀이 가리키는
    #    `decisionPath` 가 배선 후 파일이 되어, 그 셀을 집계하면 조용히 틀린다.
    #    원장 항목의 `actionProfiles.digest` 와 같은 값이다.
    digest = profileFingerprint()["digest"]
    stem = f"{policy}-{scenario}-r{repeat}-{digest}"
    tracePath = RESULTS_DIR / f"trace-{stem}.jsonl"
    decisionPath = RESULTS_DIR / f"decisions-{stem}.jsonl"

    # 셀 사이를 확실히 정리한다. 안 하면 이전 상태가 남아 다음 셀을 오염시킨다.
    runChain.composeDown(environment)
    hog = runChain.ConnectionHog(arguments.hogSessions, environment)
    loadResult: dict = {}
    loopSummary: dict = {}
    injectedAt = None

    try:
        runChain.composeUp(environment)
        # 루프의 기준선 확정 시점을 부하 워밍업 끝에 맞춘다. 어긋나면 기준선이
        # 워밍업 구간의 지연 급등을 품게 된다.
        warmupTicks = max(3, int(arguments.warmup / arguments.loopPeriodSeconds) - 1)

        def driveLoad() -> None:
            loadResult.update(loadGen.runLoad(
                targets=runChain.ENTRY_TARGETS, workerCount=arguments.workers,
                durationSeconds=arguments.normalSeconds + arguments.faultSeconds,
                warmupSeconds=arguments.warmup))

        loadThread = threading.Thread(target=driveLoad, daemon=True)
        loop = controlLoop.ControlLoop(
            artifactPath=PREDICTOR, nodes=runChain.NODE_ENDPOINTS,
            callGraph=json.loads((HERE.parent / "chain" / "callGraph.json")
                                 .read_text(encoding="utf-8")),
            policy=policy, periodSeconds=arguments.loopPeriodSeconds,
            warmupTicks=warmupTicks, predictionLeadSeconds=arguments.horizonSeconds)

        with HostLoadSampler() as hostLoad, \
             metricScraper.ChainScraper(runChain.NODE_ENDPOINTS, tracePath) as scraper, \
             loop:
            loadThread.start()
            time.sleep(arguments.warmup + arguments.normalSeconds)

            injectedAt = time.time()
            if scenario == "s1":
                hog.start()
            elif scenario == "s3":
                runChain.setFault("payment", arguments.faultDelayMillis)
            loadThread.join(timeout=arguments.faultSeconds + 120)
            sampleCount = scraper.sampleCount

        loopSummary = loop.summary()
        loop.writeDecisions(decisionPath)
        hog.stop()
        status = "done"
        error = None
    except Exception as exception:  # noqa: BLE001 — 실패 격리. 배치는 계속 돌아야 한다.
        status = "failed"
        error = f"{type(exception).__name__}: {exception}"
        traceback.print_exc()
        sampleCount = 0
        hostLoad = None
    finally:
        runChain.composeDown(environment)

    return {
        "cellKey": key, "policy": policy, "scenario": scenario, "repeat": repeat,
        "status": status, "error": error,
        "recordedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "injectedAt": injectedAt, "injectionNode": "payment",
        "tracePath": str(tracePath.relative_to(HERE)),
        "decisionPath": str(decisionPath.relative_to(HERE)) if status == "done" else None,
        "normalSeconds": arguments.normalSeconds, "faultSeconds": arguments.faultSeconds,
        "nodeSampleCount": sampleCount,
        "entryLoad": loadResult, "loopSummary": loopSummary,
        "hostLoad": hostLoad.summary() if hostLoad is not None else None,
        "callGraph": json.loads((HERE.parent / "chain" / "callGraph.json")
                                .read_text(encoding="utf-8")),
        # 🔴 엔진 파라미터의 지문. 이것이 다르면 다른 코호트다 (profileFingerprint 주석).
        "actionProfiles": profileFingerprint(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="무인 야간 배치")
    parser.add_argument("--policies", default=",".join(COMPOSE_POLICIES))
    parser.add_argument("--scenarios", default="s1,s3")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--resume", action="store_true",
                        help="원장을 읽어 완료된 셀을 건너뛴다")
    parser.add_argument("--dryRun", action="store_true", help="행렬만 보여주고 끝낸다")
    parser.add_argument("--ledger", default=str(RESULTS_DIR / LEDGER_NAME))
    # 셀 파라미터
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--warmup", type=float, default=60.0)
    parser.add_argument("--normalSeconds", type=float, default=50.0)
    parser.add_argument("--faultSeconds", type=float, default=70.0)
    parser.add_argument("--maxConnections", type=int, default=30)
    parser.add_argument("--hogSessions", type=int, default=20)
    parser.add_argument("--faultDelayMillis", type=int, default=400)
    parser.add_argument("--loopPeriodSeconds", type=float, default=5.0)
    parser.add_argument("--horizonSeconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=20261016)
    arguments = parser.parse_args()

    if not PREDICTOR.exists():
        print(f"예측기가 없다: {PREDICTOR}")
        print("먼저 돌린다: python3 code/control/trainPredictor.py")
        return 1

    RESULTS_DIR.mkdir(exist_ok=True)
    ledgerPath = pathlib.Path(arguments.ledger)
    policies = [p.strip() for p in arguments.policies.split(",") if p.strip()]
    scenarios = [s.strip() for s in arguments.scenarios.split(",") if s.strip()]

    # 🔒 이름을 먼저 검증한다. 모르는 시나리오는 주입이 없는 정상 에피소드로
    # 조용히 돌아가므로, 오타가 데이터로 남는 것이 가장 위험한 실패다.
    unknownScenarios = [s for s in scenarios if s not in KNOWN_SCENARIOS]
    if unknownScenarios:
        print(f"알 수 없는 시나리오: {unknownScenarios}  (가능: {list(KNOWN_SCENARIOS)})")
        print("주입이 구현되지 않은 이름은 '주입 없음'으로 조용히 돌아가므로 거부한다")
        return 1
    unknownPolicies = [p for p in policies if p not in controlLoop.POLICIES]
    if unknownPolicies:
        print(f"알 수 없는 정책: {unknownPolicies}  (가능: {list(controlLoop.POLICIES)})")
        return 1

    cells = [(p, s, r) for p, s, r
             in itertools.product(policies, scenarios, range(1, arguments.repeats + 1))]
    # 순서를 섞는다. 고정 순서로 돌리면 머신이 더워지는 추세가 뒤쪽 조건에만 실린다.
    random.Random(arguments.seed).shuffle(cells)

    done = readLedger(ledgerPath) if arguments.resume else {}
    # 🔴 `--resume` 은 **같은 엔진 파라미터로 만든 셀만** 완료로 본다 (2026-11-02).
    #    `cellKey` 만 보면, `Dₐ` 를 배선하고 다시 돌릴 때 배선 전 셀이 "완료" 로
    #    잡혀 **한 셀도 돌지 않는다.** 조용히 아무것도 안 하는 경로다.
    currentDigest = profileFingerprint()["digest"]
    completed = {k for k, v in done.items()
                 if v["status"] == "done"
                 and ((v.get("actionProfiles") or {}).get("digest")) == currentDigest}
    staleSkipped = sum(1 for k, v in done.items()
                       if v["status"] == "done"
                       and ((v.get("actionProfiles") or {}).get("digest")) != currentDigest)
    pending = [c for c in cells if cellKey(*c) not in completed]

    perCellSeconds = arguments.warmup + arguments.normalSeconds + arguments.faultSeconds + 55
    print(f"행렬 {len(cells)} 셀 (정책 {len(policies)} × 시나리오 {len(scenarios)} "
          f"× 반복 {arguments.repeats})")
    if arguments.resume:
        print(f"원장에서 완료 {len(completed)} 건 확인 → 남은 {len(pending)} 셀")
        print(f"엔진 지문 {currentDigest}")
        if staleSkipped:
            print(f"  ⚠️ 다른 엔진 파라미터로 만든 완료 셀 {staleSkipped} 건은 "
                  f"**완료로 보지 않는다** — θ 가 다르므로 다시 돌려야 한다")
    print(f"셀당 약 {perCellSeconds / 60:.1f}분 → 예상 {len(pending) * perCellSeconds / 3600:.1f}시간")
    unsupported = [p for p in policies if p not in COMPOSE_POLICIES]
    if unsupported:
        print(f"⚠️ Compose 에서 의미 없는 정책: {unsupported} — A1 은 HPA 가 K8s 에만")
        print("   있어 A4 와 구분되지 않고, A3 은 조치가 전부 skipped 된다")
    print()

    if arguments.dryRun:
        for policy, scenario, repeat in pending:
            print(f"  {cellKey(policy, scenario, repeat)}")
        return 0

    startedAt = time.time()
    failures = 0
    for index, (policy, scenario, repeat) in enumerate(pending, start=1):
        elapsed = time.time() - startedAt
        print(f"[{index}/{len(pending)}] {policy} × {scenario} × r{repeat}"
              f"   (경과 {elapsed / 60:.0f}분)", flush=True)
        entry = runCell(policy, scenario, repeat, arguments)
        appendLedger(ledgerPath, entry)
        if entry["status"] == "done":
            load = entry["entryLoad"]
            loop = entry["loopSummary"]
            host = entry["hostLoad"] or {}
            print(f"    goodput {load.get('goodputRps', float('nan')):.1f} rps"
                  f" / p95 {load.get('latencyP95Millis', float('nan')):.0f} ms"
                  f" / 결정 {loop.get('decisions', 0)}"
                  f" / 보류 {loop.get('holdShare', float('nan')):.0%}"
                  f" / 호스트포화 {host.get('saturatedShare', float('nan')):.0%}", flush=True)
        else:
            failures += 1
            print(f"    ❌ 실패: {entry['error']} — 배치는 계속한다", flush=True)

    print(f"\n완료 {len(pending) - failures} / 실패 {failures}"
          f"   총 {(time.time() - startedAt) / 60:.0f}분")
    print(f"원장: {ledgerPath}")
    if failures:
        print("실패한 셀은 --resume 으로 다시 돌릴 수 있다 (완료분은 건너뛴다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
