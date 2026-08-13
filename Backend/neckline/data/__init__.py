"""数据层(plan §3.9 `data/`):tushare_client(拉取)/ limit_derived(涨跌停自算)/
adjust(前复权)/ market_data(polars scan_parquet 访问层 + SQLite 元数据读取)。

子模块按需直接导入,如 `from neckline.data.market_data import get_market_slice`。
"""
