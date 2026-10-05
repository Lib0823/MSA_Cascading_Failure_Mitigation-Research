package com.research.cascade.app;

import java.util.concurrent.atomic.AtomicLong;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 런타임에 바꿀 수 있는 장애 주입 상태.
 *
 * <p>기동 시점에 장애를 켜 두면 안 되는 이유가 있다. §4-2 의 전파성 판정은
 * <b>"상류가 먼저 열화했는가"</b> 라는 <b>순서</b> 문제인데, 처음부터 전부 열화해
 * 있으면 판정할 onset 이 없다. 라벨이 붙으려면 에피소드 중간에 주입 시점이 있어야
 * 하므로 값을 불변으로 두지 않는다.
 *
 * <p>초기값은 {@code FAULT_DELAY_MILLIS} 환경변수로 받되, 이후에는 제어
 * 엔드포인트가 덮어쓴다.
 */
@Component
public class FaultState {

    private final AtomicLong delayMillis;

    public FaultState(@Value("${app.fault.delayMillis:0}") long initialDelayMillis) {
        this.delayMillis = new AtomicLong(initialDelayMillis);
    }

    public long delayMillis() {
        return delayMillis.get();
    }

    public void setDelayMillis(long value) {
        delayMillis.set(Math.max(0, value));
    }
}
