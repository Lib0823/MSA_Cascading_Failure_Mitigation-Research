"""게이트 ①.5 — S1 미니 캘리브레이션 실행기.

확인하려는 것은 **부호 하나**다: S1(DB 커넥션 고갈) 조건에서 인스턴스를 2→4 로 늘리면
성능이 나빠지는가 (``m_scale-out < 0``, H4 전반부). 절대 수치는 목표가 아니며, 이 답을
K8s 구축(60h)에 착수하기 **전에** 얻는 것이 이 게이트의 존재 이유다.

두 단계로 나뉜다.

``band``
    **1 단계 — 부하 구간 탐색.** 대조군(DB 미제약)만 돌려서 2→4 가 개선되는 부하 수준을
    찾는다. 이 구간이 없으면 실험군의 악화를 DB 에 귀속시킬 수 없다 — 인스턴스를 늘리면
    호스트 CPU 경합만으로도 악화되기 때문이다. 어떤 부하에서도 대조군이 개선되지 않으면
    **이 머신에서는 판정 불가**로 결론내고 실험 데스크톱으로 이월한다.

``compare``
    **2 단계 — 귀속.** ``band`` 가 찾은 부하에서 대조군·실험군 × 2·4 인스턴스 네 칸을
    반복 실행한다. 대조군에서는 개선되고 실험군에서만 악화될 때 비로소 부호가
    DB 에 귀속된다.

실행 예:
    python3 runCalibration.py band --workers 16,32,64,128
    python3 runCalibration.py compare --workers 64 --repeats 3
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import subprocess
import sys
import threading

import loadGen

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
COMPOSE_PROJECT = "gate15"
RUN_STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

# 군의 정의는 max_connections 하나뿐이다. DB CPU 상한까지 군마다 다르게 주면
# 조건별 호스트 CPU 총량이 달라져 대조군이 대조군 역할을 하지 못한다.
#
# 대조군 값은 "절대 한계가 아닌" 수준이면 된다. 실험군 값이 캘리브레이션의 본체다 —
# 인스턴스 4개의 총 풀 요구(4 x maximumPoolSize)는 넘고 2개의 요구는 넘지 않는
# 구간이 부호를 가장 선명하게 만든다. 기본값 25 는 docs/proposal.md §4-5 의 예시값이며,
# 그 값에서 2 인스턴스도 이미 고갈된다면 --treatMaxConnections 로 올려 가며 찾는다.
CONTROL_MAX_CONNECTIONS = "400"
DEFAULT_TREAT_MAX_CONNECTIONS = 25

groupMaxConnections = {"control": CONTROL_MAX_CONNECTIONS,
                       "treat": str(DEFAULT_TREAT_MAX_CONNECTIONS)}

SCALE_PROFILES = {2: "s2", 4: "s4"}
SCALE_TARGETS = {
    2: ["127.0.0.1:18081", "127.0.0.1:18082"],
    4: ["127.0.0.1:18081", "127.0.0.1:18082", "127.0.0.1:18083", "127.0.0.1:18084"],
}

# 대조군에서 2→4 가 "개선됐다"고 인정할 최소 폭. 측정 잡음을 개선으로 읽지 않기 위한 값이다.
IMPROVEMENT_THRESHOLD = 0.05


def composeCommand(profile: str, *arguments: str) -> list[str]:
    return [
        "docker", "compose",
        "-p", COMPOSE_PROJECT,
        "-f", str(HERE / "docker-compose.yml"),
        "--profile", profile,
        *arguments,
    ]


def buildEnvironment(group: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment["DB_MAX_CONNECTIONS"] = groupMaxConnections[group]
    environment.update(extra or {})
    return environment


def composeUp(group: str, scale: int, environment: dict[str, str]) -> None:
    # 진행 로그는 삼키고 실패했을 때만 꺼낸다. 조건이 12~24 회 반복되므로
    # compose 의 컨테이너별 진행 출력이 섞이면 판정 표를 읽을 수 없다.
    completed = subprocess.run(
        composeCommand(SCALE_PROFILES[scale], "up", "-d", "--wait", "--wait-timeout", "180"),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise RuntimeError(f"compose up 실패 ({group}@{scale}x, exit {completed.returncode})")


def composeDown(scale: int, environment: dict[str, str]) -> None:
    subprocess.run(
        composeCommand(SCALE_PROFILES[scale], "down", "-v", "--remove-orphans"),
        env=environment,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def readBackendCount(environment: dict[str, str]) -> int | None:
    """DB 가 지금 들고 있는 백엔드(커넥션) 수를 읽는다.

    결과의 부호만 보면 "느려졌다"까지밖에 말할 수 없다. 총 커넥션 수가 인스턴스 수에
    따라 실제로 올라갔는지를 함께 봐야 **기전**을 주장할 수 있다.
    """
    query = "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
    completed = subprocess.run(
        composeCommand(
            "s2", "exec", "-T", "db",
            "psql", "-U", environment.get("DB_USER", "cascade"),
            "-d", environment.get("DB_NAME", "cascade"),
            "-tAc", query,
        ),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


class BackendSampler:
    """부하 중 DB 백엔드 수를 주기적으로 표집한다."""

    def __init__(self, environment: dict[str, str], intervalSeconds: float = 3.0) -> None:
        self.environment = environment
        self.intervalSeconds = intervalSeconds
        self.samples: list[int] = []
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stopFlag.wait(self.intervalSeconds):
            count = readBackendCount(self.environment)
            if count is not None:
                self.samples.append(count)

    def __enter__(self) -> "BackendSampler":
        self.thread.start()
        return self

    def __exit__(self, *exceptionInfo: object) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=10)

    def summary(self) -> dict:
        if not self.samples:
            return {"backendSampleCount": 0}
        return {
            "backendSampleCount": len(self.samples),
            "backendPeak": max(self.samples),
            "backendMean": round(sum(self.samples) / len(self.samples), 1),
        }


def runCell(
    group: str,
    scale: int,
    workerCount: int,
    durationSeconds: float,
    warmupSeconds: float,
    resultName: str,
) -> dict:
    """한 칸(군 × 인스턴스 수)을 기동부터 정리까지 한 번 측정한다."""
    environment = buildEnvironment(group)
    label = f"{group}@{scale}x w={workerCount}"
    print(f"  ▶ {label} 기동", flush=True)
    composeDown(4, environment)
    try:
        composeUp(group, scale, environment)
        with BackendSampler(environment) as sampler:
            summary = loadGen.runLoad(
                targets=SCALE_TARGETS[scale],
                workerCount=workerCount,
                durationSeconds=durationSeconds,
                warmupSeconds=warmupSeconds,
            )
        summary.update(sampler.summary())
    finally:
        composeDown(scale, environment)

    summary.update({
        "group": group,
        "scale": scale,
        "maxConnections": int(groupMaxConnections[group]),
        "recordedAt": dt.datetime.now().isoformat(timespec="seconds"),
    })
    appendMeasurement(resultName, summary)
    print(
        f"    goodput {summary['goodputRps']:.1f} rps"
        f" / p95 {summary['latencyP95Millis']:.0f} ms"
        f" / err {summary['errorRate']:.1%}"
        f" / backends peak {summary.get('backendPeak', '-')}",
        flush=True,
    )
    return summary


def relativeChange(valueAtTwo: float, valueAtFour: float) -> float:
    """2→4 의 상대 변화. 양수면 늘었다는 뜻이며, 지표에 따라 좋고 나쁨이 갈린다."""
    if not valueAtTwo:
        return float("nan")
    return (valueAtFour - valueAtTwo) / valueAtTwo


def writeResults(name: str, payload: dict) -> pathlib.Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}-{RUN_STAMP}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def appendMeasurement(name: str, summary: dict) -> None:
    """칸 하나가 끝날 때마다 즉시 디스크에 남긴다.

    전체 실행이 12~25 분이므로 리포트 단계에서 예외가 나면 그 시간이 통째로 날아간다.
    실제로 첫 실행이 집계 중 KeyError 로 죽어 측정 8건을 잃었다. 원시 측정과 판정은
    수명이 달라야 한다.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}-{RUN_STAMP}.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(summary, ensure_ascii=False) + "\n")


