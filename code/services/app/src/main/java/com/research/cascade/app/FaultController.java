package com.research.cascade.app;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 에피소드 중간에 장애를 켜고 끄는 제어 엔드포인트.
 *
 * <p>⚠️ <b>실험 전용이다.</b> 이 엔드포인트가 열려 있다는 사실 자체는 실험 결과에
 * 영향을 주지 않는다 — 호출되지 않는 동안 필터는 상수 하나를 읽을 뿐이다. 다만
 * {@code /work} 와 달리 지표 집계 대상이 아니어야 하므로 경로를 분리해 둔다.
 */
@RestController
public class FaultController {

    private final FaultState faultState;
    private final String serviceName;

    public FaultController(FaultState faultState, @Value("${app.serviceName}") String serviceName) {
        this.faultState = faultState;
        this.serviceName = serviceName;
    }

    @PostMapping("/fault")
    public Map<String, Object> injectFault(@RequestParam long delayMillis) {
        faultState.setDelayMillis(delayMillis);
        return currentState();
    }

    @GetMapping("/fault")
    public Map<String, Object> currentState() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("serviceName", serviceName);
        body.put("delayMillis", faultState.delayMillis());
        return body;
    }
}
