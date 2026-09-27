package com.research.cascade.app.workload;

import java.util.LinkedHashMap;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * S1(DB 커넥션 고갈)의 병목을 만드는 워크로드.
 *
 * <p>병목은 <b>앱이 아니라 DB 쪽에</b> 있어야 한다. 앱의 {@code maximumPoolSize} 를 조이면
 * 인스턴스를 2→4로 늘렸을 때 총 풀 용량이 함께 늘어 대기가 <i>줄어들고</i>, H4 전반부
 * ({@code m_scale-out < 0})가 재현되지 않는다. 따라서 앱 풀은 넉넉하게 두고 PostgreSQL 의
 * {@code max_connections} 와 DB CPU 를 한계로 삼는다. (docs/proposal.md §4-5)
 *
 * <p>요청 1건은 두 가지를 한다.
 * <ul>
 *   <li><b>점유</b> — {@code pg_sleep} 으로 커넥션 보유시간을 늘린다. 인스턴스당 동시
 *       커넥션 요구를 끌어올려 {@code max_connections} 에 닿게 하는 축이다.</li>
 *   <li><b>DB CPU 소모</b> — 해시 연산 쿼리. 커넥션이 남아도 DB 가 포화하면 같은 결과가
 *       되는지를 함께 만들어 두기 위한 축이다.</li>
 * </ul>
 * 두 축을 분리해 둔 이유는 캘리브레이션에서 어느 쪽이 실제로 부호를 만드는지
 * 따로 움직여 볼 수 있어야 하기 때문이다.
 */
@Component
@Profile("db")
public class DbWorkload implements Workload {

    private final JdbcTemplate jdbcTemplate;
    private final DataSource dataSource;
    private final double sleepSeconds;
    private final int burnRows;

    public DbWorkload(
            DataSource dataSource,
            @Value("${app.db.sleepMillis}") long sleepMillis,
            @Value("${app.db.burnRows}") int burnRows) {
        this.dataSource = dataSource;
        this.jdbcTemplate = new JdbcTemplate(dataSource);
        this.sleepSeconds = sleepMillis / 1000.0;
        this.burnRows = burnRows;
    }

    @Override
    public String name() {
        return "db";
    }

    @Override
    public Map<String, Object> execute() {
        try {
            // 두 쿼리를 한 번의 커넥션 획득 안에서 돌려야 점유시간이 의도대로 쌓인다.
            Long matched = jdbcTemplate.queryForObject(
                    "SELECT (SELECT count(*) FROM generate_series(1, ?) AS s"
                            + " WHERE md5(s::text) < '8') + (SELECT 0 FROM pg_sleep(?))",
                    Long.class,
                    burnRows,
                    sleepSeconds);

            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("matchedRows", matched);
            detail.put("sleepSeconds", sleepSeconds);
            detail.put("burnRows", burnRows);
            return detail;
        } catch (RuntimeException exception) {
            // 커넥션 획득 타임아웃과 DB 연결 거부가 모두 여기로 온다. 둘 다 포화 신호다.
            throw new WorkloadException("db workload failed", exception);
        }
    }

    /** 부하기가 조건별로 실제 적용된 풀 설정을 확인할 수 있게 노출한다. */
    public DataSource dataSource() {
        return dataSource;
    }
}
