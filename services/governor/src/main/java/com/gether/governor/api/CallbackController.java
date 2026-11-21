package com.gether.governor.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.gether.governor.callback.HmacVerifier;
import com.gether.governor.service.TaskService;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/internal/callbacks")
public class CallbackController {

    private final TaskService service;
    private final HmacVerifier verifier;
    private final ObjectMapper mapper;

    public CallbackController(TaskService service, HmacVerifier verifier, ObjectMapper mapper) {
        this.service = service;
        this.verifier = verifier;
        this.mapper = mapper;
    }

    public record CallbackRequest(String event, long seq, JsonNode payload) {
    }

    @PostMapping("/{taskId}")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public void callback(
            @PathVariable UUID taskId,
            @RequestHeader(value = "X-Timestamp", required = false) String timestamp,
            @RequestHeader(value = "X-Signature", required = false) String signature,
            @RequestBody String rawBody
    ) {
        if (!verifier.verify(timestamp, rawBody, signature)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid callback signature");
        }
        try {
            CallbackRequest request = mapper.readValue(rawBody, CallbackRequest.class);
            if (request.event() == null || request.event().isBlank()) {
                throw new IllegalArgumentException("event required");
            }
            service.applyCallback(taskId, request.event(), request.seq(), request.payload());
        } catch (ResponseStatusException exc) {
            throw exc;
        } catch (Exception exc) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, exc.getMessage());
        }
    }
}