def commandBand(arguments: argparse.Namespace) -> int:
    """1 단계 — 대조군에서 2→4 가 개선되는 부하 구간을 찾는다."""
    print("═══ 1 단계: 부하 구간 탐색 (대조군만) ═══")
    print("찾는 것: 2→4 증설이 실제로 도움이 되는 부하 수준.")
    print("없으면 이 머신에서는 판정 불가다 — 실험군의 악화를 DB 에 귀속시킬 수 없다.\n")

    rows = []
    for workerCount in arguments.workers:
        print(f"[부하 {workerCount} VU]")
        atTwo = runCell("control", 2, workerCount, arguments.duration, arguments.warmup, "band")
        atFour = runCell("control", 4, workerCount, arguments.duration, arguments.warmup, "band")
        goodputChange = relativeChange(atTwo["goodputRps"], atFour["goodputRps"])
        p95Change = relativeChange(atTwo["latencyP95Millis"], atFour["latencyP95Millis"])
        improved = goodputChange > IMPROVEMENT_THRESHOLD
        rows.append({
            "workerCount": workerCount,
            "goodputChange": goodputChange,
            "p95Change": p95Change,
            "improved": improved,
            "atTwo": atTwo,
            "atFour": atFour,
        })
        print(
            f"  → goodput {goodputChange:+.1%} / p95 {p95Change:+.1%}"
            f"  {'✅ 개선' if improved else '❌ 개선 없음'}\n",
            flush=True,
        )

    usable = [row for row in rows if row["improved"]]
    print("─── 1 단계 결과 ───")
    for row in rows:
        mark = "✅" if row["improved"] else "❌"
        print(f"  {mark} {row['workerCount']:>4} VU   goodput {row['goodputChange']:+.1%}")

    if usable:
        best = max(usable, key=lambda row: row["goodputChange"])
        print(f"\n사용 가능한 부하 구간: {[row['workerCount'] for row in usable]}")
        print(f"권장: --workers {best['workerCount']} 로 2 단계(compare) 진행")
        verdict = "band-found"
    else:
        print("\n⚠️ 대조군이 2→4 에서 개선되는 구간이 없다.")
        print("   이 머신에서는 판정 불가다. 실험 데스크톱으로 이월하고 여기서 멈춘다.")
        print("   (호스트 코어가 4 인스턴스 + DB 를 감당하지 못하는 것이 가장 흔한 원인이다)")
        verdict = "undecidable-on-this-host"

    path = writeResults("band", {"verdict": verdict, "rows": rows})
    print(f"\n기록: {path}")
    return 0


