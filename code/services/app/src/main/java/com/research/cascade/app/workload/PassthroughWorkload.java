package com.research.cascade.app.workload;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * 하류를 호출하기만 하는 노드. 의존 사슬의 깊이를 만드는 것이 유일한 역할이다.
 *
 * <p>{@code app.passthrough.extraDelayMillis} 를 올리면 이 노드 자신이 느려지므로
 * S3(다운스트림 지연) 주입 지점이 된다. 게이트 ①.5 에서는 쓰지 않지만, S1 과 S3 가
 * 증상 동형이어야 한다는 요구(H5b) 때문에 같은 이미지 안에 있어야 한다.
 */
@Component
@Profile("passthrough")
public class PassthroughWorkload implements Workload {

    private final HttpClient httpClient;
    private final URI downstreamUri;
    private final long extraDelayMillis;
    private final Duration requestTimeout;

    public PassthroughWorkload(
            @Value("${app.downstream}") String downstream,
            @Value("${app.passthrough.extraDelayMillis}") long extraDelayMillis,
            @Value("${app.passthrough.timeoutMillis}") long timeoutMillis) {
        this.downstreamUri = URI.create(downstream + "/work");
        this.extraDelayMillis = extraDelayMillis;
        this.requestTimeout = Duration.ofMillis(timeoutMillis);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();
    }

    @Override
    public String name() {
        return "passthrough";
    }

    @Override
    public Map<String, Object> execute() {
        try {
            if (extraDelayMillis > 0) {
                Thread.sleep(extraDelayMillis);
            }
            HttpRequest request = HttpRequest.newBuilder(downstreamUri)
                    .timeout(requestTimeout)
                    .GET()
                    .build();
            HttpResponse<String> response =
                    httpClient.send(request, HttpResponse.BodyHandlers.ofString());

            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("downstream", downstreamUri.toString());
            detail.put("downstreamStatus", response.statusCode());
            detail.put("extraDelayMillis", extraDelayMillis);
            if (response.statusCode() >= 500) {
                throw new WorkloadException(
                        "downstream returned " + response.statusCode(), null);
            }
            return detail;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new WorkloadException("passthrough interrupted", exception);
        } catch (IOException exception) {
            throw new WorkloadException("downstream call failed", exception);
        }
    }
}
