"""
DATA — data acquisition, loading & reference data

- Config: JSON config loader with CLI overrides
- DownloadDataStage: (conditional) S&P 500 download from Yahoo
- DataLoaderStage: load price CSVs, build DAG & market returns
- UniverseScreener: GICS sector resolution from constituents
"""

from modules.data.config_loader import Config
from modules.data.download_data import DownloadDataStage
from modules.data.data_loader import DataLoaderStage
from modules.data.screener import UniverseScreener

__all__ = ["Config", "DownloadDataStage", "DataLoaderStage", "UniverseScreener"]