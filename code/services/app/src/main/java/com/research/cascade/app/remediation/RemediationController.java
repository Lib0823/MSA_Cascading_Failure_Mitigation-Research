package com.research.cascade.app.remediation;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 에피소드 중간에 조치를 거는 제어 엔드포인트. 실험 전용이다. */
@RestController
public class RemediationController {

    private final RemediationState remediationState;
    private final String serviceName;

    public RemediationController(
            RemediationState remediationState, @Value("${app.serviceName}") String serviceName) {
        this.remediationState = remediationState;
        this.serviceName = serviceName;
    }

    @PostMapping("/remediation")
    public Map<String, Object> apply(@RequestParam String action) {
        remediationState.setActive(Remediation.valueOf(action.toUpperCase()));
        return currentState();
    }

    @GetMapping("/remediation")
    public Map<String, Object> currentState() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("serviceName", serviceName);
        body.put("remediation", remediationState.active().name());
        return body;
    }
}
