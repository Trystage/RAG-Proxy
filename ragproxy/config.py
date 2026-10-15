"""配置加载与热重载。

优先级：命令行参数 > 环境变量 > 配置文件(config.yaml) > 内置默认值
- 环境变量只控制"进程怎么起"（端口/日志/数据目录/密钥）
- 业务配置全在 config.yaml，修改后热重载
"""
from __future__ import annotations

import copy
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .utils.text_extract import extract_text as _unused  # noqa: F401

ENV_DATA_DIR = "RAGPROXY_DATA_DIR"
ENV_PORT = "RAGPROXY_PORT"
ENV_HOST = "RAGPROXY_HOST"
ENV_LOG_LEVEL = "RAGPROXY_LOG_LEVEL"
ENV_CONFIG = "RAGPROXY_CONFIG"
ENV_SECRET_KEY = "RAGPROXY_SECRET_KEY"

DEFAULT_PORT = 8100
DEFAULT_HOST = "0.0.0.0"
DEFAULT_LOG_LEVEL = "INFO"

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
DEFAULT_CONFIG_NAME = "config.yaml"
SECRET_KEY_FILENAME = "secret_key.yaml"


@dataclass
class AppSettings:
    """进程级配置：由 CLI 与环境变量决定。"""

    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    config_path: Path = None  # type: ignore[assignment]
    secret_key: str | None = None
    docker: bool = False

    def __post_init__(self):
        if self.config_path is None:
            self.config_path = self.data_dir / "config" / DEFAULT_CONFIG_NAME


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def resolve_settings(
    data_dir: str | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
    config_path: str | None = None,
    secret_key: str | None = None,
) -> AppSettings:
    """按 优先级 CLI > 环境变量 > 默认值 解析进程配置。"""
    docker = _env_bool("RAGPROXY_DOCKER", False)

    if data_dir is None:
        data_dir = os.environ.get(ENV_DATA_DIR)
    if data_dir is None:
        data_dir = "/data" if docker else "./data"

    if host is None:
        host = os.environ.get(ENV_HOST, DEFAULT_HOST)
    if port is None:
        port = int(os.environ.get(ENV_PORT, str(DEFAULT_PORT)))
    if log_level is None:
        log_level = os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL)
    if config_path is None:
        config_path = os.environ.get(ENV_CONFIG)
    if secret_key is None:
        secret_key = os.environ.get(ENV_SECRET_KEY)

    settings = AppSettings(
        data_dir=Path(data_dir),
        host=host,
        port=port,
        log_level=log_level.upper(),
        config_path=Path(config_path) if config_path else None,
        secret_key=secret_key,
        docker=docker,
    )
    return settings


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先。"""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class ConfigManager:
    """业务配置（config.yaml）加载、热重载、原子写回。"""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._lock = threading.RLock()
        self._config: dict = {}
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._ensure_files()

    # ---------- 文件初始化 ----------
    def _ensure_files(self) -> None:
        cfg_dir = self.settings.data_dir / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("vectordb", "documents", "cache/embedding", "cache/rerank", "logs"):
            (self.settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
        if not self.settings.config_path.exists():
            src = DEFAULTS_DIR / DEFAULT_CONFIG_NAME
            shutil.copyfile(src, self.settings.config_path)
            self.logger_info(f"已从内置默认配置初始化：{self.settings.config_path}")

    def logger_info(self, msg: str) -> None:  # pragma: no cover - 避免循环导入
        import logging

        logging.getLogger("ragproxy.config").info(msg)

    # ---------- 加载 / 重载 ----------
    def load(self, force: bool = False) -> dict:
        with self._lock:
            now = time.time()
            try:
                mtime = self.settings.config_path.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            if (
                not force
                and self._config
                and now - self._last_check < 1.0
                and mtime == self._mtime
            ):
                return self._config
            self._last_check = now
            if mtime != self._mtime or force or not self._config:
                self._config = self._read_config()
                self._mtime = mtime
            return self._config

    def _read_config(self) -> dict:
        path = self.settings.config_path
        if not path.exists():
            self._ensure_files()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            data = {"_raw": data}
        # 与默认配置合并，保证新增字段有默认值
        with open(DEFAULTS_DIR / DEFAULT_CONFIG_NAME, "r", encoding="utf-8") as f:
            defaults = yaml.safe_load(f) or {}
        merged = _deep_merge(defaults, data)
        merged["_mtime"] = time.time()
        return merged

    # ---------- 访问 ----------
    def get(self, path: str, default=None):
        cur = self.load()
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return default
        return cur

    def raw(self) -> dict:
        return self.load()

    # ---------- 写回（Web UI 修改配置）----------
    def update_yaml(self, content: str) -> None:
        """校验 YAML 后原子写回配置文件。"""
        parsed = yaml.safe_load(content)
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("配置文件必须是 YAML 映射（顶层键值对）")
        # 写回
        tmp = self.settings.config_path.with_suffix(".yaml.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                parsed, f, allow_unicode=True, sort_keys=False, default_flow_style=False
            )
        tmp.replace(self.settings.config_path)
        self.load(force=True)

    # ---------- 敏感信息脱敏 ----------
    def sanitized(self) -> dict:
        """导出配置给前端展示，api_key 脱敏。"""
        cfg = copy.deepcopy(self.raw())
        for section in ("upstreams", "embeddings", "rerankers"):
            for item in cfg.get(section, []) or []:
                if isinstance(item, dict) and "api_key" in item and item["api_key"]:
                    item["api_key"] = mask_secret(item["api_key"])
        return cfg


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        # 短密钥保留前 2 位，避免显示成纯 "****"（与"损坏占位符 ****"混淆）
        return value[:2] + "***"
    return value[:3] + "****" + value[-3:]


def is_masked(value) -> bool:
    """判断密钥是否为脱敏显示/损坏占位值（真实密钥不会包含连续 3 个及以上星号）。"""
    if not value:
        return False
    return "***" in str(value)


# ---------- 面板登录密钥 ----------
def load_or_create_secret_key(settings: AppSettings, env_key: str | None) -> str:
    """密钥来源：环境变量 > <DATA_DIR>/config/secret_key.yaml > 首次生成。"""
    if env_key:
        return env_key
    key_file = settings.data_dir / "config" / SECRET_KEY_FILENAME
    if key_file.exists():
        data = yaml.safe_load(key_file.read_text(encoding="utf-8")) or {}
        sk = data.get("secret_key")
        if sk:
            return sk
    new_key = secrets.token_urlsafe(24)
    key_file.write_text(
        yaml.safe_dump({"secret_key": new_key}, allow_unicode=True),
        encoding="utf-8",
    )
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return new_key
