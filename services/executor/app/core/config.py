"""核心配置：全部来自环境变量，启动时快速失败。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根目录（services/executor/app/core/config.py -> Gether/）
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env"

ProviderName = Literal["deepseek", "dashscope", "openai", "ollama", "mock"]
SUPPORTED_PROVIDERS: tuple[str, ...] = ("deepseek", "dashscope", "openai", "ollama", "mock")
ROLES: tuple[str, ...] = ("planner", "critic", "extract", "writer", "router")

#: 各供应商内置默认值（可被环境变量覆盖）
PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:14b",
    },
    "mock": {
        "base_url": None,
        "model": "mock",
    },
}

#: 需要 API Key 的供应商
KEY_REQUIRED: frozenset[str] = frozenset({"deepseek", "dashscope", "openai"})


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str | None
    default_model: str
    role_models: dict[str, str | None] = field(default_factory=dict)

    def model_for(self, role: str) -> str:
        return self.role_models.get(role) or self.default_model


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 通用 ----------
    app_env: Literal["local", "demo", "prod"] = "local"
    app_log_level: str = "INFO"
    app_api_prefix: str = "/internal"
    app_task_max_duration_minutes: int = 60
    data_dir: str = "./data"
    raw_media_ttl_hours: int = 24

    # ---------- 视频源 ----------
    video_sources: str = "bilibili,youtube"
    bilibili_cookies_file: str = "./secrets/bilibili.cookies.txt"
    youtube_cookies_file: str = "./secrets/youtube.cookies.txt"
    ytdlp_cookies_file: str | None = None
    bilibili_sub_langs: str = "zh-Hans,zh-CN,zh,ai-zh"
    youtube_sub_langs: str = "zh-Hans,zh,en"
    bilibili_max_height: int = 720
    youtube_max_height: int = 720
    ytdlp_proxy: str | None = None
    ytdlp_rate_limit: str = "5M"
    ytdlp_sleep_requests: int = 1
    video_max_duration_minutes: int = 180
    video_max_concurrent_downloads: int = 2
    prefer_native_subtitle: bool = True
    store_video: bool = False

    # ---------- ASR ----------
    asr_provider: str = "faster_whisper"
    asr_model: str = "large-v3"
    asr_fast_model: str = "small"
    asr_fast_beam_size: int = 1
    asr_fast_word_timestamps: bool = False
    asr_fast_batch_size: int = 8
    asr_fast_concurrency: int = 1
    asr_device: str = "auto"
    asr_compute_type: str = "int8_float16"
    asr_language: str = "zh"
    asr_beam_size: int = 5
    asr_vad: bool = True
    asr_word_timestamps: bool = True
    asr_initial_prompt: str | None = None
    asr_hotwords: str | None = None
    asr_condition_on_previous_text: bool = False
    asr_vad_min_silence_ms: int = 500
    asr_vad_speech_pad_ms: int = 200
    asr_correction_enabled: bool = False
    asr_correction_batch_size: int = 30
    asr_correction_max_chars: int = 3000
    asr_correction_min_similarity: float = 0.2

    # ---------- 抽帧 / OCR ----------
    ocr_enabled: bool = True
    ocr_provider: str = "paddleocr"
    ocr_lang: str = "ch"
    ocr_min_confidence: float = 0.6
    frame_extract_mode: str = "scene"
    frame_scene_threshold: float = 0.3
    frame_interval_seconds: int = 5
    frame_max_per_video: int = 300
    frame_dedup_hamming: int = 5
    ocr_merge_gap_ms: int = 1500
    ocr_asr_fusion_enabled: bool = True
    ocr_asr_fusion_min_similarity: float = 0.55
    ocr_asr_fusion_min_confidence: float = 0.7

    # ---------- LLM 选择 ----------
    llm_provider: ProviderName = "deepseek"
    llm_fallback_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.3
    llm_temperature_writer: float = 0.6
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 3
    llm_concurrency: int = 4
    llm_json_mode: bool = True
    llm_fallback_model: str | None = None

    # 角色级全局覆盖（最高优先级）
    llm_model_planner: str | None = None
    llm_model_critic: str | None = None
    llm_model_extract: str | None = None
    llm_model_writer: str | None = None
    llm_model_router: str | None = None

    # ---------- 分供应商配置 ----------
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model_default: str | None = None
    deepseek_model_planner: str | None = None
    deepseek_model_critic: str | None = None
    deepseek_model_extract: str | None = None
    deepseek_model_writer: str | None = None
    deepseek_model_router: str | None = None

    dashscope_api_key: str | None = None
    dashscope_base_url: str | None = None
    dashscope_model_default: str | None = None
    dashscope_model_planner: str | None = None
    dashscope_model_critic: str | None = None
    dashscope_model_extract: str | None = None
    dashscope_model_writer: str | None = None
    dashscope_model_router: str | None = None

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model_default: str | None = None

    ollama_base_url: str | None = None
    ollama_model_default: str | None = None

    # ---------- Embedding / 检索 ----------
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512
    embedding_api_key: str | None = None
    embedding_batch_size: int = 32
    chunk_target_chars: int = 360
    chunk_max_chars: int = 480
    chunk_overlap_chars: int = 60
    dashscope_embedding_model: str = "text-embedding-v3"
    dashscope_embedding_dim: int = 1024
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    retrieval_top_k: int = 10
    retrieval_candidates: int = 50
    rrf_k: int = 40
    vector_weight: float = 1.0
    bm25_weight: float = 1.0
    auto_index_after_parse: bool = True
    parse_concurrency_fast: int = 2
    parse_concurrency_quality: int = 1
    report_rebind_enabled: bool = False
    report_reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    report_reranker_cache_dir: str | None = None
    report_rebind_threshold: float = 0.0
    report_rebind_lexical_threshold: float = 0.45
    # 质量模式证据量过大时跳过 cross-encoder，避免 8 路视频任务长时间卡在 90%。
    report_rebind_max_evidence: int = 1000
    report_rebind_timeout_seconds: int = 60
    # 快速模式：跳过 LLM critic，并行写章节，并跳过本地 cross-encoder rebind。
    report_parallel_sections: int = 4
    report_fast_parallel_enabled: bool = True
    report_fast_rebind_enabled: bool = False

    # ---------- 视频发现（P2.6） ----------
    discovery_enabled: bool = True
    discovery_sources: str = "bilibili"
    discovery_bilibili_search_mode: str = "api"
    # 发现阶段硬门槛：低于该播放量的候选在 Topic Gate / 排序 / 选择之前直接排除。
    discovery_min_views: int = 8000
    bilibili_min_views: int = 10000
    youtube_min_views: int = 10000
    discovery_fallback_min_views: int = 1000
    discovery_min_views_exception: bool = True
    discovery_min_engagement_rate: float = 0.01
    discovery_max_videos: int = 8
    discovery_min_videos: int = 2
    discovery_hard_max_videos: int = 8
    discovery_duration_budget_min: int = 180
    discovery_queries_per_facet: int = 2
    discovery_search_results_per_query: int = 20
    discovery_metadata_top_n: int = 30
    discovery_min_relevance: float = 0.35
    discovery_min_facet_coverage: float = 0.6
    discovery_mmr_lambda: float = 0.7
    discovery_freshness_half_life_days: int = 180
    discovery_freshness_weight: float = 0.15
    discovery_max_age_days: int = 0
    discovery_facet_match_threshold: float = 0.55
    discovery_facet_min_score: float = 0.20
    discovery_select_min_score: float = 0.25
    discovery_facet_lexical_weight: float = 0.5
    discovery_facet_embedding_weight: float = 0.5
    discovery_topic_semantic_threshold: float = 0.72
    discovery_max_video_minutes: int = 60
    discovery_search_retries: int = 2
    discovery_search_backoff_seconds: float = 4.0
    discovery_bilibili_cooldown_seconds: float = 15.0
    discovery_search_cache_ttl_seconds: int = 3600
    discovery_query_interval_seconds: float = 4.0

    # ---------- 存储 ----------
    database_url: str | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "gether"
    postgres_user: str = "gether"
    postgres_password: str = ""
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "gether-media"
    redis_url: str | None = None
    governor_base_url: str | None = None
    callback_hmac_secret: str = "change_me"

    # ---------- 校验 ----------
    @model_validator(mode="after")
    def _validate_llm(self) -> Settings:
        if self.llm_provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"LLM_PROVIDER={self.llm_provider!r} 不支持，可选：{', '.join(SUPPORTED_PROVIDERS)}"
            )
        self._require_key(self.llm_provider, "LLM_PROVIDER")
        if self.llm_fallback_provider:
            if self.llm_fallback_provider not in SUPPORTED_PROVIDERS:
                raise ValueError(
                    f"LLM_FALLBACK_PROVIDER={self.llm_fallback_provider!r} 不支持"
                )
            self._require_key(self.llm_fallback_provider, "LLM_FALLBACK_PROVIDER")
        return self

    def _require_key(self, provider: str, variable: str) -> None:
        if provider not in KEY_REQUIRED:
            return
        if not self.provider_api_key(provider):
            raise ValueError(
                f"{variable}={provider} 需要 API Key，但 {provider.upper()}_API_KEY "
                f"（或全局 LLM_API_KEY）为空。离线开发可设置 LLM_PROVIDER=mock。"
            )

    # ---------- 解析 ----------
    def provider_api_key(self, provider: str) -> str | None:
        if self.llm_api_key:
            return self.llm_api_key
        return getattr(self, f"{provider}_api_key", None)

    def provider_config(self, provider: str) -> ProviderConfig:
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError(f"未知 LLM 供应商：{provider}")
        defaults = PROVIDER_DEFAULTS[provider]
        base_url = (
            self.llm_base_url
            if provider == self.llm_provider and self.llm_base_url
            else getattr(self, f"{provider}_base_url", None) or defaults["base_url"] or ""
        )
        default_model = (
            getattr(self, f"{provider}_model_default", None) or defaults["model"] or provider
        )
        role_models: dict[str, str | None] = {}
        for role in ROLES:
            generic = getattr(self, f"llm_model_{role}", None)
            specific = getattr(self, f"{provider}_model_{role}", None)
            role_models[role] = generic or specific
        return ProviderConfig(
            name=provider,
            base_url=base_url,
            api_key=self.provider_api_key(provider),
            default_model=default_model,
            role_models=role_models,
        )

    @property
    def effective_provider(self) -> ProviderConfig:
        return self.provider_config(self.llm_provider)

    @property
    def fallback_provider(self) -> ProviderConfig | None:
        if not self.llm_fallback_provider:
            return None
        return self.provider_config(self.llm_fallback_provider)

    def temperature_for(self, role: str) -> float:
        return self.llm_temperature_writer if role == "writer" else self.llm_temperature

    @property
    def enabled_sources(self) -> list[str]:
        return [item.strip() for item in self.video_sources.split(",") if item.strip()]

    def effective_config(self) -> dict:
        """脱敏后的有效配置，供启动日志与本地调试。"""
        provider = self.effective_provider
        return {
            "app_env": self.app_env,
            "llm_provider": self.llm_provider,
            "llm_fallback_provider": self.llm_fallback_provider,
            "llm_base_url": provider.base_url,
            "llm_models": {role: provider.model_for(role) for role in ROLES},
            "llm_api_key_configured": bool(provider.api_key),
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "video_sources": self.enabled_sources,
            "ocr_enabled": self.ocr_enabled,
            "store_video": self.store_video,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
