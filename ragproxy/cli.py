"""命令行入口：python -m ragproxy [--data-dir ...] [--port ...] ..."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m ragproxy",
        description="RAG Proxy：知识库中间层代理",
    )
    parser.add_argument("--data-dir", help="数据根目录（优先于环境变量 RAGPROXY_DATA_DIR）")
    parser.add_argument("--config", help="配置文件路径（默认 <data_dir>/config/config.yaml）")
    parser.add_argument("--host", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, help="监听端口（默认 8100）")
    parser.add_argument("--log-level", help="日志级别 DEBUG/INFO/WARNING/ERROR（默认 INFO）")
    parser.add_argument(
        "--secret-key",
        help="面板登录密钥（覆盖环境变量，仅建议临时使用）",
        default=None,
    )
    args = parser.parse_args(argv)

    from .config import resolve_settings

    settings = resolve_settings(
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        config_path=args.config,
        secret_key=args.secret_key,
    )

    from .main import create_app

    app = create_app(settings)

    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    sys.exit(main())
