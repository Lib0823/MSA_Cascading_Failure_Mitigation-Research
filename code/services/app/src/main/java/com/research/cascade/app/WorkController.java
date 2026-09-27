package com.research.cascade.app;

import com.research.cascade.app.workload.Workload;
import com.research.cascade.app.workload.WorkloadException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 모든 노드가 공유하는 단일 작업 엔드포인트. 실제로 무엇을 하는지는 주입된
 * {@link Workload} 구현(= {@code WORKLOAD} 프로파일)이 결정한다.
 */
@RestController
public class WorkController {

    private final Workload workload;
    private final String serviceName;

    public WorkController(Workload workload, @Value("${app.serviceName}") String serviceName) {
        this.workload = workload;
        this.serviceName = serviceName;
    }

    @GetMapping("/work")
    public Map<String, Object> work() {
        long startedNanos = System.nanoTime();
        Map<String, Object> detail = workload.execute();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("serviceName", serviceName);
        body.put("workload", workload.name());
        body.put("durationMs", (System.nanoTime() - startedNanos) / 1_000_000.0);
        body.put("detail", detail);
        return body;
    }

    /**
     * 워크로드 실패는 500 이 아니라 503 으로 돌려준다. 커넥션 획득 실패·DB 연결 거부는
     * 애플리케이션 버그가 아니라 <b>측정 대상인 포화 신호</b>이므로, 부하기가 이를
     * 에러율로 집계할 수 있어야 한다.
     */
    @ExceptionHandler(WorkloadException.class)
    public ResponseEntity<Map<String, Object>> handleWorkloadFailure(WorkloadException exception) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("serviceName", serviceName);
        body.put("error", exception.getClass().getSimpleName());
        body.put("message", String.valueOf(exception.getMessage()));
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(body);
    }
}
