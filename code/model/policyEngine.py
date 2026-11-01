"""Policy Engine — 신뢰도 기반 조치 선택 (§2-B).

**연구의 유일한 증명 대상(H2)이자 컷 불가 항목**인데 지금까지 코드가 없었다.
여기서 만드는 것은 설계를 그대로 옮긴 골격이며, `mₐ`·`ℓₐ` 는 실측 전이므로
가정값으로 둔다. 실측이 들어오면 **표만 교체**하면 된다.

설계의 핵심은 신뢰도 구간을 손으로 긋지 않는다는 것이다
=========================================================

*"고신뢰도=적극적 조치 / 중간=저비용·가역적 / 저=보류"* 는 **정의가 아니라 유도의
결과**다. 비용함수에서 조치별 임계값 `θₐ` 가 나오고 구간은 거기서 자동 생성된다.

.. code-block:: text

    E[비용 | 조치 a] = p·[ (1−mₐ)·L + Dₐ ] + (1−p)·[ Dₐ·Rₐ ]
    E[비용 | 보류   ] = p·L

    조치 a 가 보류를 이기는 조건을 p 에 대해 정리하면

    p > θₐ ,  θₐ = Dₐ·Rₐ / ( mₐ·L − Dₐ·(1−Rₐ) )

분모가 0 이하이면 **어떤 p 로도 보류를 이기지 못한다.** 이 경우 `θₐ = ∞` 이며,
H4 의 두 사례가 정확히 여기에 해당한다 — S1 의 Scale-out 은 `mₐ < 0`(역효과),
S2 의 Scale-out 은 `mₐ = 0`(불가능)이다. **외부 하드코딩 규칙 없이 비용함수가
스스로 배제한다.**

세 가지가 비용함수 하나에서 함께 나온다
=======================================

1. **Safety Guard** — 불확실성이 크면 `p_eff = max(0, p̄ − κ√u)` 가 줄어 모든 `θₐ`
   아래로 내려가고, 자동으로 보류된다. 저위험 노드가 보류되는 것과 **같은 기전**이다.
2. **리드타임 배제** — `ℓₐ ≥ L_pred` 인 조치는 후보에서 먼저 빠진다. 늦게 듣는
   조치는 선제 집행이 성립하지 않는다(H6).
3. **병목 유형 의존** — `mₐ` 가 유형별로 다르므로 같은 신뢰도에서도 선택이 달라진다.
   *"신뢰도만으로는 조치를 고를 수 없다"* 는 §2-0 다중 클래스 설계의 근거다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

HOLD = "보류"
INFINITE_THRESHOLD = float("inf")

# 조치별 적용 지점 (§2-C). 조치 종류와 노드 타입의 함수이며 탐색 대상이 아니다.
#   caller = v 의 호출자,  self = v 자신
APPLY_POINT = {
    ("CIRCUIT_BREAKER", "service"): "caller",
    ("CIRCUIT_BREAKER", "llm"): "caller",
    ("BULKHEAD", "service"): "caller",
    ("BULKHEAD", "llm"): "caller",
    ("BROWNOUT", "service"): "caller",
    ("BROWNOUT", "llm"): "self",      # max_tokens 는 서빙 노드 자신의 요청 파라미터다
    ("SCALE_OUT", "service"): "self",
    ("SCALE_OUT", "llm"): None,       # 레플리카 1 고정 — 적용 불가
}


@dataclass(frozen=True)
class ActionProfile:
    """조치 × 노드 타입 구현체의 비용 파라미터.

    `a` 는 추상적인 조치가 아니라 **노드 타입별 구현체**를 가리킨다. 같은 조치라도
    노드 타입이 다르면 `Dₐ`·`Rₐ`·`mₐ` 가 달라 `θₐ` 도 달라진다(§2-B).
    """

    name: str
    nodeType: str
    disruption: float          # Dₐ — 즉시 비용 (정상 트래픽 손실·자원 소모)
    reversibility: float       # Rₐ — 되돌리기 난이도 (0~1, 클수록 어렵다)
    leadTimeSeconds: float     # ℓₐ — 조치 발동부터 효과 발현까지
    # mₐ 는 **병목 유형에 의존한다.** 이 의존성이 다중 클래스 설계의 근거다.
    mitigationByBottleneck: dict[str, float] = field(default_factory=dict)

    def mitigation(self, bottleneck: str) -> float:
        return self.mitigationByBottleneck.get(bottleneck, 0.0)


def effectiveRisk(meanRisk: float, uncertainty: float, kappa: float) -> float:
    """`p_eff = max(0, p̄ − κ·√u)`.

    κ=0 이면 순수 기대비용 최소화로 환원된다. 이 성질이 Safety Guard 가 **외부 규칙이
    아니라 비용함수의 일부**임을 보인다.
    """
    return max(0.0, meanRisk - kappa * math.sqrt(max(0.0, uncertainty)))


def expectedCostOfAction(risk: float, profile: ActionProfile, bottleneck: str,
                         availabilityLoss: float) -> float:
    mitigation = profile.mitigation(bottleneck)
    return (risk * ((1.0 - mitigation) * availabilityLoss + profile.disruption)
            + (1.0 - risk) * (profile.disruption * profile.reversibility))


def expectedCostOfHold(risk: float, availabilityLoss: float) -> float:
    return risk * availabilityLoss


def confidenceThreshold(profile: ActionProfile, bottleneck: str,
                        availabilityLoss: float) -> float:
    """`θₐ = Dₐ·Rₐ / (mₐ·L − Dₐ·(1−Rₐ))`.

    분모가 0 이하이면 어떤 신뢰도로도 보류를 이기지 못하므로 ∞ 를 돌려준다.
    **H4 의 두 사례가 여기서 자동 배제된다** — S1 의 Scale-out(`mₐ<0`)과
    S2 의 Scale-out(`mₐ=0`)이다.
    """
    mitigation = profile.mitigation(bottleneck)
    denominator = mitigation * availabilityLoss - profile.disruption * (1.0 - profile.reversibility)
    if denominator <= 0:
        return INFINITE_THRESHOLD
    numerator = profile.disruption * profile.reversibility
    if numerator <= 0:
        # 되돌리기가 공짜면 오탐 비용이 없어 임계가 0 이다 — 위험이 조금만 있어도 건다.
        return 0.0
    return numerator / denominator


@dataclass
class Decision:
    node: str
    action: str
    applyPoint: str | None
    effectiveRisk: float
    expectedCost: float
    threshold: float
    reason: str
    candidates: list[dict]


class PolicyEngine:
    """`A ∪ {보류}` 위에서 `ℓₐ < L_pred` 제약 하에 기대비용을 최소화한다."""

    def __init__(self, profiles: list[ActionProfile], availabilityLoss: float = 1.0,
                 kappa: float = 1.0) -> None:
        self.profiles = profiles
        self.availabilityLoss = availabilityLoss
        self.kappa = kappa

    def decide(self, node: str, nodeType: str, bottleneck: str, meanRisk: float,
               uncertainty: float, predictionLeadSeconds: float) -> Decision:
        risk = effectiveRisk(meanRisk, uncertainty, self.kappa)
        holdCost = expectedCostOfHold(risk, self.availabilityLoss)

        candidates = []
        best = (HOLD, None, holdCost, INFINITE_THRESHOLD)
        for profile in self.profiles:
            if profile.nodeType != nodeType:
                continue
            applyPoint = APPLY_POINT.get((profile.name, nodeType))
            threshold = confidenceThreshold(profile, bottleneck, self.availabilityLoss)

            # ① 리드타임 제약이 먼저다. 늦게 듣는 조치는 선제 집행이 성립하지 않는다.
            if profile.leadTimeSeconds >= predictionLeadSeconds:
                candidates.append({"action": profile.name, "excluded": "리드타임",
                                   "threshold": threshold, "expectedCost": None})
                continue
            # ② 적용 지점이 없는 조치는 물리적으로 불가능하다 (LLM 의 Scale-out).
            if applyPoint is None:
                candidates.append({"action": profile.name, "excluded": "적용 불가",
                                   "threshold": threshold, "expectedCost": None})
                continue

            cost = expectedCostOfAction(risk, profile, bottleneck, self.availabilityLoss)
            candidates.append({"action": profile.name, "excluded": None,
                               "threshold": threshold, "expectedCost": cost,
                               "beatsHold": cost < holdCost})
            if cost < best[2]:
                best = (profile.name, applyPoint, cost, threshold)

        action, applyPoint, cost, threshold = best
        if action == HOLD:
            blocked = [c["action"] for c in candidates if c["excluded"]]
            reason = ("리드타임·적용 불가로 전부 배제" if blocked and len(blocked) == len(candidates)
                      else f"p_eff {risk:.3f} 가 모든 θₐ 에 미달")
        else:
            reason = f"p_eff {risk:.3f} > θ {threshold:.3f}"
        return Decision(node=node, action=action, applyPoint=applyPoint, effectiveRisk=risk,
                        expectedCost=cost, threshold=threshold, reason=reason,
                        candidates=candidates)


def defaultProfiles() -> list[ActionProfile]:
    """⚠️ **가정값이다.** `mₐ`·`ℓₐ`·**`Dₐ`** 는 실측되며(§4-4·§4-5) 그때 교체한다.

    부호와 상대적 크기만 §2-B 표를 따른다 — Scale-out 은 S1 에서 음수, S2 에서 0,
    Disruption 과 리드타임은 Scale-out 이 가장 크다.

    🔴 **`Dₐ` 가 교체 대상 목록에서 빠져 있었다** (2026-11-01 정정). `Dₐ` 가 H5a 와
    같은 양이라는 발견(§4-5)보다 이 주석이 먼저 쓰였기 때문이다. 그래서 `Dₐ` 가
    2026-10-16 에 실측됐는데도 배선되지 않은 채 남아 **문서가 말하는 것과 코드가
    하는 것이 달랐다**([[research/challenges.md]] J18).

    **실측값 (2026-11-01, `disruptionByAction.py`)**

    ==================  ======  ========  ===================================
    조치                 가정    실측      비고
    ==================  ======  ========  ===================================
    BULKHEAD            0.25    **0.716**  ✅ 쓸 수 있다. 0.703 의 독립 재현
    CIRCUIT_BREAKER     0.15    0.002      ⚠️ 조건부 — 회로가 열릴 때 발생한다
    BROWNOUT            0.30    0.001      🔴 무효 — 처리량이 비용의 반대 방향
    SCALE_OUT           0.50    —          게이트 ② 대기 (K8s 필요)
    ==================  ======  ========  ===================================

    🔴 **2026-11-02 — 두 값을 배선했다. 판단이 바뀐 근거는 측정이다.**

    J19 는 *"Scale-out 이 빠진 상태로 일부만 바꾸면 게이트 ② 에서 또 전부
    재측정해야 한다"* 며 유보했다. **그 전제가 Compose 범위에서는 성립하지 않는다.**

    S1 20셀의 결정 로그 **690건에서 선택된 조치는 두 개뿐**이었다.

        BULKHEAD 380 · CIRCUIT_BREAKER 429 · 보류 226
        BROWNOUT 0 · SCALE_OUT 0

    **Brownout 과 Scale-out 은 한 번도 선택되지 않는다** — Brownout 은 Compose
    토폴로지에 선택 호출이 없어 무동작이고, Scale-out 은 K8s 가 필요하다. 따라서
    **이 둘을 가정값으로 남겨도 Compose 결과는 달라지지 않는다.** 게이트 ② 의
    재측정은 Scale-out 이 **새 조치(A3)로 들어오기 때문에** 어차피 필요하다.

    **배선 기준은 "측정이 파라미터의 정의와 맞는가" 다.**

    ==================  ==========  ====================================
    조치                 배선         근거
    ==================  ==========  ====================================
    BULKHEAD            **0.716**   ✅ 동시성을 **무조건** 제한하므로 비용이
                                    즉시·무조건 발생한다. `Dₐ` 의 정의와 일치.
                                    0.703 의 독립 재현이라 신뢰할 수 있다
    CIRCUIT_BREAKER     **0.002**   ✅ `Dₐ` 가 **무조건적** 비용으로 정의돼
                                    있고, CB 의 무조건적 비용은 실제로 0 에
                                    가깝다. 측정이 정의와 일치한다 — §2-B 가
                                    *"장전은 싸다"* 를 결과로 받기로 한 대로다
    BROWNOUT            0.30 유지    🔴 측정이 **무동작**이었다. 0.001 은
                                    "무료" 가 아니라 "아무것도 하지 않음" 이다
    SCALE_OUT           0.50 유지    게이트 ② 대기 (K8s 필요)
    ==================  ==========  ====================================

    **유도되는 `θ` (R=0.1, L=1)**

        S1 판정 → BULKHEAD          θ = 0.716·0.1 / (0.8 − 0.716·0.9) = 0.460
        S3 판정 → CIRCUIT_BREAKER   θ = 0.002·0.1 / (0.8 − 0.002·0.9) = 0.00025
        S3 의 BULKHEAD · S1 의 CB    분모 ≤ 0 → θ = ∞ (자동 배제, 종전과 같다)

    종전 가정값에서는 θ 가 **0.0435 · 0.0226** 이었고 `p_eff` 의 p50(0.063) 보다
    작아 **게이트가 상시 열려 있었다**([[research/challenges.md]] J22). θ_BH 가
    0.460 이 되면 게이팅이 물릴 자리가 생긴다.

    ⚠️ **`llm` 종류 프로파일은 건드리지 않았다.** Compose 콜그래프에 `llm` 노드가
    없어 쓰이지 않으며, 실측은 서비스 노드에서 했다.

    ⚠️ **이 배선으로 종전 A5 vs A6 결과(S3·64워커 2회)는 무효가 된다.** 조치의
    상대 비용이 바뀌었기 때문이다. 재측정으로 대체한다.
    """
    return [
        ActionProfile("CIRCUIT_BREAKER", "service", disruption=0.002, reversibility=0.1,
                      leadTimeSeconds=0.05,
                      mitigationByBottleneck={"S1": -0.3, "S2": 0.0, "S3": 0.8}),
        ActionProfile("BULKHEAD", "service", disruption=0.716, reversibility=0.1,
                      leadTimeSeconds=0.1,
                      mitigationByBottleneck={"S1": 0.8, "S2": 0.0, "S3": 0.2}),
        ActionProfile("BROWNOUT", "service", disruption=0.3, reversibility=0.1,
                      leadTimeSeconds=0.2,
                      mitigationByBottleneck={"S1": 0.1, "S2": 0.3, "S3": 0.2}),
        ActionProfile("SCALE_OUT", "service", disruption=0.5, reversibility=0.7,
                      leadTimeSeconds=10.0,
                      mitigationByBottleneck={"S1": -0.4, "S2": 0.0, "S3": 0.6}),
        # LLM 노드 — CB·Bulkhead 는 세 파라미터가 일반 서비스와 실질적으로 같다(§2-B 주).
        ActionProfile("CIRCUIT_BREAKER", "llm", disruption=0.15, reversibility=0.1,
                      leadTimeSeconds=0.05,
                      mitigationByBottleneck={"S1": -0.3, "S2": 0.2, "S3": 0.8}),
        ActionProfile("BULKHEAD", "llm", disruption=0.25, reversibility=0.1,
                      leadTimeSeconds=0.1,
                      mitigationByBottleneck={"S1": 0.8, "S2": 0.4, "S3": 0.2}),
        ActionProfile("BROWNOUT", "llm", disruption=0.2, reversibility=0.05,
                      leadTimeSeconds=0.2,
                      mitigationByBottleneck={"S1": 0.1, "S2": 0.9, "S3": 0.2}),
        # 레플리카 1 고정이므로 완화효과가 **정의상 0** 이다 — θ → ∞ 로 원천 배제된다.
        ActionProfile("SCALE_OUT", "llm", disruption=0.5, reversibility=0.7,
                      leadTimeSeconds=10.0,
                      mitigationByBottleneck={"S1": 0.0, "S2": 0.0, "S3": 0.0}),
    ]
