"""endToEnd 악화 진단 — 평가 집합을 고정하고 학습 집합만 바꾼다 (J35).

사용: .venv/bin/python code/model/diagnoseEndToEnd.py              (평가 고정 절제, 약 2분)
      .venv/bin/python code/model/diagnoseEndToEnd.py --reproduce  (커밋된 endToEnd.json 재현)
결과: research/challenges.md J35. 출력 원본은 code/model/diagnoseEndToEnd.json.

endToEnd 는 GroupKFold 첫 접기를 평가 집합으로 쓰므로 파일을 더하면 평가 집합까지 바뀐다.
그래서 "추가 데이터가 모델을 망쳤는가(학습 효과)" 와 "추가 표본 자체가 오탐을 내는가
(평가 구성 효과)" 가 섞인다. 여기서는 10-15 코퍼스의 평가 접기를 고정한다.

함수는 endToEnd·compareTiers 의 것을 그대로 쓴다 — 정의가 같아야 비교가 된다.
"""
import argparse
import collections
import json
import pathlib
import subprocess
import sys
import types

import numpy as np
from sklearn.model_selection import GroupKFold

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code" / "model"))
import buildDataset  # noqa: E402
import compareTiers  # noqa: E402
import endToEnd  # noqa: E402
import policyEngine  # noqa: E402
from policyEngine import HOLD, PolicyEngine  # noqa: E402

NORMAL = endToEnd.NORMAL_LABEL
# 10-15(endToEnd.json 의 마지막 생성) 이후 코퍼스에 들어온 파일.
ADDED_NORMAL = {"remediation-20261101-170108.json"}   # `Dₐ` 측정용 normal × 강제 조치 4셀
ADDED_S3 = {"episodes-20261101-005334.json", "episodes-20261101-005654.json",
            "episodes-20261101-010013.json", "episodes-20261101-010332.json"}
ARGS = argparse.Namespace(horizonSeconds=10.0, precursorWindowSeconds=30.0, latencyMultiplier=2.0,
                          errorRateThreshold=0.05, precursorMultiplier=1.5)
CHAIN = ROOT / "code" / "chain"


def load():
    records = []
    for path in sorted((CHAIN / "results").glob("*.json")):
        if not (path.name.startswith("episodes-") or path.name.startswith("remediation-")):
            continue
        for record in json.loads(path.read_text(encoding="utf-8")):
            if record.get("injectedAt") is None or record.get("backend", "compose") != "compose":
                continue
            group = ("norm" if path.name in ADDED_NORMAL else
                     "s3new" if path.name in ADDED_S3 else "old")
            records.append((group, path.name, record))
    episodes = []
    for group, name, record in records:
        try:
            episodes.append((group, name, record, buildDataset.buildEpisodeSamples(record, CHAIN, ARGS)))
        except FileNotFoundError:
            continue
    return episodes


def arrays(episodes, offset=0):
    features, labels, groups, nodes, sources = [], [], [], [], []
    for index, (group, name, record, built) in enumerate(episodes):
        for sample in built["samples"]:
            features.append(np.concatenate([sample["self"], sample["neighbour"]]))
            labels.append(sample["label"])
            groups.append(offset + index)
            nodes.append(sample["node"])
            sources.append(f"{record.get('scenario')}/{record.get('remediation') or ''}")
    return (np.array(features), np.array(labels), np.array(groups), np.array(nodes),
            np.array(sources))


def fit(features, labels, groups):
    """endToEnd 와 같다 — train 을 다시 쪼개 보정기를 base 가 안 본 표본으로 학습한다."""
    fitLocal, calibLocal = next(iter(GroupKFold(n_splits=3).split(features, labels, groups)))
    return compareTiers.buildCalibratedEnsemble(
        features[fitLocal], labels[fitLocal], features[calibLocal], labels[calibLocal],
        size=5, method="sigmoid")


