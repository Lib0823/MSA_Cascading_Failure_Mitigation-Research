package com.research.cascade.app;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import java.io.IOException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * 모든 노드에 동일하게 적용되는 장애 주입 지점.
 *
 * <p>단일 이미지 구성의 실질적 이득이 여기서 나온다 — 서비스마다 언어와 코드가 다르면
 * 장애 주입을 서비스마다 구현해야 하지만, 같은 이미지를 배포하면 <b>필터 하나가 전
 * 서비스에 적용된다</b>(docs/proposal.md §4-1).
 *
 * <p>주입은 워크로드보다 <b>바깥</b>에 있다. 이것이 S3(다운스트림 지연)와
 * S1(DB 커넥션 고갈)을 기전상 분리한다.
 * <ul>
 *   <li><b>S3</b> — 여기서 지연을 넣는다. 노드의 응답은 느려지지만 커넥션 풀은
 *       건드리지 않으므로 {@code hikaricp_connections_acquire} 가 정상이다.</li>
 *   <li><b>S1</b> — 주입 지점이 DB 쪽이다. 노드 응답이 느려지는 것은 같지만
 *       풀 획득 대기가 함께 오른다.</li>
 * </ul>
 * 두 경우 모두 <b>상류에서 보면 증상이 같다</b>(하류가 느려짐). 이 동형성이 H5b 의
 * 전제이고, 구분 가능한 지표를 찾는 것이 H7 이다(§4-4).
 */
@Component
@Order(1)
public class FaultInjectionFilter implements Filter {

    private final FaultState faultState;
    private final String serviceName;

    public FaultInjectionFilter(
            FaultState faultState, @Value("${app.serviceName}") String serviceName) {
        this.faultState = faultState;
        this.serviceName = serviceName;
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        long delayMillis = faultState.delayMillis();
        if (delayMillis > 0) {
            try {
                Thread.sleep(delayMillis);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new ServletException(serviceName + " fault injection interrupted", exception);
            }
        }
        chain.doFilter(request, response);
    }
}
