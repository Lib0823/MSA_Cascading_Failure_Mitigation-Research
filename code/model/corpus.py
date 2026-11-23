"""모델 코퍼스 — 어떤 기록이 학습·평가 에피소드인가 (J30·J36).

`compareTiers.loadAllEpisodes`(학습)와 `episodeValidity.episodeRecords`(검증)가 **이 규칙
하나**를 쓴다. 두 로더가 규칙을 따로 가지면 검증이 보는 집합과 학습이 보는 집합이 달라진다
— 실제로 K8s 셀 필터를 두 곳에 손으로 맞춰 넣었고(J30), `Dₐ` 측정 셀은 둘 다 놓쳤다(J35).

=====================  ===========================================================
빼는 기록               이유
=====================  ===========================================================
주입 시각이 없는 기록    에피소드가 아니다(사슬 기동 실패 등)
K8s 셀                  다른 환경의 분포다(J30)
**`Dₐ` 측정 셀**        `runRemediation --scenarios normal` 의 셀 — 장애 없이 조치만 강제한다.
                        라벨은 전부 정상인데 조치가 feature 를 바꾼다(Bulkhead 셀 지연 3.3배).
                        학습에 섞이면 정상 표본의 병목 판정이 S1 → S3 로 넘어가 오탐이
                        0.018 → 0.591 이 된다(J35). `runChain` 의 normal **대조군** 에피소드는
                        `remediation` 필드가 없어 남는다
=====================  ===========================================================

무효 에피소드(장애가 느려지지 않아 기준선이 부적격 — feature 가 틀린다)는 여기서 빼지 않는다.
기록만 보고는 알 수 없고 트레이스를 읽어야 하므로 `episodeValidity` 가 판정하고,
`loadAllEpisodes` 가 그 판정으로 뺀다(J36).
"""

from __future__ import annotations

import json
import pathlib

CORPUS_PREFIXES = ("episodes-", "remediation-")


def corpusFiles(chainDir: pathlib.Path) -> list[pathlib.Path]:
    return [path for path in sorted((chainDir / "results").glob("*.json"))
            if path.name.startswith(CORPUS_PREFIXES)]


def exclusionReason(record: dict) -> str | None:
    """코퍼스에서 빼는 이유. 코퍼스 에피소드면 None."""
    if record.get("injectedAt") is None:
        return "주입 시각 없음"
    # 필드가 없는 기존 레코드는 전부 Compose 다.
    if record.get("backend", "compose") != "compose":
        return "K8s 셀"
    if record.get("scenario") == "normal" and "remediation" in record:
        return "Dₐ 측정 셀"
    return None


def corpusRecords(chainDir: pathlib.Path) -> list[dict]:
    """코퍼스 에피소드의 기록. 트레이스가 없는 고아 기록은 호출하는 쪽이 거른다."""
    records = []
    for path in corpusFiles(chainDir):
        for record in json.loads(path.read_text(encoding="utf-8")):
            if exclusionReason(record) is None:
                records.append(record)
    return records
