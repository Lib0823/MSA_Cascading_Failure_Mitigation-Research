"""S1↔S3 증상 동형성 실험 (H5b) + 전파성 판정용 시계열 생성.

사슬은 ``gateway → orders → payment → postgres`` 이고, 장애는 전부 **payment** 에
주입한다. 두 시나리오는 주입 기전이 다르지만 **상류에서 보면 같은 증상**(하류가
느려짐)을 내야 한다 — 그것이 H5b 의 전제다.

====  ==========================================  ==========================
시나리오  주입 기전                                  상류가 보는 것
====  ==========================================  ==========================
S1    DB 커넥션 예산을 외부에서 점유해 payment 의     payment 응답 지연 ↑
      풀이 굶는다                                  (에러율 ↑ 가능)
S3    payment 의 필터가 응답을 지연시킨다            payment 응답 지연 ↑
                                                  (에러율 ↑ 가능)
====  ==========================================  ==========================

**S1 을 커넥션 점유로 주입하는 이유**: ``max_connections`` 는 재기동 없이 못 바꾸는데,
전파성 판정(§4-2)은 에피소드 **중간의 onset** 을 요구한다. 외부에서 세션을 쥐는 방식은
런타임에 걸 수 있을 뿐 아니라 게이트 ①.5 에서 확인한 기전 — *"총 커넥션 요구가
max_connections 를 넘으면 풀이 굶는다"* — 과 동일하다. 실제 실험에서 그 요구를 만드는
것은 HPA 가 늘린 레플리카들이고, 여기서는 그 역할을 점유 세션이 대신한다.

에피소드 구성:

    [ 워밍업 ] [ 정상 구간 ] [ ⚡주입 ] [ 장애 구간 ]
                             ^ injectedAt — 라벨 역산의 기준 시각
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "calibration"))

import loadGen  # noqa: E402  (calibration 의 폐루프 부하기를 재사용한다)
import metricScraper  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
COMPOSE_PROJECT = "chain"
RUN_STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

NODE_ENDPOINTS = {
    "gateway": "http://127.0.0.1:18091",
    "orders": "http://127.0.0.1:18092",
    "payment": "http://127.0.0.1:18093",
}
ENTRY_TARGETS = ["127.0.0.1:18091"]

SCENARIOS = ("normal", "s1", "s3")


def composeCommand(*arguments: str) -> list[str]:
    return ["docker", "compose", "-p", COMPOSE_PROJECT,
            "-f", str(HERE / "docker-compose.yml"), *arguments]


def composeUp(environment: dict[str, str]) -> None:
    completed = subprocess.run(
        composeCommand("up", "-d", "--wait", "--wait-timeout", "240"),
        env=environment, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout + completed.stderr)
        raise RuntimeError(f"compose up 실패 (exit {completed.returncode})")


def composeDown(environment: dict[str, str]) -> None:
    subprocess.run(composeCommand("down", "-v", "--remove-orphans"),
                   env=environment, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def setFault(node: str, delayMillis: int) -> None:
    """필터 지연을 런타임에 바꾼다 (S3 주입)."""
    url = NODE_ENDPOINTS[node] + "/fault?" + urllib.parse.urlencode({"delayMillis": delayMillis})
    request = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


class ConnectionHog:
    """DB 커넥션 예산을 외부에서 점유한다 (S1 주입).

    ``psql`` 세션을 띄워 트랜잭션을 연 채로 붙잡아 둔다. payment 의 풀이 새 커넥션을
    열려 할 때 남은 슬롯이 없어 획득 대기가 쌓이는 것이 S1 의 기전이다.
    """

    def __init__(self, sessionCount: int, environment: dict[str, str]) -> None:
        self.sessionCount = sessionCount
        self.environment = environment
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        """커넥션 예산을 뺏는다 — 끊고, 곧바로 점유한다.

        **점유만 해서는 물지 않는다.** payment 의 Hikari 풀은 워밍업·정상 구간을 지나며
        이미 커넥션을 쥐고 있어서, 뒤늦게 들어온 점유 세션은 남은 슬롯만 가져가고 끝난다.
        첫 실행에서 S1 의 goodput 이 정상보다 **높게** 나온 것이 이 때문이다.

        이것은 하네스 결함이자 S1 기전에 대한 실측이기도 하다 — **이미 자리잡은 풀에서는
        커넥션을 빼앗을 수 없다.** 실제 실험에서 굶는 쪽도 기존 파드가 아니라 **HPA 가
        새로 띄운 레플리카**다. 기존 파드는 자기 커넥션을 계속 들고 있고, 신규 파드가
        풀을 열지 못한다.

        그래서 주입을 두 단계로 한다 — (1) 기존 백엔드를 끊어 payment 를 "풀을 새로 여는
        쪽"으로 만들고, (2) 즉시 예산을 점유해 다시 열지 못하게 한다. 순서가 바뀌면
        점유 세션 자신이 끊긴다.
        """
        appUser = self.environment.get("DB_APP_USER", "appuser")
        database = self.environment.get("DB_NAME", "cascade")
        superUser = self.environment.get("DB_USER", "cascade")
        script = (
            f"psql -U {superUser} -d {database} -tAc "
            f"\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE usename = '{appUser}' AND pid <> pg_backend_pid()\" >/dev/null 2>&1; "
            f"for i in $(seq 1 {self.sessionCount}); do "
            f"psql -U {appUser} -d {database} "
            f"-c 'SELECT pg_sleep(3600)' >/dev/null 2>&1 & done; wait"
        )
        self.process = subprocess.Popen(
            composeCommand("exec", "-T", "db", "bash", "-c", script),
            env=self.environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
        subprocess.run(
            composeCommand("exec", "-T", "db", "psql",
                           "-U", self.environment.get("DB_USER", "cascade"),
                           "-d", self.environment.get("DB_NAME", "cascade"),
                           "-tAc", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                   "WHERE query LIKE 'SELECT pg_sleep(3600)%'"),
            env=self.environment, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def buildEnvironment(scenario: str, arguments: argparse.Namespace) -> dict[str, str]:
    environment = dict(os.environ)
    # 모든 시나리오가 같은 기동 구성을 쓴다. 조건 차이는 전부 주입 시점 이후에 생긴다.
    environment["DB_MAX_CONNECTIONS"] = str(arguments.maxConnections)
    environment["PAYMENT_FAULT_DELAY_MILLIS"] = "0"
    return environment


def runEpisode(scenario: str, arguments: argparse.Namespace) -> dict:
    """에피소드 하나를 돌리고 노드별 시계열 + 주입 시각을 남긴다."""
    environment = buildEnvironment(scenario, arguments)
    tracePath = RESULTS_DIR / f"trace-{scenario}-{RUN_STAMP}.jsonl"
    RESULTS_DIR.mkdir(exist_ok=True)

    print(f"\n━━━ 시나리오 {scenario} ━━━", flush=True)
    composeDown(environment)
    hog = ConnectionHog(arguments.hogSessions, environment)
    injectedAt = None
    sampleCount = 0
    try:
        composeUp(environment)

        loadResult: dict = {}

        def driveLoad() -> None:
            loadResult.update(loadGen.runLoad(
                targets=ENTRY_TARGETS,
                workerCount=arguments.workers,
                durationSeconds=arguments.normalSeconds + arguments.faultSeconds,
                warmupSeconds=arguments.warmup))

        loadThread = threading.Thread(target=driveLoad, daemon=True)

        with metricScraper.ChainScraper(NODE_ENDPOINTS, tracePath) as scraper:
            loadThread.start()
            # 워밍업 + 정상 구간이 지난 뒤에 주입한다. 정상 구간이 있어야
            # 전파성 판정이 기준선을 잡고 onset 을 집을 수 있다.
            time.sleep(arguments.warmup + arguments.normalSeconds)

            injectedAt = time.time()
            if scenario == "s1":
                print(f"  ⚡ S1 주입 — DB 커넥션 {arguments.hogSessions} 개 점유", flush=True)
                hog.start()
            elif scenario == "s3":
                print(f"  ⚡ S3 주입 — payment 필터 지연 {arguments.faultDelayMillis}ms", flush=True)
                setFault("payment", arguments.faultDelayMillis)
            else:
                print("  (정상 — 주입 없음, 대조 구간)", flush=True)

            loadThread.join(timeout=arguments.faultSeconds + 120)
            sampleCount = scraper.sampleCount

        hog.stop()
    finally:
        composeDown(environment)

    episode = {
        "scenario": scenario,
        "injectedAt": injectedAt,
        "injectionNode": "payment",
        "tracePath": str(tracePath.relative_to(HERE)),
        "normalSeconds": arguments.normalSeconds,
        "faultSeconds": arguments.faultSeconds,
        "entryLoad": loadResult,
        "callGraph": json.loads((HERE / "callGraph.json").read_text(encoding="utf-8")),
    }
    print(f"  입구 goodput {loadResult.get('goodputRps', float('nan')):.1f} rps"
          f" / p95 {loadResult.get('latencyP95Millis', float('nan')):.0f} ms"
          f" / err {loadResult.get('errorRate', float('nan')):.1%}"
          f" / 노드 표본 {sampleCount}", flush=True)
    return episode


def main() -> int:
    parser = argparse.ArgumentParser(description="S1↔S3 증상 동형성 실험")
    parser.add_argument("--scenarios", default="normal,s1,s3")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--warmup", type=float, default=15.0)
    parser.add_argument("--normalSeconds", type=float, default=40.0,
                        help="주입 전 정상 구간. 기준선과 onset 판정에 쓰인다")
    parser.add_argument("--faultSeconds", type=float, default=60.0)
    parser.add_argument("--maxConnections", type=int, default=30)
    parser.add_argument("--hogSessions", type=int, default=24,
                        help="S1 에서 외부가 점유할 DB 세션 수")
    parser.add_argument("--faultDelayMillis", type=int, default=150)
    arguments = parser.parse_args()

    episodes = []
    for scenario in [name.strip() for name in arguments.scenarios.split(",") if name.strip()]:
        episodes.append(runEpisode(scenario, arguments))

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"episodes-{RUN_STAMP}.json"
    path.write_text(json.dumps(episodes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
