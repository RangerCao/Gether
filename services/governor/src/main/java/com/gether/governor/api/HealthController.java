package com.gether.governor.api;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {

    @GetMapping({"/", "/api/health"})
    public Map<String, Object> root() {
        return Map.of(
                "service", "gether-governor",
                "status", "ok",
                "version", "0.1.0"
        );
    }
}
