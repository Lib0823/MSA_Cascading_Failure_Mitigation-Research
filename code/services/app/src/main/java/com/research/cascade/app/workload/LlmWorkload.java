package com.research.cascade.app.workload;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Semaphore;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * S2(LLM 노드 포화)의 자리를 잡아 두는 워크로드. <b>게이트 ①.5 의 검증 대상이 아니다.</b>
 *
 * <p>S2 의 본질은 "GPU 가 단일이라 레플리카를 늘려도 처리량이 늘지 않는다"는 것이다.
 * 여기서는 그 성질만 {@link Semaphore} 로 흉내 낸다 — 동시 실행 수가 1 로 고정되므로
 * 인스턴스를 늘려도 노드 전체 처리량이 오르지 않는다. 실제 추론 서버로 교체할 때
 * 바뀌는 것은 이 클래스 하나뿐이다.
 *
 * <p>⚠️ 이 구현은 {@code m_scale-out = 0} 을 <i>가정으로 심어 둔</i> 것이므로 그 자체로는
 * H4 후반부의 증거가 되지 않는다. 증거는 실제 GPU 추론 노드에서 얻어야 한다.
 */
@Component
@Profile("llm")
public class LlmWorkload implements Workload {

    private final Semaphore acceleratorLock = new Semaphore(1, true);
    private final long millisPerToken;
    private final int maxTokens;

    public LlmWorkload(
            @Value("${app.llm.millisPerToken}") long millisPerToken,
            @Value("${app.llm.maxTokens}") int maxTokens) {
        this.millisPerToken = millisPerToken;
        this.maxTokens = maxTokens;
    }

    @Override
    public String name() {
        return "llm";
    }

    @Override
    public Map<String, Object> execute() {
        try {
            acceleratorLock.acquire();
            try {
                Thread.sleep(millisPerToken * maxTokens);
            } finally {
                acceleratorLock.release();
            }
            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("maxTokens", maxTokens);
            detail.put("millisPerToken", millisPerToken);
            return detail;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new WorkloadException("llm workload interrupted", exception);
        }
    }
}
