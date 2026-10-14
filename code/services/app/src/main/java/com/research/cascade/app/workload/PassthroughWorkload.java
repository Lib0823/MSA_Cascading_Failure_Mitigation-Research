package com.research.cascade.app.workload;

import com.research.cascade.app.remediation.Remediation;
import com.research.cascade.app.remediation.RemediationDecorator;
import com.research.cascade.app.remediation.RemediationState;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * 하류를 호출하는 노드. 의존 사슬의 깊이와 분기를 만든다.
 *
 * <p><b>하류가 여럿일 수 있다.</b> §4-1 의 토폴로지에서 {@code order} 는 셋으로
 * 분기한다 — {@code inventory}(필수), {@code notification}(필수),
 * {@code assistant}(선택). 하나만 받던 종전 구현으로는 이 위상을 표현할 수 없었다.
 *
 * <pre>
 *   gateway → order → inventory → payment → [PostgreSQL]
 *                   ↘ notification
 *                   ↘ assistant (선택 호출)
 * </pre>
 *
 * <p><b>필수와 선택을 나누는 이유는 Brownout 때문이다.</b> §2-C 는 일반 서비스의
 * Brownout 을 <i>"optional 호출 생략"</i> 으로 규정한다. 선택 하류를 따로 두어야
 * 그 조치를 구현할 수 있고, 이는 <b>GPU 없이 가능한 네 번째 조치</b>다.
 *
 * <p>S3(다운스트림 지연) 주입은 {@link com.research.cascade.app.FaultInjectionFilter}
 * 가 담당한다. 주입 손잡이를 워크로드마다 두면 노드 타입에 따라 기전이 달라져
 * S1 과의 증상 동형 요구(H5b)가 구현 차이로 깨진다.
 */
@Component
@Profile("passthrough")
public class PassthroughWorkload implements Workload {

    private final HttpClient httpClient;
    private final List<URI> requiredTargets;
    private final List<URI> optionalTargets;
    private final Duration requestTimeout;
    private final RemediationDecorator remediationDecorator;
    private final RemediationState remediationState;

    public PassthroughWorkload(
            @Value("${app.downstream}") String downstream,
            @Value("${app.optionalDownstream:}") String optionalDownstream,
            @Value("${app.passthrough.timeoutMillis}") long timeoutMillis,
            RemediationDecorator remediationDecorator,
            RemediationState remediationState) {
        this.requiredTargets = parseTargets(downstream);
        this.optionalTargets = parseTargets(optionalDownstream);
        this.remediationDecorator = remediationDecorator;
        this.remediationState = remediationState;
        this.requestTimeout = Duration.ofMillis(timeoutMillis);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();
    }

    private static List<URI> parseTargets(String value) {
        if (value == null || value.isBlank()) {
            return List.of();
        }
        List<URI> targets = new ArrayList<>();
        for (String part : Arrays.stream(value.split(",")).map(String::trim).toList()) {
            if (!part.isEmpty()) {
                targets.add(URI.create(part + "/work"));
            }
        }
        return List.copyOf(targets);
    }

    @Override
    public String name() {
        return "passthrough";
    }

    @Override
    public Map<String, Object> execute() {
        // 조치는 **호출자 측**에 건다. 유입 부하를 줄이는 조치는 병목 노드 자신이
        // 아니라 그 노드를 호출하는 쪽에 걸어야 효과가 난다(docs/proposal.md §2-C).
        return remediationDecorator.execute(this::callDownstreams);
    }

    private Map<String, Object> callDownstreams() {
        Map<String, Object> detail = new LinkedHashMap<>();
        List<Map<String, Object>> calls = new ArrayList<>();

        for (URI target : requiredTargets) {
            calls.add(callOne(target, true));
        }
        // Brownout 은 선택 호출을 생략한다 — 요청을 처리하되 결과를 삭감하는 것이
        // 차단(CB)·제한(Bulkhead)과 질적으로 다른 지점이다(§2-C).
        boolean brownout = remediationState.active() == Remediation.BROWNOUT;
        detail.put("brownoutActive", brownout);
        if (!brownout) {
            for (URI target : optionalTargets) {
                calls.add(callOne(target, false));
            }
        }
        detail.put("calls", calls);
        return detail;
    }

    private Map<String, Object> callOne(URI target, boolean required) {
        try {
            HttpRequest request = HttpRequest.newBuilder(target)
                    .timeout(requestTimeout)
                    .GET()
                    .build();
            HttpResponse<String> response =
                    httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (required && response.statusCode() >= 500) {
                throw new WorkloadException(
                        target + " returned " + response.statusCode(), null);
            }
            return Map.of("target", target.toString(), "status", response.statusCode(),
                          "required", required);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new WorkloadException("passthrough interrupted", exception);
        } catch (IOException exception) {
            if (required) {
                throw new WorkloadException("downstream call failed: " + target, exception);
            }
            // 선택 호출의 실패는 전파하지 않는다. 그것이 "선택"의 정의다.
            return Map.of("target", target.toString(), "error", exception.getClass().getSimpleName(),
                          "required", false);
        }
    }
}
