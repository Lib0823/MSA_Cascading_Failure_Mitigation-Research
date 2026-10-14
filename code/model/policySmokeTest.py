"""Policy Engine 검증 — 설계 주장이 코드에서 실제로 성립하는가.

게이트 ① 과 같은 성격이다. 성능이 아니라 **주장이 구현에서 유도되는지**를 본다.
`mₐ`·`ℓₐ` 가 가정값이므로 절대 수치는 검증 대상이 아니다.

확인 항목
=========

P1  θₐ 가 비용함수에서 유도된다          — 임계가 손으로 박힌 상수가 아님
P2  H4 전반 — S1 에서 Scale-out 자동 배제  (mₐ<0 → θ=∞)
P3  H4 후반 — LLM 에서 Scale-out 원천 배제 (mₐ=0 → θ=∞, 적용 지점도 없음)
P4  Safety Guard 가 비용함수에서 유도된다   — κ↑ 면 조치가 보류로 바뀜
P5  병목 유형에 따라 선택이 달라진다        — 같은 신뢰도, 다른 정답
P6  리드타임 제약이 조치를 배제한다         — ℓₐ ≥ L_pred
P7  적용 지점이 조치 종류로 결정된다        — 위험 노드와 다를 수 있음
P8  κ=0 이면 순수 기대비용 최소화로 환원된다
"""

from __future__ import annotations

import math
import sys

from policyEngine import (APPLY_POINT, HOLD, INFINITE_THRESHOLD, PolicyEngine,
                          confidenceThreshold, defaultProfiles, effectiveRisk,
                          expectedCostOfAction, expectedCostOfHold)

AVAILABILITY_LOSS = 1.0
AMPLE_LEAD = 60.0   # 리드타임이 충분한 경우 — 제약이 걸리지 않게 한다


def profileOf(name: str, nodeType: str):
    for profile in defaultProfiles():
        if profile.name == name and profile.nodeType == nodeType:
            return profile
    raise KeyError(f"{name}/{nodeType}")


def checkThresholdDerivation() -> tuple[bool, str]:
    """P1 — θₐ 가 실제로 '보류를 이기는 경계'인가.

    공식이 맞다면 p = θ 바로 위에서 조치가 보류를 이기고, 바로 아래에서는 진다.
    상수를 박아 둔 것과 유도된 것의 차이가 여기서 갈린다.
    """
    profile = profileOf("BULKHEAD", "service")
    threshold = confidenceThreshold(profile, "S1", AVAILABILITY_LOSS)
    if not (0.0 < threshold < 1.0):
        return False, f"θ 가 [0,1] 밖이다: {threshold}"
    for delta, expectBeat in ((+1e-3, True), (-1e-3, False)):
        risk = threshold + delta
        beats = (expectedCostOfAction(risk, profile, "S1", AVAILABILITY_LOSS)
                 < expectedCostOfHold(risk, AVAILABILITY_LOSS))
        if beats != expectBeat:
            return False, f"p={risk:.4f} 에서 경계가 어긋난다 (기대 {expectBeat})"
    return True, f"θ_Bulkhead(S1) = {threshold:.3f} 경계에서 부등호가 뒤집힌다"


def checkScaleOutExcludedInS1() -> tuple[bool, str]:
    """P2 — H4 전반부. mₐ<0 이면 θ=∞ 가 되어 자동 배제된다."""
    profile = profileOf("SCALE_OUT", "service")
    threshold = confidenceThreshold(profile, "S1", AVAILABILITY_LOSS)
    if threshold != INFINITE_THRESHOLD:
        return False, f"θ 가 유한하다: {threshold}"
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    decision = engine.decide("payment", "service", "S1", meanRisk=0.99, uncertainty=0.0,
                             predictionLeadSeconds=AMPLE_LEAD)
    if decision.action == "SCALE_OUT":
        return False, "신뢰도 0.99 에서도 Scale-out 이 선택되면 안 된다"
    return True, f"θ=∞ 로 배제. p=0.99 에서도 선택은 {decision.action}"


def checkScaleOutImpossibleOnLlm() -> tuple[bool, str]:
    """P3 — H4 후반부. mₐ=0 이고 적용 지점도 없다."""
    profile = profileOf("SCALE_OUT", "llm")
    threshold = confidenceThreshold(profile, "S2", AVAILABILITY_LOSS)
    if threshold != INFINITE_THRESHOLD:
        return False, f"θ 가 유한하다: {threshold}"
    if APPLY_POINT[("SCALE_OUT", "llm")] is not None:
        return False, "적용 지점이 있으면 안 된다 (레플리카 1 고정)"
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    decision = engine.decide("assistant", "llm", "S2", meanRisk=0.99, uncertainty=0.0,
                             predictionLeadSeconds=AMPLE_LEAD)
    excluded = {c["action"]: c["excluded"] for c in decision.candidates}
    if excluded.get("SCALE_OUT") is None:
        return False, "후보에서 배제되지 않았다"
    return True, f"θ=∞ + 적용 불가. 선택은 {decision.action} (S2 의 기대 정답은 BROWNOUT)"


