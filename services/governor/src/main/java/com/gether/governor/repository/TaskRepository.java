package com.gether.governor.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.gether.governor.domain.TaskStatus;
import com.gether.governor.domain.TaskSummary;
import com.gether.governor.domain.TaskView;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class TaskRepository {

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public TaskRepository(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    public UUID create(List<String> videos, String question, UUID userId) {
        String input;
        try {
            input = mapper.writeValueAsString(Map.of(
                    "videos", videos,
                    "question", question == null ? "" : question
            ));
        } catch (JsonProcessingException exc) {
            throw new IllegalArgumentException("无法序列化任务输入", exc);
        }
        return jdbc.queryForObject(
                "INSERT INTO tasks (user_id, status, input_json) "
                        + "VALUES (?, 'PENDING', ?::jsonb) RETURNING id",
                UUID.class,
                userId,
                input
        );
    }

    public Optional<TaskView> find(UUID id) {
        List<TaskView> rows = jdbc.query(
                "SELECT t.id, t.user_id, t.status, t.progress, t.error_code, t.error_message, "
                        + "t.version, t.created_at, t.updated_at, "
                        + "(SELECT count(*) FROM evidence_packets e WHERE e.task_id = t.id) AS evidence_count, "
                        + "(SELECT count(*) FROM videos v WHERE v.task_id = t.id) AS video_count "
                        + "FROM tasks t WHERE t.id = ?",
                (rs, rowNum) -> new TaskView(
                        rs.getObject("id", UUID.class),
                        rs.getObject("user_id", UUID.class),
                        rs.getString("status"),
                        rs.getInt("progress"),
                        rs.getString("error_code"),
                        rs.getString("error_message"),
                        rs.getLong("version"),
                        rs.getLong("evidence_count"),
                        rs.getLong("video_count"),
                        rs.getObject("created_at", java.time.OffsetDateTime.class),
                        rs.getObject("updated_at", java.time.OffsetDateTime.class)
                ),
                id
        );
        return rows.stream().findFirst();
    }

    public boolean transition(
            UUID id,
            long expectedVersion,
            TaskStatus from,
            TaskStatus to,
            int progress,
            String errorCode,
            String errorMessage
    ) {
        if (!from.canTransitionTo(to)) {
            throw new IllegalStateException("非法状态迁移：" + from + " -> " + to);
        }
        var updated = jdbc.query(
                "UPDATE tasks SET status = ?, progress = ?, error_code = ?, error_message = ?, "
                        + "version = version + 1, updated_at = now() "
                        + "WHERE id = ? AND status = ? AND version = ? RETURNING 1",
                (rs, rowNum) -> 1,
                to.name(),
                progress,
                errorCode,
                errorMessage,
                id,
                from.name(),
                expectedVersion
        );
        return !updated.isEmpty();
    }

    public int activeCount(UUID userId) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM tasks WHERE user_id = ? "
                        + "AND status IN ('PENDING','PARSING','PLANNING','GENERATING')",
                Integer.class,
                userId
        );
        return count == null ? 0 : count;
    }

    public boolean insertCallback(UUID taskId, String eventType, long seq, String payload) {
        var inserted = jdbc.query(
                "INSERT INTO task_callbacks (task_id, event_type, seq, payload) "
                        + "VALUES (?, ?, ?, ?::jsonb) "
                        + "ON CONFLICT (task_id, event_type, seq) DO NOTHING RETURNING 1",
                (rs, rowNum) -> 1,
                taskId,
                eventType,
                seq,
                payload
        );
        return !inserted.isEmpty();
    }

    public List<TaskSummary> listByUser(UUID userId, int limit) {
        return jdbc.query(
                "SELECT t.id, t.status, t.progress, t.error_code, t.error_message, "
                        + "coalesce(t.input_json->>'question', '') AS question, "
                        + "t.created_at, t.updated_at, "
                        + "(SELECT count(*) FROM evidence_packets e WHERE e.task_id = t.id) AS evidence_count, "
                        + "(SELECT count(*) FROM videos v WHERE v.task_id = t.id) AS video_count "
                        + "FROM tasks t WHERE t.user_id = ? "
                        + "ORDER BY t.created_at DESC LIMIT ?",
                (rs, rowNum) -> new TaskSummary(
                        rs.getObject("id", UUID.class),
                        rs.getString("status"),
                        rs.getInt("progress"),
                        rs.getLong("evidence_count"),
                        rs.getLong("video_count"),
                        rs.getString("question"),
                        rs.getString("error_code"),
                        rs.getString("error_message"),
                        rs.getObject("created_at", java.time.OffsetDateTime.class),
                        rs.getObject("updated_at", java.time.OffsetDateTime.class)
                ),
                userId,
                limit
        );
    }

    public List<TaskView> activeTasks(int limit) {
        return jdbc.query(
                "SELECT t.id, t.user_id, t.status, t.progress, t.error_code, t.error_message, "
                        + "t.version, t.created_at, t.updated_at, "
                        + "(SELECT count(*) FROM evidence_packets e WHERE e.task_id = t.id) AS evidence_count, "
                        + "(SELECT count(*) FROM videos v WHERE v.task_id = t.id) AS video_count "
                        + "FROM tasks t WHERE t.status IN ('PENDING','PARSING','PLANNING','GENERATING') "
                        + "ORDER BY t.created_at ASC LIMIT ?",
                (rs, rowNum) -> new TaskView(
                        rs.getObject("id", UUID.class),
                        rs.getObject("user_id", UUID.class),
                        rs.getString("status"),
                        rs.getInt("progress"),
                        rs.getString("error_code"),
                        rs.getString("error_message"),
                        rs.getLong("version"),
                        rs.getLong("evidence_count"),
                        rs.getLong("video_count"),
                        rs.getObject("created_at", java.time.OffsetDateTime.class),
                        rs.getObject("updated_at", java.time.OffsetDateTime.class)
                ),
                limit
        );
    }
}
