"""K8s 백엔드 — Compose 하니스를 쿠버네티스(kubeadm 계열) 위에서 그대로 돌린다.

왜 필요한가 (2026-11-22, [[research/challenges.md]] J30)
========================================================

Compose 에서는 `mₐ` 가 실현되지 않는다 — 병목 용량이 외생적으로 고정되어 조치가
대기 위치를 옮길 뿐이고, 그래서 모든 능동 정책이 무조치보다 나빴다(J22). 증설이
용량을 실제로 바꾸는 환경이 있어야 H4(증설 역효과)와 `mₐ` 를 볼 수 있다.

하니스가 Compose 에 닿는 곳은 다섯 군데뿐이다 — 기동·정지·DB 안 명령 실행·장애
주입(HTTP)·엔드포인트 주소. `runChain.useBackend("k8s")` 가 그 다섯을 이 모듈로 돌리면
`runRemediation`·`runBatch`·`controlLoop` 는 **수정 없이** K8s 에서 돈다.

토폴로지는 [compose-parity.yaml](../../deploy/k8s/parity/compose-parity.yaml) 이다 —
docker-compose.yml 을 한 줄씩 옮긴 것이고, **같은 환경 변수 딕셔너리**로 렌더링한다.

kubectl 만 쓴다. minikube 든 kubeadm 실험 머신이든 kubeconfig 만 바꾸면 같다.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
TEMPLATE = REPO / "deploy" / "k8s" / "parity" / "compose-parity.yaml"
INIT_SQL = REPO / "code" / "calibration" / "initDb.sql"

NAMESPACE = "cascade-parity"
ORDER_LABEL = "parity.cascade/order"
# docker-compose.yml 의 depends_on 순서. 단계마다 Ready 를 기다린다.
START_ORDER = (
    ("1", ("statefulset/db",)),
    ("2", ("deployment/payment",)),
    ("3", ("deployment/orders",)),
    ("4", ("deployment/gateway",)),
    ("5", ()),                       # 외부 접속용 NodePort
)
NODE_PORTS = {"gateway": 31091, "orders": 31092, "payment": 31093}
# 같은 노드에 다른 실험이 떠 있으면 CPU 를 나눠 쓴다 — 측정이 오염된다.
CONFLICTING_NAMESPACES = ("cascade",)

PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def kubectl(*arguments: str, stdin: str | None = None, check: bool = True,
            timeout: float | None = 300) -> subprocess.CompletedProcess:
    completed = subprocess.run(["kubectl", *arguments], input=stdin, capture_output=True,
                               text=True, check=False, timeout=timeout)
    if check and completed.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(arguments[:4])} … 실패 "
                           f"(exit {completed.returncode}): {completed.stderr.strip()[:400]}")
    return completed


def render(environment: dict[str, str]) -> str:
    """`${VAR:-default}` 를 Compose 와 같은 의미로 치환한다.

    `:-` 는 미설정 **또는 빈 값**이면 기본값이다(Compose 규칙). 기본값 없는 `${VAR}` 는
    필수로 본다 — Compose 는 빈 문자열로 넘기며 경고하지만, 여기서는 매니페스트가
    조용히 틀린 값을 갖게 되므로 멈춘다.
    """
    def substitute(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        value = environment.get(name)
        if value not in (None, ""):
            return value
        if default is not None:
            return default
        raise KeyError(f"매니페스트가 요구하는 환경 변수 {name} 가 없다")

    return PLACEHOLDER.sub(substitute, TEMPLATE.read_text(encoding="utf-8"))


def nodeAddress() -> str:
    """노드의 InternalIP. NodePort 는 여기로 들어온다."""
    nodes = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"]
    for address in nodes[0]["status"]["addresses"]:
        if address["type"] == "InternalIP":
            return address["address"]
    raise RuntimeError("노드 InternalIP 를 찾지 못했다")


def endpoints() -> tuple[dict[str, str], list[str]]:
    """`runChain.NODE_ENDPOINTS`·`ENTRY_TARGETS` 에 해당하는 값."""
    host = nodeAddress()
    nodes = {name: f"http://{host}:{port}" for name, port in NODE_PORTS.items()}
    return nodes, [f"{host}:{NODE_PORTS['gateway']}"]


def guardNoConflictingExperiment() -> None:
    """다른 실험 네임스페이스에 돌고 있는 파드가 있으면 거부한다."""
    for namespace in CONFLICTING_NAMESPACES:
        completed = kubectl("-n", namespace, "get", "pods",
                            "--field-selector=status.phase=Running", "-o", "name", check=False)
        running = [line for line in completed.stdout.splitlines() if line.strip()]
        if running:
            raise RuntimeError(
                f"네임스페이스 {namespace} 에 파드 {len(running)}개가 돌고 있다 — 같은 노드의 "
                f"CPU 를 나눠 쓰므로 측정이 오염된다. 내리고 다시 돌려라: "
                f"kubectl delete namespace {namespace}")


def waitForRoute(timeoutSeconds: float = 60.0) -> None:
    """NodePort 경로가 실제로 열릴 때까지 기다린다.

    `kubectl apply` 직후에는 kube-proxy 가 규칙을 아직 깔지 않아 접속이 즉시 거절된다
    (2026-11-22 실측 — 0.001초 만에 실패했다). 롤아웃 완료가 경로 개통을 뜻하지 않는다.
    """
    nodes, _ = endpoints()
    deadline = time.time() + timeoutSeconds
    pending = dict(nodes)
    while pending and time.time() < deadline:
        for name, base in list(pending.items()):
            try:
                with urllib.request.urlopen(base + "/actuator/health/readiness", timeout=2) as r:
                    if r.status == 200:
                        pending.pop(name)
            except Exception:  # noqa: BLE001
                pass
        if pending:
            time.sleep(1.0)
    if pending:
        raise RuntimeError(f"NodePort 경로가 {timeoutSeconds:.0f}초 안에 열리지 않았다: "
                           f"{sorted(pending)}")


def up(environment: dict[str, str]) -> None:
    """Compose 의 `up -d --wait` 에 해당한다. depends_on 순서로 적용하고 단계마다 기다린다."""
    guardNoConflictingExperiment()
    manifest = render(environment)
    kubectl("apply", "-f", "-", "-l", f"{ORDER_LABEL}=0", stdin=manifest)
    # 초기화 SQL 은 Compose 가 마운트하는 **바로 그 파일**에서 만든다 — 원본이 하나다.
    configMap = kubectl("-n", NAMESPACE, "create", "configmap", "db-init",
                        f"--from-file=initDb.sql={INIT_SQL}",
                        "--dry-run=client", "-o", "yaml").stdout
    kubectl("apply", "-f", "-", stdin=configMap)
    for order, workloads in START_ORDER:
        kubectl("apply", "-f", "-", "-l", f"{ORDER_LABEL}={order}", stdin=manifest)
        for workload in workloads:
            kubectl("-n", NAMESPACE, "rollout", "status", workload, "--timeout=240s",
                    timeout=260)
    waitForRoute()


def down(environment: dict[str, str] | None = None) -> None:
    """Compose 의 `down -v` 에 해당한다 — 네임스페이스째 지워 셀마다 빈 DB 로 시작한다."""
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true",
            "--timeout=180s", check=False, timeout=200)


def dbExecCommand(*command: str) -> list[str]:
    """DB 파드 안에서 명령을 돈다. `docker compose exec -T db …` 의 대응이다."""
    # `-i` 를 붙이지 않는다 — psql `-c` 는 stdin 을 읽지 않고, 백그라운드 배치에서
    #  stdin 이 /dev/null 이면 `-i` 가 스트림을 곧바로 닫는다.
    return ["kubectl", "-n", NAMESPACE, "exec", "db-0", "--", *command]


def podsOf(app: str) -> list[str]:
    """Running 인 파드 이름. 증설 중이면 여럿이다."""
    completed = kubectl("-n", NAMESPACE, "get", "pods", "-l", f"app={app}",
                        "--field-selector=status.phase=Running", "-o", "name")
    return [line.split("/", 1)[1] for line in completed.stdout.splitlines() if line.strip()]


def setFault(node: str, delayMillis: int) -> list[str]:
    """S3 주입 — **파드마다** 건다.

    NodePort 로 보내면 파드 하나만 받는다. 레플리카가 여럿일 때 그러면 일부만 느려져
    S3 가 아니라 다른 조건이 된다. 그래서 파드마다 컨테이너 안에서 직접 호출한다
    (이미지에 curl 이 있다 — Compose healthcheck 가 그것을 쓴다).
    """
    applied = []
    for pod in podsOf(node):
        kubectl("-n", NAMESPACE, "exec", pod, "--", "curl", "-fsS", "-X", "POST",
                f"http://localhost:8080/fault?delayMillis={delayMillis}")
        applied.append(pod)
    if not applied:
        raise RuntimeError(f"{node} 에 Running 파드가 없다 — 장애를 걸 수 없다")
    return applied


def scale(deployment: str, replicas: int) -> None:
    kubectl("-n", NAMESPACE, "scale", f"deployment/{deployment}", f"--replicas={replicas}")


def replicaCounts(deployment: str) -> dict:
    status = json.loads(kubectl("-n", NAMESPACE, "get", f"deployment/{deployment}",
                                "-o", "json").stdout)
    return {"spec": status["spec"].get("replicas", 0),
            "ready": status.get("status", {}).get("readyReplicas", 0) or 0}


class ReplicaWatcher:
    """증설 이후 Ready 레플리카 수를 1초마다 기록한다 — `ℓₐ`(Scale-out 리드타임)의 재료다.

    proposal §355 가 *"Scale-out 의 `ℓₐ` 가 가장 클 것"* 이라 예상한 값이다 — 파드
    스케줄링 + JVM 기동 + 워밍업. 클러스터가 있어야 잰다고 이월돼 있었다.
    """

    def __init__(self, deployment: str, intervalSeconds: float = 1.0) -> None:
        self.deployment = deployment
        self.intervalSeconds = intervalSeconds
        self.timeline: list[dict] = []
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stopFlag.is_set():
            try:
                counts = replicaCounts(self.deployment)
                self.timeline.append({"timestamp": time.time(), **counts})
            except Exception:  # noqa: BLE001
                pass
            self.stopFlag.wait(self.intervalSeconds)

    def start(self) -> "ReplicaWatcher":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=10)

    def leadTimeSeconds(self, since: float, target: int) -> float | None:
        """`since` 부터 Ready 가 `target` 에 처음 닿기까지. 닿지 못했으면 None."""
        for row in self.timeline:
            if row["timestamp"] >= since and row["ready"] >= target:
                return round(row["timestamp"] - since, 1)
        return None


WORK_COUNT = re.compile(r'http_server_requests_seconds_count\{[^}]*uri="/work"[^}]*\}\s+([0-9.eE+]+)')


def workRequestCount(pod: str) -> float | None:
    """파드가 지금까지 처리한 `/work` 요청 수(상태코드 무관). 파드 안에서 직접 긁는다."""
    completed = kubectl("-n", NAMESPACE, "exec", pod, "--", "curl", "-s",
                        "http://localhost:8080/actuator/prometheus", check=False, timeout=10)
    if completed.returncode != 0:
        return None
    return sum(float(m) for m in WORK_COUNT.findall(completed.stdout))


class PodTrafficSampler:
    """노드의 **파드별** 처리 속도를 5초마다 기록한다 — 증설의 기전 증거다.

    🔴 2026-11-22 진단에서 발견했다(J30). 장애가 없는데 payment 를 1→3 으로 늘리자
    총 처리량이 322 → 약 180~260rps 로 **떨어졌다.** 신규 파드는 DB 예산의 남은
    7슬롯을 나눠 쥐고 67·51rps 에 고정됐고, kube-proxy 가 연결을 고르게 나누자 느린
    파드에 워커가 쌓여 커넥션 20개를 가진 기존 파드가 **0rps 까지** 굶었다.

    NodePort 로 긁는 노드별 트레이스는 레플리카가 여럿이면 파드 하나만 보므로 이 현상을
    볼 수 없다. 그래서 파드 안에서 직접 센다. **증설하지 않는 셀에서도 같은 주기로 돈다** —
    한쪽 셀에만 표집 부하가 얹히지 않게 하기 위해서다. 다만 exec 횟수는 파드 수에
    비례한다(레플리카 1 이면 5초에 1회, 3 이면 3회). 1회가 수십 ms 라 부하는 작지만
    완전한 대칭은 아니다.
    """

    def __init__(self, app: str, intervalSeconds: float = 5.0) -> None:
        self.app = app
        self.intervalSeconds = intervalSeconds
        self.samples: list[dict] = []
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stopFlag.wait(self.intervalSeconds):
            try:
                counts = {pod: workRequestCount(pod) for pod in podsOf(self.app)}
                self.samples.append({"timestamp": time.time(), "counts": counts})
            except Exception:  # noqa: BLE001
                pass

    def start(self) -> "PodTrafficSampler":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=20)

    def rates(self) -> list[dict]:
        """연속 표본 사이의 파드별 처리 속도(rps). 새로 생긴 파드는 다음 표본부터 잡힌다."""
        out = []
        for previous, current in zip(self.samples, self.samples[1:]):
            elapsed = current["timestamp"] - previous["timestamp"]
            perPod = {}
            for pod, count in current["counts"].items():
                before = previous["counts"].get(pod)
                if count is not None and before is not None and elapsed > 0:
                    perPod[pod] = round((count - before) / elapsed, 2)
            out.append({"timestamp": current["timestamp"], "rps": perPod})
        return out


def podAddresses() -> dict[str, str]:
    """파드 IP → 파드 이름. DB 가 보는 것은 클라이언트 IP 뿐이다."""
    completed = kubectl("-n", NAMESPACE, "get", "pods", "-o", "json", check=False, timeout=10)
    if completed.returncode != 0:
        return {}
    addresses = {}
    for pod in json.loads(completed.stdout)["items"]:
        address = pod.get("status", {}).get("podIP")
        if address:
            addresses[address] = pod["metadata"]["name"]
    return addresses


CONNECTIONS_SQL = ("SELECT coalesce(host(client_addr), 'local'), count(*) "
                   "FROM pg_stat_activity WHERE usename = '{user}' GROUP BY 1")


class DbConnectionSampler:
    """앱 사용자의 DB 커넥션 수를 **파드별로** 5초마다 기록한다 — 예산 분할의 직접 증거다(J31).

    `podTraffic` 으로도 신규 파드의 커넥션 수를 역산할 수는 있다(포화된 파드의 처리량 =
    커넥션 수 × 커넥션당 처리율). 그러나 그것은 포화를 전제한 추정이다. DB 쪽
    `pg_stat_activity` 를 클라이언트 IP 로 묶으면 추정 없이 센다. S1 의 점유 세션은 db-0
    안에서 소켓으로 붙으므로 `local` 로 잡힌다.
    """

    def __init__(self, environment: dict[str, str], intervalSeconds: float = 5.0) -> None:
        self.appUser = environment.get("DB_APP_USER", "appuser")
        self.superUser = environment.get("DB_USER", "cascade")
        self.database = environment.get("DB_NAME", "cascade")
        self.intervalSeconds = intervalSeconds
        self.samples: list[dict] = []
        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sampleOnce(self) -> dict | None:
        completed = kubectl("-n", NAMESPACE, "exec", "db-0", "--", "psql", "-U", self.superUser,
                            "-d", self.database, "-tAc", CONNECTIONS_SQL.format(user=self.appUser),
                            check=False, timeout=10)
        if completed.returncode != 0:
            return None
        names = podAddresses()
        connections = {}
        for line in completed.stdout.splitlines():
            if "|" not in line:
                continue
            address, count = line.rsplit("|", 1)
            connections[names.get(address, address)] = int(count)
        return {"timestamp": time.time(), "connections": connections}

    def loop(self) -> None:
        while not self.stopFlag.wait(self.intervalSeconds):
            try:
                sample = self.sampleOnce()
                if sample is not None:
                    self.samples.append(sample)
            except Exception:  # noqa: BLE001
                pass

    def start(self) -> "DbConnectionSampler":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=20)


def clusterFingerprint() -> dict:
    """셀을 만든 클러스터 — 셀과 함께 원장에 남긴다. 다른 클러스터의 셀과 섞지 않기 위해서다."""
    nodes = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"]
    node = nodes[0]
    info = node["status"]["nodeInfo"]
    images = {}
    completed = kubectl("-n", NAMESPACE, "get", "pods", "-o", "json", check=False)
    if completed.returncode == 0:
        for pod in json.loads(completed.stdout)["items"]:
            for container in pod.get("status", {}).get("containerStatuses", []):
                images[container["image"]] = container.get("imageID", "")
    return {
        "kubernetesVersion": info.get("kubeletVersion"),
        "containerRuntime": info.get("containerRuntimeVersion"),
        "kernel": info.get("kernelVersion"),
        "osImage": info.get("osImage"),
        "nodeCount": len(nodes),
        "allocatableCpu": node["status"]["allocatable"].get("cpu"),
        "allocatableMemory": node["status"]["allocatable"].get("memory"),
        "images": images,
    }
