from app.contracts import EvidencePacket, EvidenceSourceType
from app.index.chunking import chunk_evidence
from app.index.embedding import MockEmbeddingProvider
from app.index.retrieval import rank_hybrid


def _packet(index: int, text: str) -> EvidencePacket:
    return EvidencePacket(
        task_id="t1",
        video_id="v1",
        modality="asr",
        text=text,
        start_ms=index * 1000,
        end_ms=index * 1000 + 900,
        source_type=EvidenceSourceType.WHISPER,
        content_hash=f"h{index}",
    )


async def test_rank_hybrid_merges_vector_and_bm25():
    chunks = chunk_evidence(
        [
            _packet(0, "杭州的房租很高，隔断房很多。"),
            _packet(1, "模型训练需要大量显卡和数据集。"),
            _packet(2, "重庆的整租价格相对便宜。"),
            _packet(3, "广州的天气潮湿，回南天很难受。"),
            _packet(4, "上海的地铁早高峰非常拥挤。"),
            _packet(5, "北京的秋天适合去公园散步。"),
            _packet(6, "成都的火锅店排队很久。"),
            _packet(7, "深圳的科技公司加班比较多。"),
        ],
        target_chars=20,
        max_chars=30,
        overlap_chars=0,
    )
    embedder = MockEmbeddingProvider(dim=64)
    # 构造与第一个 chunk 完全相同的 query 向量，保证向量路命中
    target = chunks[0].text
    query_vector = await embedder.embed_query(target)
    embeddings = await embedder.embed_documents([chunk.text for chunk in chunks])

    hits = rank_hybrid(
        chunks,
        embeddings,
        query="房租 隔断房",
        query_vector=query_vector,
        candidate_k=3,
        rrf_k=60,
        vector_weight=1.0,
        bm25_weight=1.0,
        top_k=3,
    )
    assert hits
    assert hits[0].chunk.text == target
    assert hits[0].vector_rank == 1
    assert hits[0].bm25_rank == 1
