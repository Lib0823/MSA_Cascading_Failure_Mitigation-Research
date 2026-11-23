"""셀 코호트 — 측정 대상 시스템을 바꾸는 구성 요인 (J31·J32).

기본값과 **다른** 요인만 태그로 만든다. 태그는 원장 키·파일명·분석 필터에 **같은
규칙으로** 쓴다. 전부 기본값이면 빈 문자열이라 종전 키·파일명이 그대로 유지된다.

🔴 요인을 늘릴 때는 **여기에만** 추가한다. 키·파일명·필터가 각자 규칙을 가지면 그중
   하나가 빠졌을 때 다른 코호트의 셀이 조용히 섞인다 — J21(정책 코호트)·J23(엔진
   지문)·J24(트레이스 경로)가 전부 그 유형이었다.

`config` 가 없는 셀(J31 이전)은 전 요인이 기본값으로 돌았다 — runRemediation 의
argparse 기본값이 곧 아래 기본값이다.
"""

from __future__ import annotations

# (config 키, 기본값, 태그 접두어). 순서가 태그의 순서다.
FACTORS = (
    ("maxConnections", 30, "mc"),          # DB max_connections (J31 예산 대조)
    ("paymentKeepAlive", 100, "ka"),       # payment Tomcat maxKeepAliveRequests (J32)
    ("paymentHpa", 0, "hpa"),              # payment 에 HPA(A1)를 거는가 (J32)
    ("workers", 32, "w"),
    ("hogSessions", 24, "hog"),
    ("faultDelayMillis", 400, "fd"),
    ("bulkheadMaxConcurrent", 6, "bh"),
    ("scaleOutReplicas", 3, "so"),
    ("warmup", 60.0, "wu"),
    ("normalSeconds", 50.0, "ns"),
    ("faultSeconds", 70.0, "fs"),
)
DEFAULTS = {key: default for key, default, _ in FACTORS}


def cohortTag(config: dict | None) -> str:
    """기본값과 다른 요인만 이어 붙인다. 예: ``{"maxConnections": 70}`` → ``"mc70"``."""
    config = config or {}
    parts = []
    for key, default, prefix in FACTORS:
        value = config.get(key, default)
        if value is None or float(value) == float(default):
            continue
        parts.append(f"{prefix}{float(value):g}")
    return "-".join(parts)


def cellCohort(cell: dict) -> str:
    """원장의 셀 하나의 코호트. 시간 구간은 셀 최상위에도 있으므로 함께 본다."""
    config = dict(cell.get("config") or {})
    for key in ("normalSeconds", "faultSeconds"):
        if key not in config and cell.get(key) is not None:
            config[key] = cell[key]
    return cohortTag(config)