def evaluate(models, features, labels, nodes, profiles):
    meanRisk, uncertainty, bottleneck = endToEnd.summarizeEnsemble(models, features)
    engine = PolicyEngine(profiles, availabilityLoss=1.0, kappa=1.0)
    actions = np.array([engine.decide(node=str(n), nodeType="service", bottleneck=str(b),
                                      meanRisk=float(r), uncertainty=float(u),
                                      predictionLeadSeconds=10.0).action
                        for n, b, r, u in zip(nodes, bottleneck, meanRisk, uncertainty)])
    normal = labels == NORMAL
    acted = normal & (actions != HOLD)
    isPositive = (~normal).astype(float)
    edges = np.linspace(0.0, 1.0, 11)
    bins = np.clip(np.digitize(meanRisk, edges) - 1, 0, 9)
    ece = sum(abs(meanRisk[bins == b].mean() - isPositive[bins == b].mean()) * (bins == b).mean()
              for b in range(10) if (bins == b).any())
    effective = np.maximum(0.0, meanRisk - np.sqrt(uncertainty))
    return {
        "n": int(len(labels)), "normal": int(normal.sum()),
        "fpr": float(acted.sum() / normal.sum()) if normal.any() else float("nan"),
        "ece": float(ece),
        "fpActions": dict(collections.Counter(actions[acted])),
        "normalRiskMedian": float(np.median(meanRisk[normal])) if normal.any() else float("nan"),
        "normalRiskP90": float(np.percentile(meanRisk[normal], 90)) if normal.any() else float("nan"),
        "normalEffectiveAboveCb": float((effective[normal] > 0.00025).mean()) if normal.any() else float("nan"),
        "normalTaggedS3": float((bottleneck[normal] == "S3").mean()) if normal.any() else float("nan"),
        "fpEffectiveMedian": float(np.median(effective[acted])) if acted.any() else float("nan"),
    }


