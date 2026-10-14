package com.research.cascade.app.remediation;

/**
 * 조치 종류. <b>상호 배타적이다</b> — 한 번에 하나만 활성화한다.
 *
 * <p>조합 효과는 본 연구의 범위가 아니며, Policy Engine 도 시점당 조치 하나를
 * 선택하므로 설계와 정합한다(docs/proposal.md §2-C).
 *
 * <p>Scale-out 만 여기 없다. K8s 레플리카 증가라 앱이 할 수 없고 클러스터가 필요하다.
 *
 * <p><b>Brownout 은 노드 타입에 따라 적용 지점이 갈린다</b>(§2-C). 일반 서비스에서는
 * <b>호출자가 optional 호출을 생략</b>하는 것이고, LLM 노드에서는 <b>서빙 노드 자신의
 * {@code max_tokens} 를 줄이는</b> 것이다. 전자는 GPU 없이 구현되므로 여기에 둔다 —
 * 종전에 "둘 다 데스크톱이 필요하다"고 적었던 것은 그 구분을 놓친 것이다.
 */
public enum Remediation {
    /** 무조치 — `mₐ` 를 재는 기준선. */
    NONE,
    /** 동시 진입 제한. S1(커넥션 고갈)의 기대 정답. */
    BULKHEAD,
    /** 호출 차단. S3(다운스트림 지연)의 기대 정답이며 S1 에서는 역효과가 예상된다. */
    CIRCUIT_BREAKER,
    /**
     * 품질 저하. 일반 서비스에서는 <b>선택 하류 호출을 생략</b>한다.
     *
     * <p>차단(CB)·제한(Bulkhead)과 질적으로 다르다 — <b>요청을 처리하되 결과를 삭감</b>한다.
     * 따라서 가용성 손실이 아니라 품질 손실로 비용이 잡힌다(§2-B {@code Dₐ}).
     */
    BROWNOUT,
}
