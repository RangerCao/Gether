package com.gether.governor.callback;

import static org.assertj.core.api.Assertions.assertThat;

import com.gether.governor.config.GetherProperties;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;

class HmacVerifierTest {

    private final GetherProperties properties = new GetherProperties(
            new GetherProperties.Executor("http://localhost:8000", 3000, 600000),
            new GetherProperties.Sse(15, 200),
            new GetherProperties.Callback("secret", 300),
            new GetherProperties.Auth("change_me")
    );

    @Test
    void acceptsValidSignature() throws Exception {
        HmacVerifier verifier = new HmacVerifier(properties);
        String timestamp = String.valueOf(Instant.now().getEpochSecond());
        String body = "{\"event\":\"parse_finished\",\"seq\":1,\"payload\":{}}";
        assertThat(verifier.verify(timestamp, body, sign(timestamp, body, "secret"))).isTrue();
    }

    @Test
    void rejectsTamperedBody() throws Exception {
        HmacVerifier verifier = new HmacVerifier(properties);
        String timestamp = String.valueOf(Instant.now().getEpochSecond());
        String body = "{\"event\":\"parse_finished\"}";
        String signature = sign(timestamp, body, "secret");
        assertThat(verifier.verify(timestamp, body + "x", signature)).isFalse();
    }

    @Test
    void rejectsStaleTimestamp() throws Exception {
        HmacVerifier verifier = new HmacVerifier(properties);
        String timestamp = String.valueOf(Instant.now().minusSeconds(1000).getEpochSecond());
        String body = "{}";
        assertThat(verifier.verify(timestamp, body, sign(timestamp, body, "secret"))).isFalse();
    }

    private static String sign(String timestamp, String body, String secret) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return HexFormat.of().formatHex(mac.doFinal((timestamp + "." + body).getBytes(StandardCharsets.UTF_8)));
    }
}
