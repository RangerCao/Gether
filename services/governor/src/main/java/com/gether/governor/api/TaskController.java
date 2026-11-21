package com.gether.governor.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.gether.governor.domain.TaskSummary;
import com.gether.governor.domain.TaskView;
import com.gether.governor.executor.ExecutorClient;
import com.gether.governor.security.UserPrincipal;
import com.gether.governor.service.TaskService;
import com.gether.governor.sse.SseHub;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/tasks")
public class TaskController {

    private final TaskService service;
    private final ExecutorClient executor;
    private final SseHub sseHub;

    public TaskController(TaskService service, ExecutorClient executor, SseHub sseHub) {
        this.service = service;
        this.executor = executor;
        this.sseHub = sseHub;
    }

    public record CreateTaskRequest(
            @NotEmpty List<@NotBlank String> videos,
            String question
    ) {
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public TaskView create(
            @Valid @RequestBody CreateTaskRequest request,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        return service.create(request.videos(), request.question(), principal.userId());
    }

    @GetMapping
    public List<TaskSummary> list(
            @RequestParam(defaultValue = "12") int limit,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        return service.list(principal.userId(), limit);
    }

    @GetMapping("/{taskId}")
    public TaskView get(
            @PathVariable UUID taskId,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        try {
            return service.requireAccess(taskId, principal.userId());
        } catch (TaskService.TaskNotFoundException exc) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, exc.getMessage());
        }
    }

    @GetMapping(value = "/{taskId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter events(
            @PathVariable UUID taskId,
            @RequestHeader(value = "Last-Event-ID", required = false) Long lastEventId,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        try {
            service.requireAccess(taskId, principal.userId());
        } catch (TaskService.TaskNotFoundException exc) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, exc.getMessage());
        }
        return sseHub.subscribe(taskId, lastEventId);
    }

    @PostMapping("/{taskId}/parse")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public void parse(
            @PathVariable UUID taskId,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        service.requireAccess(taskId, principal.userId());
        executor.triggerParse(taskId);
    }

    @GetMapping("/{taskId}/report")
    public JsonNode report(
            @PathVariable UUID taskId,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        service.requireAccess(taskId, principal.userId());
        return executor.report(taskId);
    }

    @PostMapping("/{taskId}/plan")
    public JsonNode plan(
            @PathVariable UUID taskId,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        service.requireAccess(taskId, principal.userId());
        return executor.plan(taskId);
    }

    @PostMapping("/{taskId}/resume")
    public JsonNode resume(
            @PathVariable UUID taskId,
            @RequestBody JsonNode body,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal
    ) {
        service.requireAccess(taskId, principal.userId());
        return executor.resume(taskId, body);
    }

    @PostMapping(value = "/{taskId}/resume/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public void resumeStream(
            @PathVariable UUID taskId,
            @RequestBody JsonNode body,
            @org.springframework.security.core.annotation.AuthenticationPrincipal UserPrincipal principal,
            HttpServletResponse response
    ) throws java.io.IOException {
        service.requireAccess(taskId, principal.userId());
        response.setContentType(MediaType.TEXT_EVENT_STREAM_VALUE);
        response.setCharacterEncoding("UTF-8");
        response.setHeader("Cache-Control", "no-cache");
        response.setHeader("X-Accel-Buffering", "no");
        executor.streamResume(taskId, body, response.getOutputStream());
        response.flushBuffer();
    }
}
