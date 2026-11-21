package com.gether.governor.api;

import com.gether.governor.executor.ExecutorUnavailableException;
import com.gether.governor.service.QuotaExceededException;
import com.gether.governor.service.TaskService;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(QuotaExceededException.class)
    public ResponseEntity<Map<String, Object>> quota(QuotaExceededException exc) {
        return error(HttpStatus.TOO_MANY_REQUESTS, "QUOTA_EXCEEDED", exc.getMessage());
    }

    @ExceptionHandler(ExecutorUnavailableException.class)
    public ResponseEntity<Map<String, Object>> executor(ExecutorUnavailableException exc) {
        return error(HttpStatus.SERVICE_UNAVAILABLE, "EXECUTOR_UNAVAILABLE", exc.getMessage());
    }

    @ExceptionHandler(TaskService.TaskNotFoundException.class)
    public ResponseEntity<Map<String, Object>> notFound(TaskService.TaskNotFoundException exc) {
        return error(HttpStatus.NOT_FOUND, "TASK_NOT_FOUND", exc.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> validation(MethodArgumentNotValidException exc) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_INPUT", "请求参数不合法");
    }

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> status(ResponseStatusException exc) {
        String code = exc.getStatusCode().value() == 404 ? "NOT_FOUND" : "REQUEST_FAILED";
        return error(HttpStatus.valueOf(exc.getStatusCode().value()), code, exc.getReason());
    }

    private ResponseEntity<Map<String, Object>> error(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(Map.of(
                "detail", Map.of("code", code, "message", message == null ? code : message)
        ));
    }
}
