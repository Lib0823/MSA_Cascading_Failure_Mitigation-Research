package com.research.cascade.app.remediation;

import java.util.concurrent.atomic.AtomicReference;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 현재 활성화된 조치. 런타임에 바꾼다.
 *
 * <p>{@code FaultState} 와 같은 이유로 가변이다 — 조치를 기동 시점에 고정하면
 * <b>조치 전후를 한 에피소드 안에서 비교할 수 없고</b>, 조건마다 컨테이너를
 * 다시 띄우는 비용이 실험을 느리게 만든다.
 */
@Component
public class RemediationState {

    private final AtomicReference<Remediation> active;

    public RemediationState(@Value("${app.remediation.initial:NONE}") String initial) {
        this.active = new AtomicReference<>(Remediation.valueOf(initial));
    }

    public Remediation active() {
        return active.get();
    }

    public void setActive(Remediation remediation) {
        active.set(remediation);
    }
}