def checkSafetyGuardEmerges() -> tuple[bool, str]:
    """P4 — κ 를 올리면 조치가 보류로 바뀐다. 외부 규칙이 아니라 유도다."""
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    confident = engine.decide("payment", "service", "S1", 0.6, uncertainty=0.0,
                              predictionLeadSeconds=AMPLE_LEAD)
    if confident.action == HOLD:
        return False, "불확실성 0 에서 이미 보류면 검증이 성립하지 않는다"

    flipped = None
    for kappa in (0.5, 1.0, 1.5, 2.0, 3.0):
        engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=kappa)
        decision = engine.decide("payment", "service", "S1", 0.6, uncertainty=0.04,
                                 predictionLeadSeconds=AMPLE_LEAD)
        if decision.action == HOLD:
            flipped = (kappa, decision.effectiveRisk)
            break
    if flipped is None:
        return False, "κ 를 올려도 보류로 바뀌지 않는다"
    return True, (f"κ=0 → {confident.action}, κ={flipped[0]} → 보류 "
                  f"(p_eff {confident.effectiveRisk:.3f} → {flipped[1]:.3f})")


def checkBottleneckDependentChoice() -> tuple[bool, str]:
    """P5 — 같은 신뢰도인데 병목 유형이 다르면 선택이 달라진다.

    이것이 성립하지 않으면 다중 클래스 라벨(§2-0)의 존재 이유가 사라진다.
    """
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    chosen = {}
    for bottleneck in ("S1", "S3"):
        decision = engine.decide("payment", "service", bottleneck, 0.7, uncertainty=0.0,
                                 predictionLeadSeconds=AMPLE_LEAD)
        chosen[bottleneck] = decision.action
    if chosen["S1"] == chosen["S3"]:
        return False, f"S1·S3 둘 다 {chosen['S1']} — 유형 추정이 필요 없다는 뜻이 된다"
    return True, f"S1 → {chosen['S1']}, S3 → {chosen['S3']} (같은 p=0.7)"


def checkLeadTimeExclusion() -> tuple[bool, str]:
    """P6 — ℓₐ ≥ L_pred 인 조치는 후보에서 빠지고, **선택이 실제로 바뀐다** (H6).

    ⚠️ 기본 파라미터로는 약한 테스트가 된다. Scale-out 은 S3 에서도 CB 보다 비싸
    애초에 선택되지 않으므로, 배제돼도 선택이 안 바뀐다 — 제약이 작동했다는 증거가
    되지 못한다. 그래서 **Scale-out 이 최선이 되는 프로파일을 일부러 만들어** 엔진
    로직 자체를 겨냥한다.
    """
    from policyEngine import ActionProfile

    # 느리지만 압도적으로 효과적인 조치 하나와, 빠르지만 약한 조치 하나.
    profiles = [
        ActionProfile("SLOW_STRONG", "service", disruption=0.05, reversibility=0.1,
                      leadTimeSeconds=10.0, mitigationByBottleneck={"S3": 0.95}),
        ActionProfile("FAST_WEAK", "service", disruption=0.4, reversibility=0.3,
                      leadTimeSeconds=0.1, mitigationByBottleneck={"S3": 0.5}),
    ]
    APPLY_POINT[("SLOW_STRONG", "service")] = "self"
    APPLY_POINT[("FAST_WEAK", "service")] = "caller"
    engine = PolicyEngine(profiles, AVAILABILITY_LOSS, kappa=0.0)

    ample = engine.decide("payment", "service", "S3", 0.9, 0.0, predictionLeadSeconds=60.0)
    tight = engine.decide("payment", "service", "S3", 0.9, 0.0, predictionLeadSeconds=1.0)
    if ample.action != "SLOW_STRONG":
        return False, f"리드타임이 넉넉하면 강한 조치를 골라야 한다: {ample.action}"
    if tight.action == "SLOW_STRONG":
        return False, "L_pred=1s 인데 ℓ=10s 조치가 선택됐다"
    tightExcluded = {c["action"]: c["excluded"] for c in tight.candidates}
    if tightExcluded.get("SLOW_STRONG") != "리드타임":
        return False, "배제 사유가 리드타임이어야 한다"

    # 기본 프로파일에서도 배제 자체는 일어나는지 확인한다.
    baseEngine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    baseTight = baseEngine.decide("payment", "service", "S3", 0.9, 0.0, predictionLeadSeconds=1.0)
    baseExcluded = {c["action"]: c["excluded"] for c in baseTight.candidates}
    if baseExcluded.get("SCALE_OUT") != "리드타임":
        return False, "기본 프로파일에서 Scale-out 이 배제되지 않았다"
    return True, (f"L_pred 60s → {ample.action}, 1s → {tight.action} (선택이 바뀐다). "
                  "기본 프로파일에서도 Scale-out 이 리드타임으로 배제됨")


