"""제어 루프 스모크 — 돌기는 하는가.

확인하는 것은 성능이 아니라 **연결**이다. 학습과 추론의 feature 공간이
어긋나면 여기서 assert 로 멈춘다.
"""
from __future__ import annotations
import json, pathlib, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "calibration"))
import loadGen  # noqa: E402
import controlLoop  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
NODES = {"gateway": "http://127.0.0.1:18091", "orders": "http://127.0.0.1:18092",
         "payment": "http://127.0.0.1:18093"}
GRAPH = json.loads((HERE.parent / "chain" / "callGraph.json").read_text(encoding="utf-8"))

policy = sys.argv[1] if len(sys.argv) > 1 else "A5"
loop = controlLoop.ControlLoop(
    artifactPath=HERE / "predictor.joblib", nodes=NODES, callGraph=GRAPH,
    policy=policy, periodSeconds=2.0, warmupTicks=8, predictionLeadSeconds=30.0)

result: dict = {}
def drive() -> None:
    result.update(loadGen.runLoad(targets=["127.0.0.1:18091"], workerCount=32,
                                 durationSeconds=40.0, warmupSeconds=10.0))
thread = threading.Thread(target=drive, daemon=True)
with loop:
    thread.start()
    thread.join(timeout=120)
summary = loop.summary()
print(json.dumps(summary, ensure_ascii=False, indent=2))
loop.writeDecisions(HERE / f"decisions-{policy}.jsonl")

# 🔒 **공허한 통과를 막는다.** 서비스가 떠 있지 않으면 루프가 긁을 것이 없어
# tickCount 0 으로 조용히 끝난다 — *"연결을 확인한다"* 면서 연결이 없을 때
# 성공을 보고하면 없는 것보다 나쁘다. 2026-11-01 에 실제로 그렇게 통과했다.
if summary.get("tickCount", 0) == 0:
    raise SystemExit(
        "🔴 틱이 한 번도 돌지 않았다. Compose 스택이 떠 있는지 확인하라 — "
        "docker compose -f code/chain/docker-compose.yml up -d / "
        + "기대 포트: " + ", ".join(NODES.values()))
if policy not in controlLoop.PASSIVE_POLICIES and summary.get("decisions", 0) == 0:
    raise SystemExit(f"🔴 {policy} 는 능동 정책인데 결정이 0건이다 — 루프가 돌았으나 "
                     "예측·판정 경로가 비어 있다")
print(f"✅ 틱 {summary['tickCount']}회 / 결정 {summary['decisions']}건 — 연결 확인")
