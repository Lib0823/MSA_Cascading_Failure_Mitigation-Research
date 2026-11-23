"""J32 진단 — 요청 단위 분산(keep-alive 1)에서 증설하면 왜 입구가 1rps 로 무너지는가.

사용: .venv/bin/python code/chain/diagnoseBlackHole.py 70 30   (예산별로 한 번씩, 셀당 약 3분)
결과: research/challenges.md J32 "블랙홀 파드". 출력 원본은 results/k8s-j32/diagnose-ka1-*.txt

단순 모형(파드마다 1/3, 굶은 파드는 1초 만에 실패 → 재시도)으로는 약 50rps 가 나와야 한다.
실측은 1rps 다. 요청이 1초 만에 실패하지 않고 **10초(HttpClient 요청 타임아웃) 가까이
매달린다**는 뜻이다. 후보:

  H-a  DB 굶주림 — 슬롯 0 인 파드가 원인. 예산이 넉넉하면(mc70) 무너지지 않아야 한다
  H-b  콜드 스타트 폭주 — 막 뜬 JVM(CPU 1코어 제한)이 트래픽 1/3 을 한꺼번에 받는다.
       예산과 무관하게 무너지고, 신규 파드의 CPU 스로틀링이 커야 한다

파드마다 3초 간격: /work 상태코드별 수(2xx·5xx), 평균 지연, Hikari 대기·활성, cgroup
CPU 스로틀링. orders 도 같이 본다(들어온 요청의 상태·지연).
"""
import pathlib
import re
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "calibration"))
import k8sBackend  # noqa: E402
import loadGen  # noqa: E402
import runChain  # noqa: E402

SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)\{([^}]*)\}\s+(\S+)$')
LABEL = re.compile(r'(\w+)="([^"]*)"')


def scrape(pod):
    """한 번의 exec 로 프로메테우스 지표와 cgroup cpu.stat 을 함께 읽는다."""
    command = ("curl -s http://localhost:8080/actuator/prometheus; echo '=== cpu'; "
               "cat /sys/fs/cgroup/cpu.stat")
    out = subprocess.run(["kubectl", "-n", k8sBackend.NAMESPACE, "exec", pod, "--", "sh", "-c", command],
                         capture_output=True, text=True, timeout=15).stdout
    metrics, cpu = out.split("=== cpu", 1) if "=== cpu" in out else (out, "")
    values = {"ok": 0.0, "err": 0.0, "sum": 0.0, "pending": None, "active": None}
    for line in metrics.splitlines():
        match = SAMPLE.match(line.strip())
        if not match:
            continue
        name, labels, value = match.group(1), dict(LABEL.findall(match.group(2))), match.group(3)
        try:
            number = float(value)
        except ValueError:
            continue
        if name == "http_server_requests_seconds_count" and labels.get("uri") == "/work":
            values["ok" if labels.get("status", "").startswith("2") else "err"] += number
        elif name == "http_server_requests_seconds_sum" and labels.get("uri") == "/work":
            values["sum"] += number
        elif name == "hikaricp_connections_pending":
            values["pending"] = number
        elif name == "hikaricp_connections_active":
            values["active"] = number
    for line in cpu.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("throttled_usec", "nr_throttled", "usage_usec"):
            values[parts[0]] = float(parts[1])
    return values


def run(budget):
    runChain.useBackend("k8s")
    arguments = type("A", (), {"maxConnections": budget})()
    environment = runChain.buildEnvironment("normal", arguments)
    environment["PAYMENT_MAX_KEEP_ALIVE_REQUESTS"] = "1"
    k8sBackend.down()
    k8sBackend.up(environment)
    load = {}
    thread = threading.Thread(target=lambda: load.update(loadGen.runLoad(
        targets=runChain.ENTRY_TARGETS, workerCount=32, durationSeconds=130, warmupSeconds=10)),
        daemon=True)
    thread.start()
    time.sleep(40)
    scaledAt = time.time()
    k8sBackend.scale("payment", 3)
    original = set(k8sBackend.podsOf("payment"))
    print(f"\n━━━ ka1 × mc{budget} × normal: payment 1→3 ━━━", flush=True)
    print("  초   | 파드: rps(2xx/5xx) 지연ms 대기/활성 스로틀ms   ※ 원=기존 신=신규, o=orders", flush=True)
    previous = {}
    for _ in range(26):
        time.sleep(3)
        elapsed = time.time() - scaledAt
        pods = k8sBackend.podsOf("payment") + k8sBackend.podsOf("orders")
        current = {}
        for pod in pods:
            try:
                current[pod] = (time.time(), scrape(pod))
            except Exception:  # noqa: BLE001
                pass
        parts = []
        for pod, (stamp, value) in sorted(current.items()):
            tag = "o" if pod.startswith("orders") else ("원" if pod in original else "신")
            if pod not in previous:
                parts.append(f"{tag}{pod[-3:]} 새로 봄")
                continue
            before, last = previous[pod]
            seconds = stamp - before
            ok = (value["ok"] - last["ok"]) / seconds
            err = (value["err"] - last["err"]) / seconds
            count = (value["ok"] + value["err"]) - (last["ok"] + last["err"])
            latency = (value["sum"] - last["sum"]) / count * 1000 if count > 0 else float("nan")
            throttled = (value.get("throttled_usec", 0) - last.get("throttled_usec", 0)) / 1000
            pool = (f"{value['pending']:.0f}/{value['active']:.0f}"
                    if value["pending"] is not None else "-")
            parts.append(f"{tag}{pod[-3:]} {ok:5.1f}/{err:4.1f} {latency:6.0f} {pool:>5} {throttled:5.0f}")
        previous = current
        print(f"  {elapsed:5.1f} | " + " | ".join(parts), flush=True)
    thread.join(timeout=200)
    print(f"  입구 goodput {load.get('goodputRps', float('nan')):.1f} rps / 에러 "
          f"{load.get('errorRate', float('nan')):.2%} / 상태 {load.get('statusCounts')}", flush=True)
    k8sBackend.down()


for budget in [int(b) for b in (sys.argv[1:] or ["70", "30"])]:
    run(budget)