def commandCompare(arguments: argparse.Namespace) -> int:
    """2 단계 — 네 칸을 반복 측정해 부호를 DB 에 귀속시킨다."""
    groupMaxConnections["treat"] = str(arguments.treatMaxConnections)
    print("═══ 2 단계: 대조군 대비 귀속 ═══")
    print(f"부하 {arguments.workers} VU, {arguments.repeats} 회 반복")
    print(f"max_connections  대조군 {CONTROL_MAX_CONNECTIONS} / 실험군 {arguments.treatMaxConnections}\n")

    cells = [(group, scale) for group in ("control", "treat") for scale in (2, 4)]
    measurements: dict[tuple[str, int], list[dict]] = {cell: [] for cell in cells}
    randomizer = random.Random(arguments.seed)

    for roundIndex in range(arguments.repeats):
        # 라운드마다 순서를 섞는다. 고정 순서로 돌리면 머신이 더워지는 추세가
        # 뒤쪽 조건에만 실린다.
        order = cells[:]
        randomizer.shuffle(order)
        print(f"[라운드 {roundIndex + 1}/{arguments.repeats}] 순서 {order}")
        for group, scale in order:
            measurements[(group, scale)].append(
                runCell(group, scale, arguments.workers, arguments.duration,
                        arguments.warmup, "compare")
            )
        print()

    def median(cell: tuple[str, int], key: str) -> float:
        # backendPeak 은 표집이 한 번도 성공하지 못하면 빠진다. 그 경우에도
        # 본체인 goodput 판정은 계속돼야 하므로 nan 으로 흘려보낸다.
        values = sorted(run.get(key, float("nan")) for run in measurements[cell])
        return values[len(values) // 2]

    report = {}
    for group in ("control", "treat"):
        goodputChange = relativeChange(
            median((group, 2), "goodputRps"), median((group, 4), "goodputRps")
        )
        p95Change = relativeChange(
            median((group, 2), "latencyP95Millis"), median((group, 4), "latencyP95Millis")
        )
        report[group] = {
            "goodputChange": goodputChange,
            "p95Change": p95Change,
            "errorRateAtTwo": median((group, 2), "errorRate"),
            "errorRateAtFour": median((group, 4), "errorRate"),
            "backendPeakAtTwo": median((group, 2), "backendPeak"),
            "backendPeakAtFour": median((group, 4), "backendPeak"),
        }

    # 반복 수가 적으므로 중앙값만 보이면 칸 사이 분산이 가려진다. 원시값을 함께 찍어
    # 두 칸의 범위가 겹치는지를 눈으로 확인할 수 있게 한다.
    print("─── 칸별 원시 측정 ───")
    print(f"{'조건':<12}{'goodput (rps)':<28}{'p95 (ms)':<26}{'backend peak'}")
    for group, scale in cells:
        runs = measurements[(group, scale)]
        goodputs = " ".join(f"{run['goodputRps']:.0f}" for run in runs)
        p95s = " ".join(f"{run['latencyP95Millis']:.0f}" for run in runs)
        backends = " ".join(str(run.get("backendPeak", "-")) for run in runs)
        print(f"{group + '@' + str(scale) + 'x':<12}{goodputs:<28}{p95s:<26}{backends}")

    print("\n─── 2→4 변화 (중앙값) ───")
    print(f"{'군':<10}{'goodput':>12}{'p95':>10}{'err@2':>9}{'err@4':>9}{'backend 2→4':>16}")
    for group in ("control", "treat"):
        row = report[group]
        print(
            f"{group:<10}{row['goodputChange']:>+11.1%}{row['p95Change']:>+9.1%}"
            f"{row['errorRateAtTwo']:>9.1%}{row['errorRateAtFour']:>9.1%}"
            f"{row['backendPeakAtTwo']:>7.0f} → {row['backendPeakAtFour']:<6.0f}"
        )

    controlImproved = report["control"]["goodputChange"] > IMPROVEMENT_THRESHOLD
    # 악화 판정을 goodput 하나에 걸지 않는다. 폐루프 부하에서 실험군은 커넥션 상한에
    # 묶여 처리량이 양쪽 다 바닥에 붙어 버릴 수 있고, 그때 증설의 해악은 처리량이 아니라
    # 대기시간으로 나타난다. 실제로 첫 실행에서 goodput 은 -16% 로 분산에 묻혔지만
    # p95 는 1.0s → 2.0s 로 두 라운드 모두 겹침 없이 갈렸다.
    goodputDegraded = report["treat"]["goodputChange"] < -IMPROVEMENT_THRESHOLD
    p95Degraded = report["treat"]["p95Change"] > 2 * IMPROVEMENT_THRESHOLD
    treatDegraded = goodputDegraded or p95Degraded
    degradationSignals = [
        name for name, fired in (("goodput", goodputDegraded), ("p95", p95Degraded)) if fired
    ]

    print("\n─── 판정 ───")
    if not controlImproved:
        verdict = "undecidable"
        print("⚠️ 판정 불가 — 대조군이 2→4 에서 개선되지 않았다.")
        print("   실험군이 악화됐더라도 그것이 DB 때문인지 CPU 경합 때문인지 구분할 수 없다.")
        print("   1 단계(band)로 돌아가 부하 수준을 다시 잡는다.")
    elif treatDegraded:
        verdict = "h4-reproduced"
        print("✅ H4 전반부 재현 — m_scale-out < 0")
        print("   대조군은 2→4 에서 개선되는데 실험군만 악화됐다. 효과는 DB 제약에 귀속된다.")
        print(f"   악화 신호: {', '.join(degradationSignals)}")
        if "goodput" not in degradationSignals:
            print("   ⚠️ 처리량은 갈리지 않고 대기시간만 갈렸다. 논문에 쓸 때 근거 지표를")
            print("      p95 로 명시해야 하며, 처리량 기준으로 서술하면 재현되지 않는다.")
        print("   → K8s 구축(게이트 ②)으로 진행한다.")
    else:
        verdict = "h4-not-reproduced"
        print("❌ H4 전반부 미재현 — 실험군도 2→4 에서 악화되지 않았다.")
        print("   S1 정의를 재설계한다. 먼저 볼 손잡이:")
        print("   --treatMaxConnections 조정 (4 인스턴스 요구는 넘고 2 인스턴스 요구는")
        print("   넘지 않는 값), DB_SLEEP_MILLIS↑ (커넥션 점유시간), HIKARI_MIN_IDLE↑")
        print("   → 이 답을 K8s 60h 이전에 얻은 것이 이 게이트의 성과다.")

    payload = {
        "verdict": verdict,
        "workerCount": arguments.workers,
        "repeats": arguments.repeats,
        "report": report,
        "degradationSignals": degradationSignals,
        "measurements": {f"{group}@{scale}": runs for (group, scale), runs in measurements.items()},
    }
    path = writeResults("compare", payload)
    print(f"\n기록: {path}")
    return 0


def parseWorkerList(value: str) -> list[int]:
    return [int(part) for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="게이트 ①.5 S1 미니 캘리브레이션")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bandParser = subparsers.add_parser("band", help="1 단계 — 대조군 부하 구간 탐색")
    bandParser.add_argument("--workers", type=parseWorkerList, default=[32, 64, 96, 128])
    bandParser.add_argument("--duration", type=float, default=60.0)
    bandParser.add_argument("--warmup", type=float, default=15.0)
    bandParser.set_defaults(handler=commandBand)

    compareParser = subparsers.add_parser("compare", help="2 단계 — 대조군 대비 귀속")
    compareParser.add_argument("--workers", type=int, required=True)
    compareParser.add_argument("--repeats", type=int, default=3)
    compareParser.add_argument("--duration", type=float, default=60.0)
    compareParser.add_argument("--warmup", type=float, default=15.0)
    compareParser.add_argument(
        "--treatMaxConnections", type=int, default=DEFAULT_TREAT_MAX_CONNECTIONS,
        help="실험군 PostgreSQL max_connections. 부호가 안 나오면 먼저 움직일 손잡이다")
    compareParser.add_argument("--seed", type=int, default=20260927)
    compareParser.set_defaults(handler=commandCompare)

    arguments = parser.parse_args()
    try:
        return arguments.handler(arguments)
    except KeyboardInterrupt:
        print("\n중단됨 — 컨테이너를 정리한다", file=sys.stderr)
        composeDown(4, buildEnvironment("control"))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
