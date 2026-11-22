package com.gether.governor.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.gether.governor.domain.TaskStatus;
import com.gether.governor.domain.TaskSummary;
import com.gether.governor.domain.TaskView;
import com.gether.governor.executor.ExecutorClient;
import com.gether.governor.repository.TaskRepository;
import com.gether.governor.sse.SseHub;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class TaskService {

    private static final Logger log = LoggerFactory.getLogger(TaskService.class);

    private final TaskRepository repository;
    private final ExecutorClient executor;
    private final SseHub sseHub;
    private final ExecutorService virtualExecutor;

    public TaskService(
            TaskRepository repository,
            ExecutorClient executor,
            SseHub sseHub,
            ExecutorService virtualExecutor
    ) {
        this.repository = repository;
        this.executor = executor;
        this.sseHub = sseHub;
        this.virtualExecutor = virtualExecutor;
    }

    public TaskView create(List<String> videos, String question, UUID userId) {
        ensureQuota(userId);
        UUID taskId = repository.create(videos, question, userId);
        TaskView task = repository.find(taskId).orElseThrow();
        sseHub.publish(taskId, "state", taskPayload(task, "created"));
        virtualExecutor.submit(() -> trigger(taskId));
        return task;
    }

    /** 单视频任务在执行层创建（需要先归一化 + 补 B 站元数据），这里只做配额校验。 */
    public void ensureQuota(UUID userId) {
        if (userId != null && repository.activeCount(userId) >= 3) {
            throw new QuotaExceededException("同一用户最多同时运行 3 个任务");
        }
    }

    private void trigger(UUID taskId) {
        try {
            executor.triggerParse(taskId);
        } catch (Exception exc) {
            log.warn("executor trigger failed task={}", taskId, exc);
            repository.find(taskId).ifPresent(task -> {
                try {
                    repository.transition(
                            taskId, task.version(), TaskStatus.PENDING,
                            TaskStatus.FAILED, 100, "EXECUTOR_UNAVAILABLE", exc.getMessage()
                    );
                    sseHub.publish(taskId, "error", java.util.Map.of(
                            "code", "EXECUTOR_UNAVAILABLE",
                            "message", "AI 执行层暂不可用"
                    ));
                } catch (Exception inner) {
                    log.warn("mark failed failed task={}", taskId, inner);
                }
            });
        }
    }

    public TaskView get(UUID taskId) {
        return repository.find(taskId)
                .orElseThrow(() -> new TaskNotFoundException(taskId));
    }

    public TaskView requireAccess(UUID taskId, UUID userId) {
        TaskView task = get(taskId);
        if (task.userId() != null && userId != null && !task.userId().equals(userId)) {
            throw new TaskNotFoundException(taskId);
        }
        return task;
    }

    public List<TaskSummary> list(UUID userId, int limit) {
        int bounded = Math.max(1, Math.min(limit, 50));
        return repository.listByUser(userId, bounded);
    }

    public void applyCallback(UUID taskId, String event, long seq, JsonNode payload) {
        TaskView task = get(taskId);
        if (!repository.insertCallback(taskId, event, seq, payload.toString())) {
            log.info("duplicate callback ignored task={} event={} seq={}", taskId, event, seq);
            return;
        }
        switch (event) {
            case "parse_finished", "planning" -> move(task, TaskStatus.PLANNING, 90, null, null);
            case "parse_failed" -> move(task, TaskStatus.FAILED, 100,
                    payload.path("code").asText("PARSE_FAILED"),
                    payload.path("message").asText("解析失败"));
            case "generation_done" -> move(task, TaskStatus.DONE, 100, null, null);
            case "generation_failed" -> move(task, TaskStatus.FAILED, 100,
                    payload.path("code").asText("GENERATION_FAILED"),
                    payload.path("message").asText("研报生成失败"));
            default -> { }
        }
        sseHub.publish(taskId, event, payload);
    }

    private void move(
            TaskView task,
            TaskStatus to,
            int progress,
            String errorCode,
            String errorMessage
    ) {
        // 回调由 Python 并发发出，旧版本号会导致状态迁移静默丢失。
        // 这里每次重新读取最新 version，最多重试 5 次。
        for (int attempt = 0; attempt < 5; attempt++) {
            TaskView latest = repository.find(task.id()).orElse(null);
            if (latest == null) {
                return;
            }
            TaskStatus current = TaskStatus.from(latest.status());
            if (current == to) {
                return;
            }
            if (!current.canTransitionTo(to)) {
                log.debug("callback state transition skipped task={} {} -> {}", latest.id(), current, to);
                return;
            }
            try {
                if (repository.transition(
                        latest.id(), latest.version(), current,
                        to, progress, errorCode, errorMessage
                )) {
                    return;
                }
            } catch (IllegalStateException exc) {
                log.debug("callback state transition skipped task={} {} -> {}", latest.id(), current, to);
                return;
            }
            try {
                Thread.sleep(10L);
            } catch (InterruptedException exc) {
                Thread.currentThread().interrupt();
                return;
            }
        }
        log.warn("callback state transition lost task={} target={}", task.id(), to);
    }

    private java.util.Map<String, Object> taskPayload(TaskView task, String reason) {
        return java.util.Map.of(
                "task_id", task.id().toString(),
                "status", task.status(),
                "progress", task.progress(),
                "evidence_count", task.evidenceCount(),
                "reason", reason
        );
    }

    public static class TaskNotFoundException extends RuntimeException {
        public TaskNotFoundException(UUID id) {
            super("任务不存在：" + id);
        }
    }
}
