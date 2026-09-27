# 로컬에 gradle 을 설치하지 않고 빌드하기 위한 멀티스테이지 구성.
# 빌드 JDK 를 21 로 고정해 호스트 JDK 버전과 무관하게 재현된다.

FROM gradle:8.14-jdk21 AS build
WORKDIR /build
COPY settings.gradle build.gradle ./
# 의존성만 먼저 받아 레이어로 굳힌다. 소스만 바뀌면 이 레이어는 재사용된다.
RUN gradle --no-daemon dependencies > /dev/null 2>&1 || true
COPY src ./src
RUN gradle --no-daemon bootJar -x test

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /build/build/libs/*.jar app.jar

# 컨테이너 CPU 상한을 JVM 이 인식하도록 두고(기본 동작), 힙만 고정한다.
# 조건 간 GC 거동이 달라지면 지연 비교가 오염되므로 상·하한을 같게 잡는다.
ENV JAVA_OPTS="-Xms256m -Xmx256m -XX:+UseG1GC"

EXPOSE 8080
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar app.jar"]
