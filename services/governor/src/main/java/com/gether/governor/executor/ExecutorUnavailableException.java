package com.gether.governor.executor;

public class ExecutorUnavailableException extends RuntimeException {
    public ExecutorUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
