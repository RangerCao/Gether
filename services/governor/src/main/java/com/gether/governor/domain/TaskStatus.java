package com.gether.governor.domain;

import java.util.Map;
import java.util.Set;

public enum TaskStatus {
    PENDING,
    PARSING,
    PLANNING,
    GENERATING,
    DONE,
    FAILED,
    CANCELLED;

    private static final Map<TaskStatus, Set<TaskStatus>> ALLOWED = Map.of(
            PENDING, Set.of(PARSING, FAILED, CANCELLED),
            PARSING, Set.of(PLANNING, FAILED, CANCELLED),
            PLANNING, Set.of(GENERATING, FAILED, CANCELLED),
            GENERATING, Set.of(DONE, FAILED, CANCELLED),
            FAILED, Set.of(PENDING, CANCELLED),
            DONE, Set.of(),
            CANCELLED, Set.of()
    );

    public boolean canTransitionTo(TaskStatus target) {
        return ALLOWED.getOrDefault(this, Set.of()).contains(target);
    }

    public static TaskStatus from(String value) {
        return TaskStatus.valueOf(value.toUpperCase());
    }
}
