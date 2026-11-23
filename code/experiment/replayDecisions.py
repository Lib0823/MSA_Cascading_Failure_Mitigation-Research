"""기록된 결정을 다른 엔진 파라미터로 다시 내려 본다 — 배선이 결정을 바꾸는가 (J34).

배선은 엔진 지문을 바꾸고, 지문이 다르면 집계가 코호트를 가른다([comparePolicies.py]
(comparePolicies.py) `cohortOf`). 종전 지문(`c299de2c4f92`)으로 기록된 A5 17반복·A6 5반복의
결론을 새 엔진에 그대로 쓸 수 있는지는 **따로 보여야 한다.**

셀을 다시 돌리지 않는다. 결정 로그에 남은 **입력**(p̄·u·병목 판정)을 두 엔진에 다시 넣어
**결정 함수만** 비교한다 — 측정 잡음이 끼지 않는다.

검증은 두 단계다.

1. **재현** — 종전 엔진이 기록된 결정을 그대로 내는가. 종전 엔진은 현재 프로파일에서
   바뀐 항목만 되돌려 만들고, **종전 지문 공식으로 다시 계산한 지문이 기록과 같은지**
   확인한다. 재현이 100% 가 아니면 재생 자체가 틀린 것이다(κ·L_pred·입력 누락).
2. **불변** — 새 엔진이 종전 엔진과 같은 결정을 내는가.

κ 와 L_pred 는 로그에 없다. controlLoop 의 규칙(A6 은 κ=0, 나머지 1)과 runBatch 의
기본값(`--horizonSeconds` 30)을 쓴다 — 재현율이 그 가정을 검증한다.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
for folder in ("model", "control", "chain", "calibration"):
    sys.path.insert(0, str(HERE.parent / folder))
sys.path.insert(0, str(HERE))

import policyEngine  # noqa: E402
from policyEngine import HOLD, PolicyEngine  # noqa: E402

# 종전 엔진(c299de2c4f92)에서 **바뀐 항목만** 적는다. 나머지는 현재 프로파일과 같다.
LEGACY_OVERRIDES = {
    ("SCALE_OUT", "service"): {"disruption": 0.5, "leadTimeSeconds": 10.0,
                               "mitigationByBottleneck": {"S1": -0.4, "S2": 0.0, "S3": 0.6}},
}
LEGACY_DIGEST = "c299de2c4f92"
GATED_POLICIES = {"A5": 1.0, "A6": 0.0}   # controlLoop: A6 은 κ=0, 나머지는 기본 κ=1
DEFAULT_HORIZON_SECONDS = 30.0            # runBatch --horizonSeconds 기본값
FLOAT_TOLERANCE = 1e-9


def legacyProfiles() -> list:
    profiles = []
    for profile in policyEngine.defaultProfiles():
        override = LEGACY_OVERRIDES.get((profile.name, profile.nodeType))
        profiles.append(dataclasses.replace(profile, **override) if override else profile)
    return profiles


def legacyDigest(profiles: list) -> str:
    """2026-11-23 이전 runBatch.profileFingerprint 의 공식 — `ℓₐ` 를 보지 않는다."""
    rows = sorted((p.name, p.nodeType, p.disruption, p.reversibility,
                   tuple(sorted(p.mitigationByBottleneck.items()))) for p in profiles)
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:12]


def sameThreshold(computed: float, logged) -> bool:
    """θ 비교. 보류의 θ 는 ∞ 로 기록된다(JSON `Infinity`)."""
    if not isinstance(logged, (int, float)):
        return False
    if computed == float("inf") or logged == float("inf"):
        return computed == logged
    return abs(computed - logged) <= FLOAT_TOLERANCE


def loadCells(ledgerPath: pathlib.Path, digest: str) -> list[dict]:
    cells, seen = [], set()
    for line in ledgerPath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cell = json.loads(line)
        if (cell.get("status") != "done" or cell.get("policy") not in GATED_POLICIES
                or (cell.get("actionProfiles") or {}).get("digest") != digest):
            continue
        # 같은 결정 로그를 가리키는 셀이 둘일 수 있다(지문이 파일명에 들어가기 전, 배선 전후
        # 셀이 같은 경로에 썼다). 파일은 마지막 실행의 것이므로 한 번만 읽는다.
        if cell["decisionPath"] in seen:
            continue
        seen.add(cell["decisionPath"])
        cells.append(cell)
    return cells


def main() -> int:
    parser = argparse.ArgumentParser(description="기록된 결정의 재생 — 배선 전후 결정 비교")
    parser.add_argument("--ledger", default=str(RESULTS_DIR / "ledger.jsonl"))
    parser.add_argument("--digest", default=LEGACY_DIGEST, help="재생할 셀의 기록 지문")
    parser.add_argument("--horizonSeconds", type=float, default=DEFAULT_HORIZON_SECONDS)
    parser.add_argument("--output", help="요약 JSON. 기본은 results/replay-<종전>-to-<새>.json")
    arguments = parser.parse_args()

    import runBatch   # noqa: PLC0415  (새 지문은 runBatch 의 현재 공식으로 계산한다)
    newDigest = runBatch.profileFingerprint()["digest"]
    oldProfiles = legacyProfiles()
    rebuilt = legacyDigest(oldProfiles)
    print(f"종전 엔진: 지문 재계산 {rebuilt} (기록 {arguments.digest}) "
          f"{'✅' if rebuilt == arguments.digest else '❌ 불일치 — 재생 불가'}")
    print(f"새 엔진: 지문 {newDigest}")
    if rebuilt != arguments.digest:
        return 1

    cells = loadCells(pathlib.Path(arguments.ledger), arguments.digest)
    tally: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    mismatches: list[dict] = []
    actions = collections.Counter()
    for cell in cells:
        policy = cell["policy"]
        kappa = GATED_POLICIES[policy]
        oldEngine = PolicyEngine(oldProfiles, kappa=kappa)
        newEngine = PolicyEngine(policyEngine.defaultProfiles(), kappa=kappa)
        path = HERE / cell["decisionPath"]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("action") is None or row.get("meanRisk") is None:
                continue
            arguments_ = dict(node=row["node"], nodeType="service", bottleneck=row["bottleneck"],
                              meanRisk=row["meanRisk"], uncertainty=row["uncertainty"],
                              predictionLeadSeconds=arguments.horizonSeconds)
            old = oldEngine.decide(**arguments_)
            new = newEngine.decide(**arguments_)
            counts = tally[policy]
            counts["rows"] += 1
            # 조치가 같고 θ 도 같아야 재현이다 — θ 까지 맞아야 입력·κ·프로파일이 모두 맞은 것이다.
            reproduced = old.action == row["action"] and sameThreshold(old.threshold,
                                                                       row.get("threshold"))
            counts["reproduced"] += reproduced
            counts["invariant"] += new.action == old.action
            actions[(policy, old.action)] += 1
            # 후보 목록은 달라질 수 있다(Scale-out 의 θ·비용). 결정과 따로 센다.
            oldScale = next((c for c in old.candidates if c["action"] == "SCALE_OUT"), None)
            newScale = next((c for c in new.candidates if c["action"] == "SCALE_OUT"), None)
            counts["scaleOutCandidateChanged"] += oldScale != newScale
            if not reproduced or new.action != old.action:
                mismatches.append({"file": cell["decisionPath"], "tick": row.get("tick"),
                                   "node": row["node"], "logged": row["action"],
                                   "old": old.action, "new": new.action,
                                   "bottleneck": row["bottleneck"]})

    print(f"\n셀 {len(cells)}개 (결정 로그 기준, 중복 경로 제외) / L_pred {arguments.horizonSeconds:.0f}초")
    print(f"{'정책':<6}{'결정':>7}{'재현':>9}{'불변':>9}{'Scale-out 후보 변화':>20}")
    summary = {}
    for policy, counts in sorted(tally.items()):
        rows = counts["rows"]
        print(f"{policy:<6}{rows:>7}{counts['reproduced'] / rows:>9.1%}{counts['invariant'] / rows:>9.1%}"
              f"{counts['scaleOutCandidateChanged']:>20}")
        summary[policy] = {k: counts[k] for k in ("rows", "reproduced", "invariant",
                                                    "scaleOutCandidateChanged")}
    print("\n선택된 조치(종전 엔진): " + ", ".join(
        f"{policy} {action} {n}" for (policy, action), n in sorted(actions.items())))
    if mismatches:
        print(f"\n❌ 불일치 {len(mismatches)}건 — 처음 5건:")
        for item in mismatches[:5]:
            print("   ", item)
    else:
        print("\n✅ 전 결정이 재현되고, 새 엔진에서도 같다 — 종전 지문의 결론을 새 엔진에 그대로 쓸 수 있다")

    output = pathlib.Path(arguments.output) if arguments.output else (
        RESULTS_DIR / f"replay-{arguments.digest}-to-{newDigest}.json")
    output.write_text(json.dumps({
        "legacyDigest": arguments.digest, "newDigest": newDigest,
        "horizonSeconds": arguments.horizonSeconds, "cells": len(cells),
        "byPolicy": summary, "mismatches": mismatches,
        "legacyOverrides": {f"{k[0]}/{k[1]}": v for k, v in LEGACY_OVERRIDES.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {output}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
