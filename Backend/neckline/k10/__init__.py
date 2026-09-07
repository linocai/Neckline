"""K10 的独立领域内核。

这个包不借用旧策略链的表或运行参数。写入前必须由受控迁移显式建立 schema；所有读取
入口均使用只读连接，因此一个 GET 或列表读取永远不会创建数据库文件或执行 DDL。
"""

from .config import ConfigurationStatus, validate_run_config
from .schema import K10SchemaError, SchemaUnavailable, initialize_schema, rollback_schema

__all__ = [
    "ConfigurationStatus",
    "K10SchemaError",
    "SchemaUnavailable",
    "initialize_schema",
    "rollback_schema",
    "validate_run_config",
]
