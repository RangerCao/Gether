package com.gether.governor.domain;

import java.time.OffsetDateTime;
import java.util.UUID;

public record TaskView(
        UUID id,
        UUID userId,
        String status,
        int progress,
        String errorCode,
        String errorMessage,
        long version,
        long evidenceCount,
        long videoCount,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
