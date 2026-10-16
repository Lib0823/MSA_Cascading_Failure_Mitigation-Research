# 게이트 ② 실험 환경 — K8s 매니페스트

§4-1 토폴로지를 그대로 옮긴 것이다. **클러스터 없이 미리 작성했고 스키마 검증까지
마쳤다** — 게이트 ②의 60h 중 설정 작성 부분을 비싼 창 밖으로 빼기 위해서다.

```
gateway → order → inventory → payment → [PostgreSQL + HikariCP]
                ↘ notification
                ↘ assistant   (선택 호출, 레플리카 1 고정, HPA 제외)
```

전부 **같은 이미지**이며 환경변수 네 개로만 구분된다 — `SERVICE_NAME` / `WORKLOAD` /
`DOWNSTREAM` / `OPTIONAL_DOWNSTREAM`.

---

## 왜 미리 쓰는가

**60h의 실체는 설정 작성이 아니라 디버깅이다** — `kubeadm init` 실패, CNI 선택,
cgroup driver 정합, metrics-server 연결, registry 접근. 전부 환경 특수적이라 미리 못 한다.

그런데 거기에 **설정 오류까지 섞이면 원인 분리가 안 된다.** *"파드가 안 뜨는데
매니페스트 문제인가 CNI 문제인가"* 를 매번 가려야 하고, 주 4시간을 2시간씩 쪼개 쓰는
구조에서 그 비용이 세션의 절반을 먹는다.

**여기서 확정되는 것은 구조다** — 서비스 그래프, 라벨 체계, 수집 대상, HPA 대상 선정.
그건 실측으로 안 바뀐다.

---

## ⚠️ 실측 전까지 플레이스홀더인 값

| 값 | 왜 미정인가 |
|---|---|
| `resources.requests/limits` | CPU 헤드룸 실측이 선행돼야 한다 (게이트 ② 확인 항목) |
| HPA `averageUtilization: 60` | 너무 낮으면 정상 부하에서도 스케일아웃되어 S1 기전이 통제를 벗어난다 |
| `DB_SLEEP_MILLIS`, `DB_BURN_ROWS` | 게이트 ①.5 에서 Compose 기준으로 잡은 값이다. 코어 수가 다르면 다시 잡는다 |

**구조는 확정, 수치는 미정**이다. 실측 후 kustomize overlay 로 덮는다.

---

## 설계가 매니페스트에 박혀 있는 지점 넷

**① `assistant` 에 HPA 가 없다.** 빠뜨린 게 아니라 **설계 결정**이다. 레플리카 1 고정이
S2 의 `m_scale-out = 0` 을 만든다(§4-6). `scaling: fixed-replica` 라벨로 의도를 명시했다.

**② `payment` 의 HPA `maxReplicas: 4`.** 풀 20 × 4 = 80 이 `max_connections = 50` 을
넘어야 고갈이 일어난다. 게이트 ①.5 가 실측한 규칙 —
**`2 × poolSize < max_connections < 4 × poolSize`**. 25 에서는 2 레플리카만으로 고갈되어
*"인스턴스↑ → 커넥션 요구↑ → 상한 도달"* 이라는 서술된 기전이 나타나지 않았다.

**③ 앱 풀(`HIKARI_MAX_POOL_SIZE`)이 넉넉하다.** 조이면 레플리카 2→4 에서 총 풀 용량이
함께 늘어 대기가 **줄고** `m_scale-out < 0` 이 뒤집힌다(§4-5).

**④ `notification` 은 하류가 없는 잎이다.** `order` 의 분기를 만들어 이웃 집계의 1-hop 이
둘 이상이 되게 하는 것이 역할이고, 동시에 **대조 노드**다 — 여기가 positive 로
라벨링되면 전파성 규칙이 샜다는 뜻이다.

---

## 적용 순서

```bash
# 전제: metrics-server 가 설치돼 있어야 한다. 없으면 HPA 가 <unknown> 으로 멈춘다.
#       kubeadm 에는 기본 포함되지 않는다.
kubectl apply -f 00-namespace.yaml
kubectl apply -f 10-postgres.yaml
kubectl -n cascade rollout status statefulset/postgres

# 이미지를 노드가 볼 수 있어야 한다. 단일 노드면 로컬 빌드 + imagePullPolicy:
# IfNotPresent 로 충분하고, 아니면 레지스트리가 필요하다.
kubectl apply -f 20-services.yaml
kubectl -n cascade rollout status deployment/payment

kubectl apply -f 30-hpa.yaml
kubectl apply -f 40-prometheus.yaml
```

## 검증

클러스터 없이 오프라인 스키마 검증이 된다.

```bash
docker run --rm -v "$(pwd):/work" -w /work ghcr.io/yannh/kubeconform:latest \
  -strict -summary -kubernetes-version 1.31.0 *.yaml
# → 27 resources, Valid: 27
```

> `kubectl --dry-run=client` 는 스키마를 받으려 클러스터에 접속하므로 여기서는 못 쓴다.

---

## 아직 안 된 것

- **클러스터에 실제로 적용해 본 적이 없다.** 스키마는 맞으나 동작은 미확인이다
- **이미지 배포 경로 미정** — 단일 노드 로컬 빌드로 갈지 레지스트리를 세울지
- **부하 생성기** — [code/calibration/loadGen.py](../../code/calibration/loadGen.py) 를 K8s 진입점
  하나를 향하도록 바꾸면 된다. Compose 처럼 포트를 나열할 필요가 없다(Service 가 분배)
- **`ℓₐ` 계측** — 파드 스케줄링·이미지 풀·JVM 기동. 클러스터가 있어야 잰다
