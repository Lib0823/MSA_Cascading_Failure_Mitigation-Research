"""게이트 ① — 모델 스모크 테스트.

가짜 6~8노드 그래프와 랜덤 feature 위에서 예측 계층(②GBM·③GAT)이
코드로 성립하는지 확인한다. 성능·정확도는 검증 대상이 아니다(랜덤 데이터).

확인 항목 (research/timeline.md ① / docs/proposal.md §2-A, §4-2):
    C1  텐서 shape 정합 — 노드별 4클래스 출력
    C2  앙상블 불확실성이 0으로 붕괴하지 않음
    C3  노드 수를 6→20으로 바꿔도 GAT 파라미터 수가 불변 (공유 per-node head)
    C4  ② 이웃 집계 feature + GBM 앙상블이 같은 규약으로 동작
    C5  p_eff = max(0, p̄ − κ√u) 가 κ에 대해 단조 감소 (Safety Guard 유도)

실행:
    python code/model/smokeTest.py
"""

from __future__ import annotations

import math
import sys

import numpy as np
import torch
import torch.nn as nn
from sklearn.ensemble import HistGradientBoostingClassifier
from torch_geometric.nn import GATConv

# --- 모델 규약 (docs/proposal.md §2-A) --------------------------------------

NUM_SLOTS = 6                 # 의미 기준 공통 슬롯 f1..f6
NUM_WINDOW_STATS = 3          # 윈도우 평균·slope·표준편차
NUM_NUMERIC_FEATURES = NUM_SLOTS * (1 + NUM_WINDOW_STATS)   # 6 + 18 = 24
NUM_NODE_TYPES = 2            # 0 = 일반 서비스, 1 = LLM 추론 노드
TYPE_EMBED_DIM = 4
NUM_CLASSES = 4               # {정상, S1, S2, S3}
CLASS_NAMES = ("정상", "S1", "S2", "S3")

HIDDEN_DIM = 32
NUM_HEADS = 4
ENSEMBLE_SIZE = 3             # 소형 앙상블 N=3
AGGREGATION_HOPS = 2          # ②의 이웃 집계 범위 (라벨 판정의 1-hop과 구분)

# 전파 방향 규약 (§4-2).
#   callEdges 는 호출 방향 (caller → callee) 으로 저장한다.
#   장애는 의존 대상에서 호출자로 번지므로, 노드 v 의 "상류"는 v 가 호출하는 쪽이다.
#   따라서 메시지 패싱과 이웃 집계는 호출 방향을 뒤집은 간선을 쓴다.


# --- 가짜 그래프 -------------------------------------------------------------


def buildFakeTopology(numNodes: int) -> tuple[np.ndarray, np.ndarray]:
    """깊이 4단의 가짜 호출 그래프를 만든다.

    게이트웨이 1개에서 시작해 각 노드를 앞선 노드 중 하나에 붙이고,
    깊이가 4단을 넘지 않도록 제한한다. 마지막 노드를 LLM 추론 노드로 둔다.

    Args:
        numNodes: 노드 수.

    Returns:
        callEdges: `[2, E]` 정수 배열. 0행이 caller, 1행이 callee.
        nodeTypes: `[numNodes]` 정수 배열. 0 = 일반 서비스, 1 = LLM 노드.
    """
    rng = np.random.default_rng(seed=0)
    depth = np.zeros(numNodes, dtype=np.int64)
    callers: list[int] = []
    callees: list[int] = []

    for node in range(1, numNodes):
        eligible = [p for p in range(node) if depth[p] < 3]
        parent = int(rng.choice(eligible)) if eligible else 0
        depth[node] = depth[parent] + 1
        callers.append(parent)
        callees.append(node)

    nodeTypes = np.zeros(numNodes, dtype=np.int64)
    nodeTypes[-1] = 1
    return np.array([callers, callees], dtype=np.int64), nodeTypes


def buildFakeFeatures(numNodes: int, seed: int) -> np.ndarray:
    """랜덤 노드 feature `[numNodes, 24]` 를 만든다.

    레이아웃은 `x_v(t) = [f1..f6 현재값] ⊕ [f1..f6 평균·slope·표준편차]` 이다.
    포화율·사용률 슬롯은 [0, 1] 범위라 그대로 두고, slope 만 부호를 허용한다.
    """
    rng = np.random.default_rng(seed)
    current = rng.uniform(0.0, 1.0, size=(numNodes, NUM_SLOTS))
    windowMean = rng.uniform(0.0, 1.0, size=(numNodes, NUM_SLOTS))
    windowSlope = rng.normal(0.0, 0.1, size=(numNodes, NUM_SLOTS))
    windowStd = rng.uniform(0.0, 0.2, size=(numNodes, NUM_SLOTS))
    features = np.concatenate([current, windowMean, windowSlope, windowStd], axis=1)
    return features.astype(np.float32)


