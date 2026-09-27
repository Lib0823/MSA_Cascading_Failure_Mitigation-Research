package com.research.cascade.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 단일 이미지 + 프로파일 구성의 진입점.
 *
 * <p>6~7개 서비스를 각각 개발하지 않고 이 애플리케이션 하나를 {@code SERVICE_NAME},
 * {@code DOWNSTREAM}, {@code WORKLOAD} 환경변수로 구분해 여러 번 배포한다. 서비스마다
 * 코드가 다르면 관측된 성능 차이가 코드 차이에서 온 것인지 위상에서 온 것인지 분리되지
 * 않으므로, 동일 이미지 배포는 구현 비용뿐 아니라 실험 통제상으로도 요구사항이다.
 * (docs/proposal.md §4-1)
 */
@SpringBootApplication
public class CascadeAppApplication {

    public static void main(String[] args) {
        SpringApplication.run(CascadeAppApplication.class, args);
    }
}