def checkApplyPoint() -> tuple[bool, str]:
    """P7 — 조치를 적용하는 노드가 위험 노드와 항상 같지는 않다 (§2-C)."""
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    decision = engine.decide("payment", "service", "S1", 0.9, 0.0, AMPLE_LEAD)
    if decision.applyPoint != "caller":
        return False, f"Bulkhead 의 적용 지점은 호출자여야 한다: {decision.applyPoint}"
    if APPLY_POINT[("SCALE_OUT", "service")] != "self":
        return False, "Scale-out 은 자신에게 적용된다"
    if APPLY_POINT[("BROWNOUT", "llm")] != "self":
        return False, "LLM 의 Brownout 은 자신의 요청 파라미터다"
    return True, (f"{decision.action} → {decision.applyPoint} (위험 노드 payment 와 다름). "
                  "노드 타입에 따라 Brownout 의 지점이 갈린다")


def checkKappaZeroReduction() -> tuple[bool, str]:
    """P8 — κ=0 이면 불확실성이 무시되고 순수 기대비용 최소화가 된다."""
    for uncertainty in (0.0, 0.05, 0.2):
        if not math.isclose(effectiveRisk(0.7, uncertainty, 0.0), 0.7):
            return False, f"κ=0 인데 u={uncertainty} 가 p_eff 를 바꿨다"
    engine = PolicyEngine(defaultProfiles(), AVAILABILITY_LOSS, kappa=0.0)
    first = engine.decide("payment", "service", "S1", 0.7, 0.0, AMPLE_LEAD)
    second = engine.decide("payment", "service", "S1", 0.7, 0.2, AMPLE_LEAD)
    if first.action != second.action:
        return False, "κ=0 인데 불확실성이 선택을 바꿨다"
    return True, f"κ=0 에서 u 와 무관하게 {first.action}"


CHECKS = (
    ("P1  θₐ 가 비용함수에서 유도됨", checkThresholdDerivation),
    ("P2  H4 전반 — S1 에서 Scale-out 배제", checkScaleOutExcludedInS1),
    ("P3  H4 후반 — LLM Scale-out 원천 배제", checkScaleOutImpossibleOnLlm),
    ("P4  Safety Guard 가 유도됨", checkSafetyGuardEmerges),
    ("P5  병목 유형이 선택을 바꿈", checkBottleneckDependentChoice),
    ("P6  리드타임 제약이 조치를 배제", checkLeadTimeExclusion),
    ("P7  적용 지점이 조치 종류로 결정됨", checkApplyPoint),
    ("P8  κ=0 이면 기대비용 최소화로 환원", checkKappaZeroReduction),
)


def main() -> int:
    print("Policy Engine 검증 — mₐ·ℓₐ 는 가정값이므로 절대 수치는 검증 대상이 아니다\n")
    failures = 0
    for name, check in CHECKS:
        passed, detail = check()
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        print(f"      {detail}")
        failures += (not passed)

    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} 통과")
    if failures:
        return 1

    print("\n━━━ 유도된 임계값 θₐ (L=1.0, 가정 파라미터) ━━━")
    print(f"{'조치':<18}{'노드':<10}{'S1':>10}{'S2':>10}{'S3':>10}")
    for profile in defaultProfiles():
        cells = []
        for bottleneck in ("S1", "S2", "S3"):
            threshold = confidenceThreshold(profile, bottleneck, AVAILABILITY_LOSS)
            cells.append("∞" if threshold == INFINITE_THRESHOLD else f"{threshold:.3f}")
        print(f"{profile.name:<18}{profile.nodeType:<10}" + "".join(f"{c:>10}" for c in cells))
    print("\n∞ = 어떤 신뢰도로도 보류를 이기지 못함. S1·S2 의 Scale-out 이 여기 해당하며,")
    print("   외부 규칙이 아니라 비용함수가 스스로 배제한 결과다 (H4).")
    print()
    print("⚠️ 유한한 θ 가 대부분 0.05 미만이다. θ = D·R / (m·L − D(1−R)) 에서 가역성 R 을")
    print("   0.1 로 가정했기 때문이며, **되돌리기가 거의 공짜면 오탐해도 손해가 없으므로")
    print("   임계가 0 에 수렴한다** — 수학적으로는 옳다. 다만 그러면 신뢰도 구간이")
    print("   실질적으로 생기지 않고 거의 항상 조치하게 된다. 구간이 의미를 가지려면")
    print("   D·R 이 L 에 비해 충분히 커야 하며, 이는 실측(§4-4)이 정할 값이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