def toPropagationEdges(callEdges: np.ndarray) -> torch.Tensor:
    """호출 간선을 전파 간선(의존 대상 → 호출자)으로 뒤집어 텐서로 만든다."""
    return torch.from_numpy(callEdges[[1, 0], :].copy())


# --- ③ GAT -------------------------------------------------------------------


class NodeRiskGat(nn.Module):
    """노드별 4클래스 위험도를 내는 GAT.

    노드 타입 임베딩을 수치 feature에 이어 붙인 뒤 GAT 2층을 통과시키고,
    모든 노드에 **동일한** MLP head를 적용한다. 따라서 파라미터 수가
    노드 수와 무관하다(§2-A, GRAF의 flatten과 대비되는 확장성 근거).
    """

    def __init__(self, dropoutRate: float = 0.2) -> None:
        super().__init__()
        self.typeEmbedding = nn.Embedding(NUM_NODE_TYPES, TYPE_EMBED_DIM)
        inputDim = NUM_NUMERIC_FEATURES + TYPE_EMBED_DIM

        self.conv1 = GATConv(inputDim, HIDDEN_DIM, heads=NUM_HEADS, concat=True)
        self.conv2 = GATConv(HIDDEN_DIM * NUM_HEADS, HIDDEN_DIM, heads=1, concat=False)
        self.dropout = nn.Dropout(dropoutRate)
        # 공유 per-node head — 노드마다 따로 두지 않는다.
        self.sharedHead = nn.Sequential(
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, NUM_CLASSES),
        )

    def forward(
        self,
        numericFeatures: torch.Tensor,
        nodeTypes: torch.Tensor,
        propagationEdges: torch.Tensor,
    ) -> torch.Tensor:
        """로짓 `[numNodes, NUM_CLASSES]` 를 낸다."""
        h = torch.cat([numericFeatures, self.typeEmbedding(nodeTypes)], dim=1)
        h = torch.relu(self.conv1(h, propagationEdges))
        h = self.dropout(h)
        h = torch.relu(self.conv2(h, propagationEdges))
        return self.sharedHead(h)


class GatEnsemble:
    """서로 다른 시드로 초기화한 소형 GAT 앙상블 (N=3)."""

    def __init__(self, size: int = ENSEMBLE_SIZE) -> None:
        self.members: list[NodeRiskGat] = []
        for memberIndex in range(size):
            torch.manual_seed(1000 + memberIndex)
            member = NodeRiskGat()
            member.eval()
            self.members.append(member)

    def predictProba(
        self,
        numericFeatures: torch.Tensor,
        nodeTypes: torch.Tensor,
        propagationEdges: torch.Tensor,
    ) -> np.ndarray:
        """멤버별 클래스 확률 `[N, numNodes, NUM_CLASSES]` 를 낸다."""
        with torch.no_grad():
            stacked = [
                torch.softmax(
                    member(numericFeatures, nodeTypes, propagationEdges), dim=1
                )
                for member in self.members
            ]
        return torch.stack(stacked).numpy()

    def countParameters(self) -> int:
        """멤버 1개의 학습 파라미터 수."""
        return sum(p.numel() for p in self.members[0].parameters() if p.requires_grad)


# --- 앙상블 → (p̄, u, p_eff) ---------------------------------------------------


