from app.core.config import Settings
from app.ingest.asr import build_asr
from app.ingest.ocr import RapidOCRProvider, build_ocr


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, llm_provider="mock", **kwargs)


def test_rapidocr_selected():
    provider = build_ocr(_settings(ocr_enabled=True, ocr_provider="rapidocr"))
    assert isinstance(provider, RapidOCRProvider)


def test_ocr_can_be_disabled():
    assert build_ocr(_settings(ocr_enabled=False)) is None
    assert build_ocr(_settings(ocr_enabled=True, ocr_provider="none")) is None


def test_asr_can_be_disabled():
    assert build_asr(_settings(asr_provider="none")) is None