def legacyProfiles():
    """10-15 의 엔진(커밋 7d1b035) — 배선 전 가정 Dₐ."""
    source = subprocess.run(["git", "show", "7d1b035:code/model/policyEngine.py"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout
    module = types.ModuleType("policyEngineLegacy")
    module.__dict__["__name__"] = "policyEngineLegacy"
    sys.modules["policyEngineLegacy"] = module
    exec(compile(source, "policyEngineLegacy", "exec"), module.__dict__)
    return module.defaultProfiles()


def row(name, result):
    actions = ", ".join(f"{k} {v}" for k, v in sorted(result["fpActions"].items())) or "-"
    print(f"{name:<34}{result['n']:>6}{result['normal']:>6}{result['fpr']:>7.3f}{result['ece']:>8.4f}"
          f"{result['normalRiskMedian']:>9.4f}{result['normalRiskP90']:>8.3f}"
          f"{result['normalEffectiveAboveCb']:>8.1%}{result['normalTaggedS3']:>7.1%}"
          f"{result['fpEffectiveMedian']:>9.4f}  {actions}")


def main():
    episodes = load()
    old = [e for e in episodes if e[0] == "old"]
    norm = [e for e in episodes if e[0] == "norm"]
    s3new = [e for e in episodes if e[0] == "s3new"]
    print(f"에피소드: 10-15 {len(old)} / normal 셀 {len(norm)} / 새 S3 {len(s3new)}")

    X, y, g, nodes, src = arrays(old)
    trainIndex, testIndex = next(iter(GroupKFold(n_splits=4).split(X, y, g)))
    print(f"고정 평가 집합: 학습 {len(trainIndex)} / 평가 {len(testIndex)} (실행 ① 과 같아야 한다: 8031 / 2816)")
    Xn, yn, gn, nn, sn = arrays(norm, offset=10_000)
    Xs, ys, gs, ns, ss = arrays(s3new, offset=20_000)

    current = policyEngine.defaultProfiles()
    header = (f"{'조건 (평가 = 10-15 평가 접기 고정)':<34}{'표본':>6}{'정상':>6}{'FPR':>7}{'ECE':>8}"
              f"{'정상p̄중앙':>9}{'p̄ p90':>8}{'p_eff>θCB':>8}{'ŝ=S3':>7}{'오탐p_eff':>9}  오탐 조치")
    print("\n" + header)
    results = {}
    conditions = {
        "A 10-15 학습": (X[trainIndex], y[trainIndex], g[trainIndex]),
        "B + normal 셀": (np.concatenate([X[trainIndex], Xn]), np.concatenate([y[trainIndex], yn]),
                         np.concatenate([g[trainIndex], gn])),
        "C + 새 S3": (np.concatenate([X[trainIndex], Xs]), np.concatenate([y[trainIndex], ys]),
                      np.concatenate([g[trainIndex], gs])),
        "D + 둘 다": (np.concatenate([X[trainIndex], Xn, Xs]), np.concatenate([y[trainIndex], yn, ys]),
                     np.concatenate([g[trainIndex], gn, gs])),
    }
    models = {}
    for name, (features, labels, groups) in conditions.items():
        models[name] = fit(features, labels, groups)
        results[name] = evaluate(models[name], X[testIndex], y[testIndex], nodes[testIndex], current)
        row(name, results[name])

    print("\n━━━ 모델 A 를 추가 표본 자체에 — 그 표본이 오탐을 내는가 ━━━")
    results["A → normal 셀 표본"] = evaluate(models["A 10-15 학습"], Xn, yn, nn, current)
    row("A → normal 셀 표본", results["A → normal 셀 표본"])
    for source in sorted(set(sn)):
        mask = sn == source
        key = f"   └ {source}"
        results[key] = evaluate(models["A 10-15 학습"], Xn[mask], yn[mask], nn[mask], current)
        row(key, results[key])
    results["A → 새 S3 표본"] = evaluate(models["A 10-15 학습"], Xs, ys, ns, current)
    row("A → 새 S3 표본", results["A → 새 S3 표본"])

    print("\n━━━ 엔진만 10-15 것으로 — 커밋된 0.285 의 출처 ━━━")
    results["A 모델 + 10-15 엔진"] = evaluate(models["A 10-15 학습"], X[testIndex], y[testIndex],
                                           nodes[testIndex], legacyProfiles())
    row("A 모델 + 10-15 엔진", results["A 모델 + 10-15 엔진"])

    labelsBySource = collections.Counter((s, l) for s, l in zip(sn, yn))
    print("\nnormal 셀의 라벨:", dict(labelsBySource))
    labelsS3 = collections.Counter(ys)
    print("새 S3 의 라벨:", dict(labelsS3), "| 10-15 S3 에피소드의 라벨 비율 참고:",
          dict(collections.Counter(y[src == "s3/"])))
    out = pathlib.Path(__file__).with_name("diagnoseEndToEnd.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def reproduceCommitted():
    """커밋된 endToEnd.json 이 어디서 왔는지 — 10-15 코퍼스 · 10-15 엔진 · Δ=60초 로 다시 만든다.

    파일은 Δ 표(proposal §Δ)를 만들 때 **마지막으로 돈 Δ=60초 실행**의 것이다(기본값 10초가
    아니다). 지금 코드로 이 조건을 다시 돌려 파일 전체가 같은지 본다. 같으면 라벨·모델·보정
    코드는 그 뒤 바뀌지 않았고, 달라진 것은 엔진과 코퍼스뿐이다. 커밋된 파일은 덮어쓰지 않는다.
    """
    added = ADDED_NORMAL | ADDED_S3

    def loadAllEpisodes(chainDir, arguments):
        episodes = []
        for path in sorted((chainDir / "results").glob("*.json")):
            if not (path.name.startswith("episodes-") or path.name.startswith("remediation-")):
                continue
            if path.name in added:
                continue
            for record in json.loads(path.read_text(encoding="utf-8")):
                if record.get("injectedAt") is None or record.get("backend", "compose") != "compose":
                    continue
                episodes.append(record)
        built = []
        for episode in episodes:
            try:
                built.append(buildDataset.buildEpisodeSamples(episode, chainDir, arguments))
            except FileNotFoundError:
                continue
        return built

    compareTiers.loadAllEpisodes = loadAllEpisodes
    legacy = legacyProfiles()
    endToEnd.defaultProfiles = lambda: legacy
    target = ROOT / "code" / "model" / "endToEnd.json"
    saved = target.read_bytes()
    argv = sys.argv
    try:
        sys.argv = ["endToEnd.py", "--horizonSeconds", "60"]
        endToEnd.main()
        produced = target.read_bytes()
    finally:
        sys.argv = argv
        target.write_bytes(saved)
    same = json.loads(produced) == json.loads(saved)
    print(f"\n커밋된 endToEnd.json 과 전체 동일: {same}")
    return same


if __name__ == "__main__":
    if "--reproduce" in sys.argv:
        raise SystemExit(0 if reproduceCommitted() else 1)
    main()
