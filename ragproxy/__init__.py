"""RAG Proxy：知识库中间层代理。

在请求转发到上游 LLM 之前，按"模型名"加载中间知识库，
检索结果注入 prompt，实现不训练 LoRA 的专项能力增强。

硬性约束：不本地部署任何模型，Embedding / ReRanker / LLM 全部远程调用。
"""

__version__ = "1.0.0"
