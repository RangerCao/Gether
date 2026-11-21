package com.gether.governor.executor;

import com.gether.governor.domain.TaskStatus;
import com.gether.governor.domain.TaskView;
import com.gether.governor.repository.TaskRepository;
import com.gether.governor.sse.SseHub;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ExecutorHealthMonitor {

    private static final Logger log = LoggerFactory.getLogger(ExecutorHealthMonitor.class);
    private final ExecutorClient executor;
    private final TaskRepository repository;
    private final SseHub sseHub;
    private final AtomicInteger failures = new AtomicInteger();

    public ExecutorHealthMonitor(ExecutorClient executor, TaskRepository repository, SseHub sseHub) {
        this.executor = executor;
        this.repository = repository;
        this.sseHub = sseHub;
    }

    @Scheduled(
            fixedDelayString = "${gether.executor.health-check-ms:5000}",
            initialDelayString = "${gether.executor.health-check-initial-delay-ms:30000}"
    )
    public void check() {
        if (executor.healthy()) {
            failures.set(0);
            return;
        }
        int count = failures.incrementAndGet();
        if (count < 3) {
            return;
        }
        failures.set(0);
        for (TaskView task : repository.activeTasks(200)) {
            if (!TaskStatus.PARSING.name().equals(task.status())
                    && !TaskStatus.GENERATING.name().equals(task.status())) {
                continue;
            }
            try {
                boolean moved = repository.transition(
                        task.id(), task.version(), TaskStatus.from(task.status()),
                        TaskStatus.FAILED, 100, "EXECUTOR_UNAVAILABLE",
                        "AI 执行层暂不可用，任务已保留，可稍后重试"
                );
                if (moved) {
                    sseHub.publish(task.id(), "error", Map.of(
                            "code", "EXECUTOR_UNAVAILABLE",
                            "message", "AI 执行层暂不可用，任务已进入可重试状态",
                            "retryable", true
                    ));
                    sseHub.publish(task.id(), "state", Map.of(
                            "status", "FAILED",
                            "progress", 100,
                            "error_code", "EXECUTOR_UNAVAILABLE"
                    ));
                }
            } catch (Exception exc) {
                log.warn("mark executor failure failed task={}", task.id(), exc);
            }
        }
    }
}
