package com.gether.governor.executor;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.gether.governor.config.GetherProperties;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.github.resilience4j.retry.annotation.Retry;
import java.time.Duration;
import java.util.UUID;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

@Component
public class ExecutorClient {

    private final RestClient client;
    private final ObjectMapper mapper;
    private final String baseUrl;

    public ExecutorClient(GetherProperties properties, ObjectMapper mapper) {
        this.mapper = mapper;
        this.baseUrl = properties.executor().baseUrl();
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(properties.executor().connectTimeoutMs()));
        factory.setReadTimeout(Duration.ofMillis(properties.executor().readTimeoutMs()));
        this.client = RestClient.builder()
                .baseUrl(properties.executor().baseUrl())
                .requestFactory(factory)
                .build();
    }

    @CircuitBreaker(name = "executor", fallbackMethod = "triggerParseFallback")
    @Retry(name = "executor", fallbackMethod = "triggerParseFallback")
    public void triggerParse(UUID taskId) {
        try {
            client.post()
                    .uri("/internal/tasks/{id}/parse", taskId)
                    .retrieve()
                    .toBodilessEntity();
        } catch (RestClientResponseException exc) {
            if (exc.getStatusCode().value() == 409) {
                return; // 已在解析中，幂等接受
            }
            throw exc;
        }
    }

    private void triggerParseFallback(UUID taskId, Throwable error) {
        throw new ExecutorUnavailableException("AI 执行层暂不可用：" + error.getMessage(), error);
    }

    public boolean healthy() {
        try {
            client.get().uri("/health").retrieve().toBodilessEntity();
            return true;
        } catch (Exception exc) {
            return false;
        }
    }

    public JsonNode status(UUID taskId) {
        return client.get()
                .uri("/internal/tasks/{id}", taskId)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode plan(UUID taskId) {
        return client.post()
                .uri("/internal/tasks/{id}/plan", taskId)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode resume(UUID taskId, JsonNode body) {
        return client.post()
                .uri("/internal/tasks/{id}/resume", taskId)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode report(UUID taskId) {
        return client.get()
                .uri("/internal/tasks/{id}/report", taskId)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode createDiscovery(JsonNode body) {
        return client.post()
                .uri("/internal/discovery/sessions")
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode discovery(String sessionId) {
        return client.get()
                .uri("/internal/discovery/sessions/{id}", sessionId)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode confirmDiscovery(String sessionId, JsonNode body) {
        return client.post()
                .uri("/internal/discovery/sessions/{id}/confirm", sessionId)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }

    public void streamResume(UUID taskId, JsonNode body, OutputStream output) {
        try {
            HttpClient http = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(30))
                    .version(HttpClient.Version.HTTP_1_1)
                    .build();
            String payload = mapper.writeValueAsString(body);
            HttpRequest request = HttpRequest.newBuilder(
                            URI.create(baseUrl + "/internal/tasks/" + taskId + "/resume/stream")
                    )
                    .timeout(Duration.ofMinutes(30))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(payload))
                    .build();
            HttpResponse<InputStream> response = http.send(request, HttpResponse.BodyHandlers.ofInputStream());
            response.body().transferTo(output);
            output.flush();
        } catch (Exception exc) {
            throw new IllegalStateException("executor stream failed", exc);
        }
    }
}
