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
print(json.dumps(loop.summary(), ensure_ascii=False, indent=2))
loop.writeDecisions(HERE / f"decisions-{policy}.jsonl")
