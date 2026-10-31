"""장애 강도를 여러 단계로 두고 신뢰도 중간 구간이 생기는지 본다.

**지금 열려 있는 유일한 위험을 직접 공략한다.** end-to-end 에서 `p̄` 가 중간
구간(0.1~0.9)에 있는 표본이 11.0% 였고, 원인 진단은 *"장애가 켜짐/꺼짐 두 상태
뿐이라 중간 강도가 없다"* 였다. 그 진단이 맞다면 **강도를 여러 단계로 두면
중간 구간이 늘어야 한다.**

검증 가능한 예측이며, 결과가 어느 쪽이든 답이 된다.

- **늘어난다** → 원인 진단이 맞다. H2 의 전제가 살아 있고 게이트 ③ 에서 더 는다
- **안 는다** → 진단이 틀렸다. 강도가 아니라 다른 원인이며 **축 이동을 앞당긴다**

강도 축
=======

===  ==========================  ==========================================
S3   ``FAULT_DELAY_MILLIS``      payment 의 필터 지연. 100~800ms
S1   점유 세션 수                 DB 커넥션 예산을 얼마나 뺏는가. 10~22
===  ==========================  ==========================================

둘 다 **Compose 손잡이**다. K8s 도 GPU 도 필요 없다 — 게이트 ③ 까지 미룰 이유가
없었고, 그렇게 분류한 것이 잘못이었다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SWEEP_STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

# 강도 단계. 기존 실행(S3 400ms, S1 hog 20)을 포함하도록 잡아 비교 가능하게 한다.
S3_DELAYS = (100, 200, 300, 500, 800)
S1_HOGS = (10, 14, 18, 22)


def runOne(scenario: str, label: str, extraArgs: list[str], arguments) -> dict | None:
    command = [sys.executable, str(HERE / "runChain.py"),
               "--scenarios", scenario,
               "--workers", str(arguments.workers),
               "--warmup", str(arguments.warmup),
               "--normalSeconds", str(arguments.normalSeconds),
               "--faultSeconds", str(arguments.faultSeconds),
               *extraArgs]
    print(f"\n━━━ {scenario} 강도 {label} ━━━", flush=True)
    completed = subprocess.run(command, cwd=HERE, capture_output=True, text=True, check=False)
    for line in completed.stdout.splitlines():
        if any(k in line for k in ("goodput", "⚡", "Traceback")):
            print("  " + line.strip(), flush=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout[-2000:] + completed.stderr[-2000:])
        print(f"  ❌ 실패 (exit {completed.returncode})", flush=True)
        return None
    # 강도 기록 대상은 **runChain 이 직접 출력한 경로**에서 받는다.
    # 종전에는 mtime 이 가장 큰 episodes 파일을 골랐는데, 그러면 다른 실행이
    # 끼거나 오래된 파일이 갱신된 경우 **엉뚱한 에피소드에 강도를 덮어쓴다** —
    # 강도 기록이 조용히 틀리면 스윕 결과 해석이 전부 어긋난다.
    written = [l.split("기록:", 1)[1].strip()
               for l in completed.stdout.splitlines() if "기록:" in l]
    if not written:
        print("  ❌ runChain 이 기록 경로를 출력하지 않았다 — 강도를 남기지 못한다", flush=True)
        return None
    newest = pathlib.Path(written[-1])
    if not newest.is_absolute():
        newest = HERE / newest
    records = json.loads(newest.read_text(encoding="utf-8"))
    for record in records:
        record["intensityLabel"] = label
        record["intensityAxis"] = scenario
    newest.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scenario": scenario, "intensity": label, "episodesFile": newest.name}


def main() -> int:
    parser = argparse.ArgumentParser(description="장애 강도 다단계 스윕")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--warmup", type=float, default=60.0)
    parser.add_argument("--normalSeconds", type=float, default=50.0)
    parser.add_argument("--faultSeconds", type=float, default=70.0)
    parser.add_argument("--skipNormal", action="store_true")
    # 어느 강도가 라벨을 만드는지는 **부하 수준에 달렸다** (2026-11-01 발견).
    # 64워커에서는 정상 구간 기준선이 357.9ms 로 올라가 2배 임계가 716ms 가 되고,
    # 100~500ms 주입이 전부 임계 미달로 **장애 라벨 0개**가 됐다. 32워커의
    # 기준선은 134.2ms 다. 그래서 강도 목록을 고정 상수로 두지 않는다.
    parser.add_argument("--s3Delays", default=",".join(str(d) for d in S3_DELAYS),
                        help="S3 하류 지연(ms) 목록. 쉼표 구분")
    parser.add_argument("--s1Hogs", default=",".join(str(h) for h in S1_HOGS),
                        help="S1 외부 점유 세션 수 목록. 쉼표 구분")
    parser.add_argument("--scenarios", default="s3,s1",
                        help="돌릴 축. s3 만 또는 s1 만 돌릴 때 쓴다")
    arguments = parser.parse_args()

    axes = [a.strip() for a in arguments.scenarios.split(",") if a.strip()]
    unknown = [a for a in axes if a not in ("s1", "s3")]
    if unknown:
        print(f"❌ 모르는 축: {unknown}. s1·s3 만 된다")
        return 1
    s3Delays = tuple(int(d) for d in arguments.s3Delays.split(",") if d.strip()) \
        if "s3" in axes else ()
    s1Hogs = tuple(int(h) for h in arguments.s1Hogs.split(",") if h.strip()) \
        if "s1" in axes else ()

    print(f"스윕 {SWEEP_STAMP}")
    print(f"부하 {arguments.workers} 워커")
    print(f"S3 지연 {s3Delays}ms / S1 점유 {s1Hogs} 세션")
    print(f"총 {len(s3Delays) + len(s1Hogs) + (0 if arguments.skipNormal else 1)} 에피소드\n")

    done = []
    if not arguments.skipNormal:
        result = runOne("normal", "none", [], arguments)
        if result:
            done.append(result)
    for delay in s3Delays:
        result = runOne("s3", f"{delay}ms", ["--faultDelayMillis", str(delay)], arguments)
        if result:
            done.append(result)
    for hog in s1Hogs:
        result = runOne("s1", f"hog{hog}", ["--hogSessions", str(hog)], arguments)
        if result:
            done.append(result)

    path = HERE / "results" / f"sweep-{SWEEP_STAMP}.json"
    path.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(done)} 에피소드 완료. 기록: {path}")
    print("다음: .venv/bin/python code/model/endToEnd.py --calibration sigmoid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
