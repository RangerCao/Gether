package com.gether.governor.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.gether.governor.executor.ExecutorClient;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/discovery/sessions")
public class DiscoveryController {

    private final ExecutorClient executor;

    public DiscoveryController(ExecutorClient executor) {
        this.executor = executor;
    }

    @PostMapping
    public JsonNode create(@RequestBody JsonNode body) {
        return executor.createDiscovery(body);
    }

    @GetMapping("/{sessionId}")
    public JsonNode get(@PathVariable String sessionId) {
        return executor.discovery(sessionId);
    }

    @PostMapping("/{sessionId}/confirm")
    public JsonNode confirm(@PathVariable String sessionId, @RequestBody JsonNode body) {
        JsonNode result = executor.confirmDiscovery(sessionId, body);
        String taskId = result.path("task_id").asText("");
        if (!taskId.isBlank()) {
            executor.triggerParse(UUID.fromString(taskId));
        }
        return result;
    }
}
