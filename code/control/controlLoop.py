"""Tier 2 폐루프 제어기 — 예측과 조치를 잇는 마지막 조각.

조각은 전부 있었는데 **이어진 적이 없었다.** 수집기·슬롯 사상·앙상블·Policy
Engine·`POST /remediation` 이 각각 검증됐지만 N초 주기로 도는 루프가 없었다.

A축 정책 변종 (§4-3)
=====================

이 루프가 없으면 A5·A6 을 돌릴 수 없고, **A6 이 핵심 주장의 실제 비교 대상**이다.

====  ==================================  ==========================================
정책   동작                                무엇을 측정하는가
====  ==================================  ==========================================
A1    루프 없음. HPA 만                    현장 기준선 — *"오토스케일링만으로는 안 된다"*
A2    규칙/임계값으로 조치 선택             신뢰도 미반영 정책
A3    위험하면 **항상 Scale-out**          GRAF 류. 보조 비교
A4    루프 없음. 무조치                    **오탐 비용의 기준선.** H5 는 이 비교로만 성립
A5    제안 — `p_eff` 와 `θₐ` 게이팅        —
A6    **신뢰도 미사용** — `p̄` 로만 argmin  **핵심 주장의 비교 대상.** A5 대비 열위여야
                                          *"신뢰도에 따라 가르는 것이 낫다"* 가 성립
====  ==================================  ==========================================

**A5 와 A6 의 차이는 κ 하나뿐이다.** A6 은 `κ=0` 이라 `p_eff = p̄` 로 환원되고
불확실성이 선택에 들어가지 않는다. 같은 예측·같은 비용함수를 쓰므로 비교가
깨끗하다 — 차이가 **신뢰도 반영 여부에서만** 온다.

⚠️ Scale-out 은 K8s 가 필요하다
================================

A3 와, A5·A6 이 Scale-out 을 고르는 경우는 Compose 에서 실행할 수 없다. 그 경우
**조치를 기록하고 실행은 건너뛴다**(`skipped` 로 남긴다). 게이트 ② 에서 K8s
드라이버를 붙이면 채워진다. 건너뛴 사실을 조용히 묻지 않는 것이 중요하다 —
A3 의 결과가 비어 있는지 실행됐는지 구분되어야 한다.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.parse
import urllib.request

import joblib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "model"))
sys.path.insert(0, str(HERE.parent / "chain"))

import metricScraper  # noqa: E402
import onlineFeatures  # noqa: E402
from policyEngine import HOLD, PolicyEngine, defaultProfiles  # noqa: E402

NORMAL_LABEL = "정상"
# 앱이 실행할 수 있는 조치. Scale-out 은 K8s 레플리카 증가라 여기 없다.
APP_LEVEL_ACTIONS = {"CIRCUIT_BREAKER", "BULKHEAD", "BROWNOUT"}

POLICIES = ("A1", "A2", "A3", "A4", "A5", "A6")
# 루프가 돌지 않는 정책. A1 은 HPA 에 맡기고 A4 는 아무것도 하지 않는다.
PASSIVE_POLICIES = {"A1", "A4"}


class ControlLoop:
    """N초 주기로 돌며 노드별 조치를 결정·실행한다."""

    def __init__(self, artifactPath: pathlib.Path, nodes: dict[str, str],
                 callGraph: dict, policy: str, periodSeconds: float = 5.0,
                 kappa: float = 1.0, availabilityLoss: float = 1.0,
                 predictionLeadSeconds: float = 30.0,
                 warmupTicks: int = 15) -> None:
        if policy not in POLICIES:
            raise ValueError(f"알 수 없는 정책: {policy} (가능: {POLICIES})")
        artifact = joblib.load(artifactPath)
        self.models = artifact["models"]
        self.classes = list(artifact["classes"])
        self.expectedDim = artifact["featureDim"]
        self.normalIndex = self.classes.index(NORMAL_LABEL)

        self.nodes = nodes
        self.policy = policy
        self.periodSeconds = periodSeconds
        self.predictionLeadSeconds = predictionLeadSeconds
        self.warmupTicks = warmupTicks

        # A6 은 κ=0 이다. p_eff = p̄ 로 환원되어 불확실성이 선택에 들어가지 않는다.
        self.engine = PolicyEngine(defaultProfiles(), availabilityLoss,
                                   kappa=(0.0 if policy == "A6" else kappa))
        self.builder = onlineFeatures.ChainFeatureBuilder(
            nodes=callGraph["nodes"], callEdges=callGraph["callEdges"])

        self.stopFlag = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.decisions: list[dict] = []
        self.appliedState: dict[str, str] = {}
        self.tickCount = 0
        self.baselineFixedAt: float | None = None

    # ── 조치 실행 ────────────────────────────────────────────────────────
    def applyRemediation(self, node: str, action: str) -> str:
        """조치를 노드에 적용한다. 실행 결과를 문자열로 돌려준다."""
        if action not in APP_LEVEL_ACTIONS and action != HOLD:
            # Scale-out — K8s 가 필요하다. 조용히 묻지 않고 skipped 로 남긴다.
            return "skipped-needs-k8s"
        target = HOLD if action == HOLD else action
        wire = "NONE" if target == HOLD else target
        if self.appliedState.get(node) == wire:
            return "unchanged"
        url = (self.nodes[node] + "/remediation?"
               + urllib.parse.urlencode({"action": wire}))
        try:
            request = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()
            self.appliedState[node] = wire
            return "applied"
        except Exception as exception:  # noqa: BLE001
            return f"failed:{type(exception).__name__}"

    # ── 예측 ──────────────────────────────────────────────────────────────
    def predict(self, features: np.ndarray) -> tuple[float, float, str]:
        """`(p̄, u, ŝ)` 를 낸다. 게이트 ① 과 같은 규약이다."""
        assert features.shape[0] == self.expectedDim, (
            f"feature 차원 불일치: {features.shape[0]} != {self.expectedDim} — "
            "학습과 추론의 feature 공간이 어긋났다")
        memberProba = np.stack([m.predict_proba(features.reshape(1, -1))[0]
                                for m in self.models])
        memberRisk = 1.0 - memberProba[:, self.normalIndex]
        meanRisk = float(memberRisk.mean())
        uncertainty = float(memberRisk.var())

        meanProba = memberProba.mean(axis=0)
        bottleneckIndexes = [i for i, name in enumerate(self.classes) if name != NORMAL_LABEL]
        bottleneck = (self.classes[bottleneckIndexes[
            int(np.argmax(meanProba[bottleneckIndexes]))]] if bottleneckIndexes else NORMAL_LABEL)
        return meanRisk, uncertainty, bottleneck

    # ── 정책별 조치 결정 ──────────────────────────────────────────────────
    def decide(self, node: str, meanRisk: float, uncertainty: float, bottleneck: str) -> dict:
        if self.policy == "A2":
            # 규칙/임계값 — 신뢰도도 병목 유형도 쓰지 않는다. 현장 관행의 재현이다.
            action = "BULKHEAD" if meanRisk > 0.5 else HOLD
            return {"action": action, "reason": f"규칙 p̄={meanRisk:.3f} > 0.5",
                    "threshold": 0.5, "effectiveRisk": meanRisk}
        if self.policy == "A3":
            # 단일 조치 고정 — 위험하면 무조건 Scale-out (GRAF 류)
            action = "SCALE_OUT" if meanRisk > 0.5 else HOLD
            return {"action": action, "reason": f"단일조치 p̄={meanRisk:.3f} > 0.5",
                    "threshold": 0.5, "effectiveRisk": meanRisk}

        # A5·A6 — 같은 비용함수를 쓰고 κ 만 다르다.
        decision = self.engine.decide(
            node=node, nodeType="service", bottleneck=bottleneck,
            meanRisk=meanRisk, uncertainty=uncertainty,
            predictionLeadSeconds=self.predictionLeadSeconds)
        return {"action": decision.action, "reason": decision.reason,
                "threshold": decision.threshold, "effectiveRisk": decision.effectiveRisk,
                "applyPoint": decision.applyPoint}

    # ── 루프 ──────────────────────────────────────────────────────────────
    def loop(self) -> None:
        while not self.stopFlag.wait(self.periodSeconds):
            tickStart = time.time()
            records = {}
            for node, baseUrl in self.nodes.items():
                raw = metricScraper.scrapeNode(baseUrl)
                if raw is not None:
                    records[node] = raw
            if not records:
                continue

            # 수집기가 누적 카운터를 비율로 바꿔 주지만 여기서는 직접 쓴다.
            # 그래서 틱 간 차분을 스스로 계산한다.
            derived = {}
            for node, raw in records.items():
                previous = getattr(self, "_previousRaw", {}).get(node)
                elapsed = tickStart - getattr(self, "_previousAt", {}).get(node, tickStart)
                derived[node] = metricScraper.deriveRates(previous, raw, elapsed)
            self._previousRaw = records
            self._previousAt = {node: tickStart for node in records}

            self.tickCount += 1
            if self.tickCount <= self.warmupTicks:
                self.builder.observeTick(derived)
                continue
            if self.baselineFixedAt is None:
                failed = self.builder.fixBaselines()
                self.baselineFixedAt = tickStart
                if failed:
                    print(f"  ⚠️ 기준선 확정 실패: {failed} — 해당 노드는 예측에서 빠진다",
                          flush=True)

            self.builder.observeTick(derived)
            if self.policy in PASSIVE_POLICIES:
                continue   # A1·A4 는 관측만 한다

            for node in self.nodes:
                features = self.builder.buildFeatures(node)
                if features is None:
                    continue
                meanRisk, uncertainty, bottleneck = self.predict(features)
                decision = self.decide(node, meanRisk, uncertainty, bottleneck)
                outcome = self.applyRemediation(node, decision["action"])
                self.decisions.append({
                    "timestamp": tickStart, "tick": self.tickCount, "node": node,
                    "policy": self.policy, "meanRisk": meanRisk, "uncertainty": uncertainty,
                    "bottleneck": bottleneck, "outcome": outcome, **decision})

    def __enter__(self) -> "ControlLoop":
        self.thread.start()
        return self

    def __exit__(self, *exceptionInfo: object) -> None:
        self.stopFlag.set()
        self.thread.join(timeout=self.periodSeconds * 3)

    def summary(self) -> dict:
        if not self.decisions:
            return {"policy": self.policy, "tickCount": self.tickCount, "decisions": 0,
                    "passive": self.policy in PASSIVE_POLICIES}
        actions: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for entry in self.decisions:
            actions[entry["action"]] = actions.get(entry["action"], 0) + 1
            outcomes[entry["outcome"]] = outcomes.get(entry["outcome"], 0) + 1
        risks = [e["meanRisk"] for e in self.decisions]
        effective = [e["effectiveRisk"] for e in self.decisions]
        return {
            "policy": self.policy, "tickCount": self.tickCount,
            "decisions": len(self.decisions),
            "actionCounts": dict(sorted(actions.items(), key=lambda kv: -kv[1])),
            "outcomeCounts": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
            "meanRiskMedian": float(np.median(risks)),
            "effectiveRiskMedian": float(np.median(effective)),
            "holdShare": actions.get(HOLD, 0) / len(self.decisions),
            "passive": False,
        }

    def writeDecisions(self, path: pathlib.Path) -> None:
        with path.open("w", encoding="utf-8") as stream:
            for entry in self.decisions:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
