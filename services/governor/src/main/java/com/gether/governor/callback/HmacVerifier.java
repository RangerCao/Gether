package com.gether.governor.callback;

import com.gether.governor.config.GetherProperties;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Component;

@Component
public class HmacVerifier {

    private final GetherProperties properties;

    public HmacVerifier(GetherProperties properties) {
        this.properties = properties;
    }

    public boolean verify(String timestamp, String body, String signature) {
        if (timestamp == null || signature == null) {
            return false;
        }
        long sent;
        try {
            sent = Long.parseLong(timestamp);
        } catch (NumberFormatException exc) {
            return false;
        }
        long now = Instant.now().getEpochSecond();
        if (Math.abs(now - sent) > properties.callback().maxSkewSeconds()) {
            return false;
        }
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(
                    properties.callback().hmacSecret().getBytes(StandardCharsets.UTF_8),
                    "HmacSHA256"
            ));
            byte[] expected = mac.doFinal((timestamp + "." + body).getBytes(StandardCharsets.UTF_8));
            byte[] actual = HexFormat.of().parseHex(signature);
            return MessageDigest.isEqual(expected, actual);
        } catch (Exception exc) {
            return false;
        }
    }
}
