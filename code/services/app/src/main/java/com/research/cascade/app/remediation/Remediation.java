package com.research.cascade.app.remediation;

/**
 * 조치 종류. <b>상호 배타적이다</b> — 한 번에 하나만 활성화한다.
 *
 * <p>조합 효과는 본 연구의 범위가 아니며, Policy Engine 도 시점당 조치 하나를
 * 선택하므로 설계와 정합한다(docs/proposal.md §2-C).
 *
 * <p>Scale-out 과 Brownout 이 여기 없는 이유는 적용 지점이 다르기 때문이다 —
 * Scale-out 은 K8s 레플리카 증가라 앱이 할 수 없고, Brownout 은 LLM 노드의
 * 요청 파라미터다. 둘 다 실험 데스크톱이 있어야 한다.
 */
public enum Remediation {
    /** 무조치 — `mₐ` 를 재는 기준선. */
    NONE,
    /** 동시 진입 제한. S1(커넥션 고갈)의 기대 정답. */
    BULKHEAD,
    /** 호출 차단. S3(다운스트림 지연)의 기대 정답이며 S1 에서는 역효과가 예상된다. */
    CIRCUIT_BREAKER,
}
