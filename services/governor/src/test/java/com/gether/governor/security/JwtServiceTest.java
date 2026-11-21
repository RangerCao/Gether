package com.gether.governor.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.gether.governor.config.GetherProperties;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class JwtServiceTest {

    private final GetherProperties properties = new GetherProperties(
            new GetherProperties.Executor("http://localhost:8000", 3000, 600000),
            new GetherProperties.Sse(15, 200),
            new GetherProperties.Callback("secret", 300),
            new GetherProperties.Auth("test-secret")
    );

    @Test
    void issuesAndParsesToken() throws Exception {
        JwtService service = new JwtService(properties);
        UUID user = UUID.randomUUID();
        assertThat(service.parse(service.issue(user))).isEqualTo(user);
    }

    @Test
    void rejectsTamperedToken() throws Exception {
        JwtService service = new JwtService(properties);
        String token = service.issue(UUID.randomUUID());
        assertThatThrownBy(() -> service.parse(token + "x")).isInstanceOf(Exception.class);
    }
}