def summarizeEnsemble(
    memberProba: np.ndarray, kappa: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """멤버 확률에서 위험 확률·불확실성·신뢰도 반영 위험도를 뽑는다.

    위반 확률은 `1 − P(정상)` 으로 정의한다. 불확실성은 그 값의 멤버 간
    분산이며, 의사결정에는 `p_eff = max(0, p̄ − κ√u)` 를 쓴다(§2-B).

    Args:
        memberProba: `[N, numNodes, NUM_CLASSES]` 멤버별 클래스 확률.
        kappa: 불확실성 회피 강도. 0이면 순수 기대비용 최소화로 환원된다.

    Returns:
        pBar, u, pEff — 각각 `[numNodes]`.
    """
    memberRisk = 1.0 - memberProba[:, :, 0]
    pBar = memberRisk.mean(axis=0)
    u = memberRisk.var(axis=0)
    pEff = np.maximum(0.0, pBar - kappa * np.sqrt(u))
    return pBar, u, pEff


# --- ② 이웃 집계 feature + GBM ------------------------------------------------


def collectUpstreamByHop(
    callEdges: np.ndarray, numNodes: int, hops: int
) -> list[list[list[int]]]:
    """각 노드의 hop별 상류(= 의존 대상) 집합을 모은다.

    Returns:
        `upstream[hop][node]` 형태의 중첩 리스트. hop 은 0-based(0이 1-hop).
    """
    directUpstream: list[list[int]] = [[] for _ in range(numNodes)]
    for caller, callee in zip(callEdges[0], callEdges[1], strict=True):
        directUpstream[int(caller)].append(int(callee))

    byHop = [directUpstream]
    for _ in range(hops - 1):
        previous = byHop[-1]
        nextHop: list[list[int]] = []
        for node in range(numNodes):
            reached = {n for mid in previous[node] for n in directUpstream[mid]}
            nextHop.append(sorted(reached - {node}))
        byHop.append(nextHop)
    return byHop


def buildTier2Features(
    numericFeatures: np.ndarray, callEdges: np.ndarray, numNodes: int
) -> np.ndarray:
    """②의 이웃 집계 feature를 만든다 (§2-A).

    구성은 `[self 24] ⊕ [1-hop 상류 mean/max/sum 72] ⊕ [2-hop 상류 mean/max/sum 72]
    ⊕ [하류 이웃의 큐 대기율·지연 분위수 2] ⊕ [구조 지표 4]` 이다.
    본질적으로 message passing을 손으로 2층 전개한 것이다.

    Returns:
        `[numNodes, 174]` 배열.
    """
    upstreamByHop = collectUpstreamByHop(callEdges, numNodes, AGGREGATION_HOPS)

    downstream: list[list[int]] = [[] for _ in range(numNodes)]
    for caller, callee in zip(callEdges[0], callEdges[1], strict=True):
        downstream[int(callee)].append(int(caller))

    blocks = [numericFeatures]
    for hopNeighbors in upstreamByHop:
        aggregated = np.zeros((numNodes, NUM_NUMERIC_FEATURES * 3), dtype=np.float32)
        for node, neighbors in enumerate(hopNeighbors):
            if not neighbors:
                continue
            window = numericFeatures[neighbors]
            aggregated[node] = np.concatenate(
                [window.mean(axis=0), window.max(axis=0), window.sum(axis=0)]
            )
        blocks.append(aggregated)

    # 하류 이웃(호출자)의 f2 큐 대기율 · f4 지연 분위수 현재값 평균.
    downstreamSignal = np.zeros((numNodes, 2), dtype=np.float32)
    for node, neighbors in enumerate(downstream):
        if neighbors:
            downstreamSignal[node] = numericFeatures[neighbors][:, [1, 3]].mean(axis=0)
    blocks.append(downstreamSignal)

    blocks.append(buildStructuralFeatures(callEdges, numNodes))
    return np.concatenate(blocks, axis=1).astype(np.float32)


def buildStructuralFeatures(callEdges: np.ndarray, numNodes: int) -> np.ndarray:
    """fan-in, fan-out, 경로 깊이, 연결 중심성 `[numNodes, 4]`."""
    fanIn = np.zeros(numNodes, dtype=np.float32)
    fanOut = np.zeros(numNodes, dtype=np.float32)
    for caller, callee in zip(callEdges[0], callEdges[1], strict=True):
        fanOut[int(caller)] += 1.0
        fanIn[int(callee)] += 1.0

    parentOf = {int(callee): int(caller) for caller, callee in zip(*callEdges, strict=True)}
    depth = np.zeros(numNodes, dtype=np.float32)
    for node in range(numNodes):
        cursor, hops = node, 0
        while cursor in parentOf and hops < numNodes:
            cursor = parentOf[cursor]
            hops += 1
        depth[node] = float(hops)

    denominator = max(numNodes - 1, 1)
    centrality = (fanIn + fanOut) / denominator
    return np.stack([fanIn, fanOut, depth, centrality], axis=1)


def buildTier2Ensemble(
    trainFeatures: np.ndarray, trainLabels: np.ndarray, size: int = ENSEMBLE_SIZE
) -> list[HistGradientBoostingClassifier]:
    """시드만 바꾼 GBM 앙상블을 학습한다.

    LightGBM 대신 sklearn `HistGradientBoostingClassifier` 를 쓴다
    (macOS arm64 `libomp` 의존을 피하기 위함).
    """
    ensemble = []
    for memberIndex in range(size):
        model = HistGradientBoostingClassifier(
            max_iter=30,
            max_depth=3,
            learning_rate=0.2,
            random_state=memberIndex,
        )
        model.fit(trainFeatures, trainLabels)
        ensemble.append(model)
    return ensemble


# --- 체크 --------------------------------------------------------------------


def checkTensorShapes() -> str:
    """C1 — 노드별 4클래스 출력과 (p̄, u, p_eff) 의 shape 정합."""
    numNodes = 7
    callEdges, nodeTypesArray = buildFakeTopology(numNodes)
    numericFeatures = torch.from_numpy(buildFakeFeatures(numNodes, seed=7))
    nodeTypes = torch.from_numpy(nodeTypesArray)
    propagationEdges = toPropagationEdges(callEdges)

    ensemble = GatEnsemble()
    memberProba = ensemble.predictProba(numericFeatures, nodeTypes, propagationEdges)

    assert memberProba.shape == (ENSEMBLE_SIZE, numNodes, NUM_CLASSES), (
        f"멤버 확률 shape 불일치: {memberProba.shape}"
    )
    assert np.allclose(memberProba.sum(axis=2), 1.0, atol=1e-5), "클래스 확률 합이 1이 아니다"

    pBar, u, pEff = summarizeEnsemble(memberProba, kappa=1.0)
    for name, value in (("p̄", pBar), ("u", u), ("p_eff", pEff)):
        assert value.shape == (numNodes,), f"{name} shape 불일치: {value.shape}"
    assert np.all((pBar >= 0.0) & (pBar <= 1.0)), "p̄ 가 확률 범위를 벗어났다"

    return (
        f"멤버 확률 {memberProba.shape} → p̄/u/p_eff {pBar.shape}, "
        f"클래스 {NUM_CLASSES}종 {CLASS_NAMES}"
    )


def checkUncertaintySpread() -> str:
    """C2 — 소형 앙상블의 불확실성이 0으로 붕괴하지 않는가."""
    numNodes = 7
    callEdges, nodeTypesArray = buildFakeTopology(numNodes)
    numericFeatures = torch.from_numpy(buildFakeFeatures(numNodes, seed=11))
    nodeTypes = torch.from_numpy(nodeTypesArray)
    propagationEdges = toPropagationEdges(callEdges)

    memberProba = GatEnsemble().predictProba(numericFeatures, nodeTypes, propagationEdges)
    _, u, _ = summarizeEnsemble(memberProba, kappa=1.0)
    spread = float(np.sqrt(u).mean())

    assert spread > 1e-4, f"앙상블 불일치가 사실상 0이다 (평균 표준편차 {spread:.2e})"
    assert float(u.std()) > 1e-8, "모든 노드의 불확실성이 동일하다 — 노드별 판별이 불가능"

    return (
        f"위반 확률의 멤버 간 평균 표준편차 √u = {spread:.4f} "
        f"(노드별 범위 {float(np.sqrt(u).min()):.4f} ~ {float(np.sqrt(u).max()):.4f})"
    )


def checkParameterInvariance() -> str:
    """C3 — 노드 수 6→20에서 파라미터 수가 불변인가 (공유 per-node head)."""
    counts = {}
    for numNodes in (6, 20):
        callEdges, nodeTypesArray = buildFakeTopology(numNodes)
        numericFeatures = torch.from_numpy(buildFakeFeatures(numNodes, seed=3))
        nodeTypes = torch.from_numpy(nodeTypesArray)
        propagationEdges = toPropagationEdges(callEdges)

        ensemble = GatEnsemble()
        memberProba = ensemble.predictProba(numericFeatures, nodeTypes, propagationEdges)
        assert memberProba.shape[1] == numNodes, "출력 노드 수가 입력과 다르다"
        counts[numNodes] = ensemble.countParameters()

    assert counts[6] == counts[20], (
        f"파라미터 수가 노드 수에 의존한다: 6노드 {counts[6]:,} vs 20노드 {counts[20]:,}"
    )
    return f"6노드·20노드 모두 멤버당 {counts[6]:,} 파라미터 — 노드 수와 무관"


def checkTier2Pipeline() -> str:
    """C4 — ② 이웃 집계 feature + GBM 앙상블이 같은 규약으로 동작하는가."""
    numNodes = 7
    callEdges, _ = buildFakeTopology(numNodes)
    rng = np.random.default_rng(seed=42)

    trainRows, trainLabels = [], []
    for episode in range(40):
        features = buildFakeFeatures(numNodes, seed=100 + episode)
        trainRows.append(buildTier2Features(features, callEdges, numNodes))
        trainLabels.append(rng.integers(0, NUM_CLASSES, size=numNodes))
    trainFeatures = np.concatenate(trainRows, axis=0)
    trainLabels = np.concatenate(trainLabels, axis=0)

    expectedDim = NUM_NUMERIC_FEATURES * (1 + 3 * AGGREGATION_HOPS) + 2 + 4
    assert trainFeatures.shape[1] == expectedDim, (
        f"② feature 차원 불일치: {trainFeatures.shape[1]} (기대 {expectedDim})"
    )
    assert set(np.unique(trainLabels)) == set(range(NUM_CLASSES)), "4클래스가 모두 나오지 않았다"

    ensemble = buildTier2Ensemble(trainFeatures, trainLabels)
    evalFeatures = buildTier2Features(
        buildFakeFeatures(numNodes, seed=999), callEdges, numNodes
    )
    memberProba = np.stack([m.predict_proba(evalFeatures) for m in ensemble])

    assert memberProba.shape == (ENSEMBLE_SIZE, numNodes, NUM_CLASSES), (
        f"② 멤버 확률 shape 불일치: {memberProba.shape}"
    )
    pBar, u, pEff = summarizeEnsemble(memberProba, kappa=1.0)
    assert pBar.shape == u.shape == pEff.shape == (numNodes,), "② 요약 통계 shape 불일치"

    return (
        f"② feature {trainFeatures.shape[1]}차원 "
        f"(self {NUM_NUMERIC_FEATURES} + {AGGREGATION_HOPS}-hop 집계 "
        f"{NUM_NUMERIC_FEATURES * 3 * AGGREGATION_HOPS} + 하류 2 + 구조 4), "
        f"③과 동일한 (p̄, u, p_eff) 규약으로 환원"
    )


def checkSafetyGuardMonotonicity() -> str:
    """C5 — p_eff 가 κ에 대해 단조 감소하고 0에서 절단되는가."""
    numNodes = 7
    callEdges, nodeTypesArray = buildFakeTopology(numNodes)
    numericFeatures = torch.from_numpy(buildFakeFeatures(numNodes, seed=5))
    nodeTypes = torch.from_numpy(nodeTypesArray)
    propagationEdges = toPropagationEdges(callEdges)
    memberProba = GatEnsemble().predictProba(numericFeatures, nodeTypes, propagationEdges)

    pBar, _, previous = summarizeEnsemble(memberProba, kappa=0.0)
    assert np.allclose(pBar, previous), "κ=0 에서 p_eff 가 p̄ 로 환원되지 않는다"

    for kappa in (0.5, 1.0, 2.0, 50.0):
        _, _, pEff = summarizeEnsemble(memberProba, kappa=kappa)
        assert np.all(pEff <= previous + 1e-9), f"κ={kappa} 에서 p_eff 가 증가했다"
        assert np.all(pEff >= 0.0), f"κ={kappa} 에서 p_eff 가 음수다"
        previous = pEff

    assert np.all(previous == 0.0), "κ를 크게 올려도 p_eff 가 0으로 절단되지 않는다"
    return "κ=0 → p̄ 환원, κ↑ → p_eff 단조 감소, 큰 κ에서 전원 보류(0)"


# --- 실행 --------------------------------------------------------------------

CHECKS = (
    ("C1  텐서 shape 정합", checkTensorShapes),
    ("C2  앙상블 불확실성 폭", checkUncertaintySpread),
    ("C3  파라미터 수 불변 (6→20노드)", checkParameterInvariance),
    ("C4  ② 이웃 집계 + GBM 앙상블", checkTier2Pipeline),
    ("C5  p_eff 의 Safety Guard 성질", checkSafetyGuardMonotonicity),
)


def main() -> int:
    """모든 체크를 돌리고 실패 개수를 종료 코드로 돌려준다."""
    torch.set_num_threads(1)
    print("게이트 ① 모델 스모크 테스트 — 랜덤 데이터, CPU 전용\n")

    failures = 0
    for name, check in CHECKS:
        try:
            detail = check()
        except AssertionError as error:
            failures += 1
            print(f"  FAIL  {name}\n        {error}")
        else:
            print(f"  PASS  {name}\n        {detail}")

    print()
    if failures:
        print(f"{failures}건 실패 — 게이트 ① 미통과")
    else:
        print("전부 통과 — ②의 입력 스펙(feature 차원·그래프 포맷) 확정 가능")
    return failures


if __name__ == "__main__":
    sys.exit(min(main(), 1))
