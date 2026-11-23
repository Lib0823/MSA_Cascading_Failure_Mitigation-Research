"""Δ 표 — 예측 지평(Δ = L_pred)에 따른 신뢰도 구간·Safety Guard 폭·오탐 (proposal §Δ, J36).

proposal §Δ 의 표는 2026-10-15 에 endToEnd 를 Δ 마다 손으로 돌려 만들었고, 산출물은 마지막
실행(Δ=60초)만 남았다(J35). 그 표는 배선 전 엔진·접기 하나·무효 에피소드가 섞인 코퍼스의
값이다. 여기서는 지금 파이프라인(endToEnd.run — 4접기 out-of-fold, corpus.py 규칙)으로 Δ 마다
돌려 **표 전체를 한 산출물로** 남긴다.

=====================  ===================================================================
열                      뜻
=====================  ===================================================================
신뢰도 중간 구간         p̄ 가 0.1~0.9 인 표본 비율 — 신뢰도로 차등화할 여지(H2)
Safety Guard 폭          κ 0 → 4 에서 보류 비율의 증가(%p)
오탐률 (건수)            정상 라벨 표본에 조치가 나간 비율(κ=1)
**오탐 비용**            정상 라벨 표본당 Dₐ·Rₐ 평균 — CB(0.0002)와 Bulkhead(0.0716)를 같은
                        1건으로 세지 않는다(J26)
=====================  ===================================================================

사용: .venv/bin/python code/model/deltaSweep.py   (Δ 5개 × 4접기, 약 30분)
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import endToEnd  # noqa: E402

DEFAULT_HORIZONS = "10,20,30,45,60"   # 2026-10-15 표와 같은 Δ


def main() -> int:
    parser = endToEnd.buildParser()
    parser.add_argument("--horizons", default=DEFAULT_HORIZONS, help="쉼표로 구분한 Δ(초)")
    arguments = parser.parse_args()

    rows = []
    print(f"{'Δ':>5}{'중간 구간':>10}{'SG 폭':>9}{'오탐률':>9}{'오탐 비용':>11}{'무장애 오탐':>11}"
          f"{'무장애 비용':>11}{'ECE':>8}  접기별 오탐률")
    for horizon in [float(h) for h in arguments.horizons.split(",") if h.strip()]:
        arguments.horizonSeconds = horizon
        with contextlib.redirect_stdout(io.StringIO()):   # endToEnd 의 상세 출력은 Δ 마다 길다
            payload = endToEnd.run(arguments, write=False)
        row = {key: payload[key] for key in (
            "horizonSeconds", "episodes", "samples", "middleConfidenceShare", "holdSwing",
            "falsePositiveRate", "falsePositiveRateByFold", "falsePositiveCost",
            "falsePositiveCostByAction", "falsePositiveByWindow", "expectedCalibrationError")}
        rows.append(row)
        print(f"{horizon:>4.0f}s{row['middleConfidenceShare']:>10.1%}{row['holdSwing']:>+9.1%}"
              f"{row['falsePositiveRate']:>9.1%}{row['falsePositiveCost']:>11.4f}"
              f"{(row['falsePositiveByWindow']['faultFree'] or {}).get('rate', float('nan')):>11.1%}"
              f"{(row['falsePositiveByWindow']['faultFree'] or {}).get('cost', float('nan')):>11.4f}"
              f"{row['expectedCalibrationError']:>8.4f}  "
              + ", ".join(f"{r:.0%}" for r in row["falsePositiveRateByFold"]), flush=True)

    output = pathlib.Path(__file__).resolve().parent / "deltaSweep.json"
    output.write_text(json.dumps({
        "evaluation": f"GroupKFold {arguments.folds}접기 out-of-fold",
        "kappas": arguments.kappas, "ensembleSize": arguments.ensembleSize,
        "calibration": arguments.calibration, "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
