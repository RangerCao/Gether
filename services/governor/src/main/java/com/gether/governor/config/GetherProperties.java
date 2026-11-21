package com.gether.governor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "gether")
public record GetherProperties(
        Executor executor,
        Sse sse,
        Callback callback,
        Auth auth
) {
    public record Executor(String baseUrl, int connectTimeoutMs, int readTimeoutMs) {
    }

    public record Sse(int heartbeatSeconds, int replayBufferSize) {
    }

    public record Callback(String hmacSecret, long maxSkewSeconds) {
    }

    public record Auth(String jwtSecret) {
    }
}
