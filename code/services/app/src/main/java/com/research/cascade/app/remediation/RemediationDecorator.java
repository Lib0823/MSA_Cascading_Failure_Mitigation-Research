package com.research.cascade.app.remediation;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 하류 호출을 조치로 감싼다. <b>이 클래스의 순서가 실험 결과를 좌우한다.</b>
 *
 * <p>데코레이터 순서는 Resilience4j 규약을 따른다 — 바깥에서 안으로
 * {@code Retry → CircuitBreaker → Bulkhead} 다.
 *
 * <pre>
 *   Retry( CircuitBreaker( Bulkhead( 하류 호출 ) ) )
 * </pre>
 *
 * <p><b>재시도는 모든 조건에서 동일하게 고정한다.</b> Retry 가 가장 바깥이므로
 * 재시도 설정이 통제되지 않으면 S1 의 재시도 증폭({@code m_CB < 0})이 재현되지
 * 않거나 과대 재현된다(docs/proposal.md §2-C). 따라서 Retry 는 조치가 아니라
 * <b>실험 환경의 상수</b>이며 토글 대상이 아니다.
 *
 * <p><b>Bulkhead 는 거절하지 않고 기다리게 한다.</b> {@code maxWaitDuration} 을 0 으로
 * 두면 즉시 거절(Shedding)이 되는데, §2-C 는 *"풀 대기를 줄이려면 입구에서 거절하는
 * 것보다 동시 진입을 제한하는 것이 기전상 정확하다"* 고 규정한다. 거절로 만들면
 * S1 에서 Bulkhead 가 정답인 이유가 사라진다.
 */
@Component
public class RemediationDecorator {

    private final RemediationState remediationState;
    private final CircuitBreaker circuitBreaker;
    private final Bulkhead bulkhead;
    private final Retry retry;

    public RemediationDecorator(
            RemediationState remediationState,
            @Value("${app.remediation.bulkhead.maxConcurrentCalls}") int maxConcurrentCalls,
            @Value("${app.remediation.bulkhead.maxWaitMillis}") long bulkheadWaitMillis,
            @Value("${app.remediation.circuitBreaker.slowCallMillis}") long slowCallMillis,
            @Value("${app.remediation.retry.maxAttempts}") int maxAttempts,
            @Value("${app.remediation.retry.waitMillis}") long retryWaitMillis) {
        this.remediationState = remediationState;

        this.bulkhead = Bulkhead.of("downstream", BulkheadConfig.custom()
                .maxConcurrentCalls(maxConcurrentCalls)
                .maxWaitDuration(Duration.ofMillis(bulkheadWaitMillis))
                .build());

        // S1·S3 는 둘 다 "느려짐"으로 나타나므로 실패율이 아니라 **느린 호출 비율**이
        // 주된 개방 조건이다. 실패율만 보면 타임아웃 전까지 열리지 않는다.
        this.circuitBreaker = CircuitBreaker.of("downstream", CircuitBreakerConfig.custom()
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(20)
                .minimumNumberOfCalls(10)
                .slowCallDurationThreshold(Duration.ofMillis(slowCallMillis))
                .slowCallRateThreshold(50.0f)
                .failureRateThreshold(50.0f)
                .waitDurationInOpenState(Duration.ofSeconds(5))
                .permittedNumberOfCallsInHalfOpenState(5)
                .automaticTransitionFromOpenToHalfOpenEnabled(true)
                .build());

        // 지터를 넣는다. 고정 백오프면 재시도가 같은 시점에 몰려 증폭이 과대 재현된다.
        this.retry = Retry.of("downstream", RetryConfig.custom()
                .maxAttempts(maxAttempts)
                .intervalFunction(IntervalFunction.ofExponentialRandomBackoff(
                        Duration.ofMillis(retryWaitMillis), 2.0, 0.5))
                .build());
    }

    /** 활성 조치에 맞춰 감싼 호출을 실행한다. */
    public <T> T execute(Supplier<T> downstreamCall) {
        Supplier<T> decorated = downstreamCall;

        // 안쪽부터 쌓는다. Bulkhead 가 가장 안, Retry 가 가장 바깥이다.
        Remediation active = remediationState.active();
        if (active == Remediation.BULKHEAD) {
            decorated = Bulkhead.decorateSupplier(bulkhead, decorated);
        }
        if (active == Remediation.CIRCUIT_BREAKER) {
            decorated = CircuitBreaker.decorateSupplier(circuitBreaker, decorated);
        }
        // Retry 는 조치가 아니라 실험 상수다. 항상 걸린다.
        decorated = Retry.decorateSupplier(retry, decorated);

        return decorated.get();
    }

    /** 조치가 실제로 발동했는지 확인하기 위한 내부 지표. 라벨이 아니라 진단용이다. */
    public Map<String, Object> snapshot() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("remediation", remediationState.active().name());
        body.put("circuitBreakerState", circuitBreaker.getState().name());
        body.put("circuitBreakerSlowCallRate", circuitBreaker.getMetrics().getSlowCallRate());
        body.put("circuitBreakerNotPermitted",
                circuitBreaker.getMetrics().getNumberOfNotPermittedCalls());
        body.put("bulkheadAvailableCalls", bulkhead.getMetrics().getAvailableConcurrentCalls());
        body.put("retryTotalAttempts",
                retry.getMetrics().getNumberOfTotalCalls());
        return body;
    }
}
