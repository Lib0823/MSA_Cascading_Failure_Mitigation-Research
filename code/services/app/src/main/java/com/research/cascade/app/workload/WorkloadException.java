package com.research.cascade.app.workload;

/** 워크로드 수행이 포화로 실패했음을 나타낸다. 애플리케이션 버그와 구분하기 위한 타입이다. */
public class WorkloadException extends RuntimeException {

    public WorkloadException(String message, Throwable cause) {
        super(message, cause);
    }
}
