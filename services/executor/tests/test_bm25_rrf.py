from app.index.bm25 import BM25Index, rrf_fuse, tokenize


def test_chinese_tokenize():
    tokens = tokenize("杭州的房租为什么这么高？")
    assert "杭州" in tokens
    assert "房租" in tokens


def test_bm25_ranks_relevant():
    index = BM25Index(
        [
            "杭州房租很高，尤其是隔断房。",
            "我们今天聊机器学习模型训练。",
            "重庆的整租价格相对便宜。",
        ]
    )
    hits = index.search("房租 隔断房", top_k=3)
    assert hits
    assert hits[0].index == 0


def test_rrf_fuse_combines_rankings():
    fused = dict(rrf_fuse([["a", "b", "c"], ["c", "a", "d"]], k=60))
    assert fused["a"] > fused["b"]
    assert fused["c"] > fused["b"]
    assert set(fused) == {"a", "b", "c", "d"}


def test_rrf_weights():
    vector_only = dict(rrf_fuse([["a"], []], weights=[1.0, 1.0]))
    bm25_only = dict(rrf_fuse([[], ["a"]], weights=[1.0, 1.0]))
    assert vector_only["a"] == bm25_only["a"]
