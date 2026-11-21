package com.gether.governor.sse;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Component
public class SseHub {

    private final Map<UUID, TaskChannel> channels = new ConcurrentHashMap<>();
    private final ObjectMapper mapper;
    private final int replaySize;

    public SseHub(ObjectMapper mapper, com.gether.governor.config.GetherProperties properties) {
        this.mapper = mapper;
        this.replaySize = Math.max(properties.sse().replayBufferSize(), 10);
    }

    public SseEmitter subscribe(UUID taskId, Long lastEventId) {
        SseEmitter emitter = new SseEmitter(0L);
        TaskChannel channel = channels.computeIfAbsent(taskId, key -> new TaskChannel(replaySize));
        channel.attach(emitter);
        emitter.onCompletion(() -> channel.detach(emitter));
        emitter.onTimeout(() -> channel.detach(emitter));
        emitter.onError(error -> channel.detach(emitter));

        long after = lastEventId == null ? 0 : lastEventId;
        for (SseEvent event : channel.replayAfter(after)) {
            try {
                emitter.send(SseEmitter.event()
                        .id(String.valueOf(event.id()))
                        .name(event.event())
                        .data(event.data()));
            } catch (IOException exc) {
                channel.detach(emitter);
                break;
            }
        }
        try {
            emitter.send(SseEmitter.event().name("connected").data("{}"));
        } catch (IOException exc) {
            channel.detach(emitter);
        }
        return emitter;
    }

    public void publish(UUID taskId, String event, Object payload) {
        TaskChannel channel = channels.computeIfAbsent(taskId, key -> new TaskChannel(replaySize));
        long id = channel.nextId();
        String data;
        try {
            data = mapper.writeValueAsString(payload == null ? Map.of() : payload);
        } catch (JsonProcessingException exc) {
            data = "{\"error\":\"serialize_failed\"}";
        }
        SseEvent sseEvent = new SseEvent(id, event, data, System.currentTimeMillis());
        channel.store(sseEvent);
        for (SseEmitter emitter : channel.emitters()) {
            try {
                emitter.send(SseEmitter.event()
                        .id(String.valueOf(id))
                        .name(event)
                        .data(data));
            } catch (IOException | IllegalStateException exc) {
                channel.detach(emitter);
            }
        }
    }

    @Scheduled(fixedDelayString = "${gether.sse.heartbeat-seconds:15}000")
    public void heartbeat() {
        channels.forEach((taskId, channel) -> {
            for (SseEmitter emitter : channel.emitters()) {
                try {
                    emitter.send(SseEmitter.event().name("heartbeat").data("{}"));
                } catch (IOException | IllegalStateException exc) {
                    channel.detach(emitter);
                }
            }
        });
    }

    private static final class TaskChannel {
        private final List<SseEmitter> emitters = new ArrayList<>();
        private final Deque<SseEvent> replay = new ArrayDeque<>();
        private final AtomicLong sequence = new AtomicLong(0);
        private final int replaySize;

        private TaskChannel(int replaySize) {
            this.replaySize = replaySize;
        }

        synchronized void attach(SseEmitter emitter) {
            emitters.add(emitter);
        }

        synchronized void detach(SseEmitter emitter) {
            emitters.remove(emitter);
        }

        synchronized List<SseEmitter> emitters() {
            return new ArrayList<>(emitters);
        }

        synchronized long nextId() {
            return sequence.incrementAndGet();
        }

        synchronized void store(SseEvent event) {
            replay.addLast(event);
            while (replay.size() > replaySize) {
                replay.removeFirst();
            }
        }

        synchronized List<SseEvent> replayAfter(long id) {
            List<SseEvent> result = new ArrayList<>();
            for (SseEvent event : replay) {
                if (event.id() > id) {
                    result.add(event);
                }
            }
            return result;
        }
    }
}
