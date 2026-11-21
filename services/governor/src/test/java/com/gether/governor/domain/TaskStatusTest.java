package com.gether.governor.domain;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TaskStatusTest {

    @Test
    void pendingCanStartParsing() {
        assertThat(TaskStatus.PENDING.canTransitionTo(TaskStatus.PARSING)).isTrue();
        assertThat(TaskStatus.PENDING.canTransitionTo(TaskStatus.DONE)).isFalse();
    }

    @Test
    void terminalStatesAreTerminal() {
        assertThat(TaskStatus.DONE.canTransitionTo(TaskStatus.PARSING)).isFalse();
        assertThat(TaskStatus.CANCELLED.canTransitionTo(TaskStatus.PENDING)).isFalse();
    }

    @Test
    void failedCanRetry() {
        assertThat(TaskStatus.FAILED.canTransitionTo(TaskStatus.PENDING)).isTrue();
    }
}
