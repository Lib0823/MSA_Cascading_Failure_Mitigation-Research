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
    # runChain 이 남긴 가장 최근 episodes 파일을 찾아 강도를 기록해 둔다.
    newest = max((HERE / "results").glob("episodes-*.json"), key=lambda p: p.stat().st_mtime)
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
    arguments = parser.parse_args()

    print(f"스윕 {SWEEP_STAMP}")
    print(f"S3 지연 {S3_DELAYS}ms / S1 점유 {S1_HOGS} 세션")
    print(f"총 {len(S3_DELAYS) + len(S1_HOGS) + (0 if arguments.skipNormal else 1)} 에피소드\n")

    done = []
    if not arguments.skipNormal:
        result = runOne("normal", "none", [], arguments)
        if result:
            done.append(result)
    for delay in S3_DELAYS:
        result = runOne("s3", f"{delay}ms", ["--faultDelayMillis", str(delay)], arguments)
        if result:
            done.append(result)
    for hog in S1_HOGS:
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
