package com.gether.governor.sse;

public record SseEvent(long id, String event, String data, long timestamp) {
}
