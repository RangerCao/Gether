package com.gether.governor.api;

import com.gether.governor.security.JwtService;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final JwtService jwtService;

    public AuthController(JwtService jwtService) {
        this.jwtService = jwtService;
    }

    @PostMapping("/token")
    public Map<String, String> token(@RequestBody(required = false) Map<String, String> body) {
        UUID userId;
        try {
            userId = UUID.fromString(body == null ? "" : body.getOrDefault("user_id", ""));
        } catch (Exception exc) {
            userId = UUID.randomUUID();
        }
        return Map.of("user_id", userId.toString(), "token", jwtService.issue(userId));
    }
}
