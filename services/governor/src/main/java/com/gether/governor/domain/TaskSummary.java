package com.gether.governor.domain;

import java.time.OffsetDateTime;
import java.util.UUID;

public record TaskSummary(
        UUID id,
        String status,
        int progress,
        long evidenceCount,
        long videoCount,
        String question,
        String errorCode,
        String errorMessage,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
