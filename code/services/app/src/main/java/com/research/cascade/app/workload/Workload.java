package com.research.cascade.app.workload;

import java.util.Map;

/**
 * 노드의 병목 유형을 결정하는 단 하나의 확장점.
 *
 * <p>구현은 {@code WORKLOAD} 환경변수(= Spring 프로파일)로 선택된다. 노드별로 달라야
 * 하는 것은 병목 유형이지 코드베이스가 아니므로, 서비스 간 차이는 전부 이 인터페이스의
 * 구현 선택과 실제 의존 대상으로 표현한다.
 */
public interface Workload {

    /** 지표·응답에 찍히는 워크로드 이름 (db / passthrough / llm). */
    String name();

    /** 요청 1건의 작업을 수행하고 관측 세부값을 돌려준다. */
    Map<String, Object> execute();
}
