"""
Open Targets Data Loader
Manages loading and caching of Open Targets datasets

Strategy:
- Pre-load ALL datasets at initialization
- OS cache warmup (via warmup_data.py) makes this fast (~10s)
- Data is cached per process; RAM use depends on the datasets queried
- Tahoe datasets use PyArrow lazy loading (too large for memory)
"""

import time
import logging
from pathlib import Path
from typing import Dict, Optional, List, Union
import pandas as pd
import pyarrow.dataset as ds
from src.config.env import Config
from src.config.datasets import OPEN_TARGETS_DATASETS

logger = logging.getLogger(__name__)


class OpenTargetsDataLoader:
    """
    Loads and caches Open Targets datasets

    Usage:
        loader = OpenTargetsDataLoader(preload_all=True)
        targets_df = loader.get_dataset("target")
        credible_sets_df = loader.get_dataset("credible_set")
    """

    def __init__(self, preload_all: bool = True):
        """
        Initialize data loader

        Args:
            preload_all: If True, pre-load all datasets at init.
                        If False, load datasets on first access.
        """
        self._cache: Dict[str, pd.DataFrame] = {}
        self._base_path = Path(Config.OPEN_TARGETS_PATH)
        self._load_times: Dict[str, float] = {}

        # All datasets to pre-load
        self._datasets_to_preload = list(OPEN_TARGETS_DATASETS)

        if preload_all:
            self._preload_all_datasets()

    def _preload_all_datasets(self):
        """Pre-load all datasets into memory at startup"""
        logger.info("=" * 70)
        logger.info("Pre-loading Open Targets datasets...")
        logger.info("(Tip: Run warmup_data.py first for faster loading)")
        logger.info("=" * 70)

        start_total = time.time()

        for dataset in self._datasets_to_preload:
            try:
                start = time.time()
                self._cache[dataset] = self._load_from_disk(dataset)
                load_time = time.time() - start
                self._load_times[dataset] = load_time

                rows = len(self._cache[dataset])
                cols = len(self._cache[dataset].columns)
                logger.info(f"  ✓ {dataset:30} {load_time:6.2f}s - {rows:>10,} rows × {cols:>3} cols")

            except Exception as e:
                logger.error(f"  ✗ {dataset:30} FAILED: {e}")
                # Don't stop loading other datasets if one fails
                continue

        total_time = time.time() - start_total

        # Calculate memory usage
        memory_gb = sum(
            df.memory_usage(deep=True).sum() / (1024**3)
            for df in self._cache.values()
        )

        logger.info("=" * 70)
        logger.info(f"✅ All datasets loaded in {total_time:.2f}s")
        logger.info(f"📊 DataFrame-reported memory: {memory_gb:.2f} GiB (resident memory may be higher)")
        logger.info(f"📦 Datasets cached: {len(self._cache)}/{len(self._datasets_to_preload)}")
        logger.info("=" * 70)

    def get_dataset(self, name: str) -> pd.DataFrame:
        """
        Get dataset by name, loading if not cached

        Args:
            name: Dataset name (e.g., 'target', 'credible_set')

        Returns:
            DataFrame with dataset contents

        Raises:
            FileNotFoundError: If dataset doesn't exist
            ValueError: If dataset failed to load
        """
        # Return from cache if available
        if name in self._cache:
            return self._cache[name]

        # Not in cache - load it now (lazy loading)
        logger.info(f"Loading {name} on first access...")
        start = time.time()

        try:
            self._cache[name] = self._load_from_disk(name)
            load_time = time.time() - start
            self._load_times[name] = load_time

            if load_time > 10:
                logger.warning(
                    f"⚠️  {name} took {load_time:.1f}s to load. "
                    "Consider adding to preload list or running warmup_data.py"
                )
            else:
                logger.info(f"  ✓ Loaded in {load_time:.2f}s")

            return self._cache[name]

        except Exception as e:
            logger.error(f"Failed to load {name}: {e}")
            raise

    def _load_from_disk(self, name: str) -> pd.DataFrame:
        """
        Load dataset from parquet files

        Args:
            name: Dataset name

        Returns:
            DataFrame with dataset contents

        Raises:
            FileNotFoundError: If dataset path doesn't exist
        """
        path = self._base_path / name

        if not path.exists():
            raise FileNotFoundError(
                f"Dataset '{name}' not found at {path}. "
                f"Available path: {self._base_path}"
            )

        # Use PyArrow Dataset API (handles both directories and single files)
        dataset = ds.dataset(str(path), format="parquet")
        table = dataset.to_table()
        return table.to_pandas()

    def is_loaded(self, name: str) -> bool:
        """
        Check if dataset is in cache

        Args:
            name: Dataset name

        Returns:
            True if dataset is cached, False otherwise
        """
        return name in self._cache

    def get_cached_datasets(self) -> List[str]:
        """
        Get list of cached dataset names

        Returns:
            List of dataset names currently in cache
        """
        return list(self._cache.keys())

    def get_stats(self) -> Dict[str, any]:
        """
        Get loading statistics

        Returns:
            Dictionary with cache statistics:
            - cached_datasets: List of cached dataset names
            - load_times: Dict of dataset -> load time (seconds)
            - total_cached: Number of datasets in cache
            - memory_estimate_gb: Estimated memory usage in GB
        """
        return {
            "cached_datasets": list(self._cache.keys()),
            "load_times": self._load_times.copy(),
            "total_cached": len(self._cache),
            "memory_estimate_gb": sum(
                df.memory_usage(deep=True).sum() / (1024**3)
                for df in self._cache.values()
            )
        }

    def clear_cache(self, dataset: Optional[str] = None):
        """
        Clear cache (all or specific dataset)

        Args:
            dataset: Dataset name to clear, or None to clear all
        """
        if dataset:
            if dataset in self._cache:
                del self._cache[dataset]
                logger.info(f"Cleared {dataset} from cache")
            else:
                logger.warning(f"{dataset} not in cache")
        else:
            self._cache.clear()
            self._load_times.clear()
            logger.info("Cleared all cached datasets")

    def get_dataset_info(self, name: str) -> Dict[str, any]:
        """
        Get information about a specific dataset

        Args:
            name: Dataset name

        Returns:
            Dictionary with dataset information
        """
        if name not in self._cache:
            return {
                "name": name,
                "cached": False,
                "error": "Dataset not loaded"
            }

        df = self._cache[name]

        return {
            "name": name,
            "cached": True,
            "rows": len(df),
            "columns": len(df.columns),
            "column_names": list(df.columns),
            "memory_mb": df.memory_usage(deep=True).sum() / (1024**2),
            "load_time_seconds": self._load_times.get(name, None)
        }

    def get_tahoe_dataset(self, name: str) -> ds.Dataset:
        """
        Get Tahoe PyArrow dataset (lazy loading - not loaded into memory)

        Tahoe datasets are queried lazily to avoid caching full tables in memory.
        Returns PyArrow Dataset for efficient filtered queries.

        Args:
            name: Dataset name - one of:
                - 'tahoe_pseudobulk_permissive' (padj < 0.10)
                - 'tahoe_pseudobulk_significant' (padj < 0.05)
                - 'tahoe_pseudobulk_high_quality' (padj < 0.05, abs(log2FoldChange) > 0.5)

        Returns:
            PyArrow Dataset (lazy - queries are executed on demand)

        Example:
            dataset = loader.get_tahoe_dataset('tahoe_pseudobulk_permissive')
            # Query for specific drug+cell line
            results = dataset.to_table(
                filter=(ds.field('drug') == 'Doxorubicin') &
                       (ds.field('Cell_ID_DepMap') == 'ACH-000956')
            ).to_pandas()
        """
        if not Config.TAHOE_DATA_PATH:
            raise ValueError(
                "TAHOE_DATA_PATH not set in environment. "
                "Set it to the directory containing Tahoe pseudobulk parquet files."
            )
        tahoe_base = Path(Config.TAHOE_DATA_PATH)

        dataset_map = {
            'tahoe_pseudobulk_permissive': tahoe_base / "tahoe_permissive_padj010.parquet",  # Single consolidated file
            'tahoe_pseudobulk_significant': tahoe_base / "pseudobulk_de_significant",
            'tahoe_pseudobulk_high_quality': tahoe_base / "pseudobulk_de_high_quality",
        }

        if name not in dataset_map:
            raise ValueError(
                f"Unknown Tahoe dataset: {name}. "
                f"Available: {list(dataset_map.keys())}"
            )

        path = dataset_map[name]
        if not path.exists():
            raise FileNotFoundError(
                f"Tahoe dataset not found at {path}. "
                f"Prepare the filtered files with tools/prepare_tahoe.py (see docs/TAHOE_SETUP.md)."
            )

        return ds.dataset(str(path), format='parquet', exclude_invalid_files=True)

    def get_tahoe_metadata(self, metadata_type: str) -> pd.DataFrame:
        """
        Get Tahoe metadata tables (cached in memory - small ~1.4 MB total)

        Args:
            metadata_type: One of 'gene', 'drug', 'cell_line', 'sample'

        Returns:
            DataFrame with metadata

        Example:
            drug_meta = loader.get_tahoe_metadata('drug')
            # drug_meta has columns: drug, targets, moa-broad, moa-fine, etc.
        """
        cache_key = f"tahoe_metadata_{metadata_type}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        if not Config.TAHOE_DATA_PATH:
            raise ValueError(
                "TAHOE_DATA_PATH not set in environment. "
                "Set it to the directory containing Tahoe pseudobulk parquet files."
            )
        tahoe_base = Path(Config.TAHOE_DATA_PATH)
        metadata_dir = tahoe_base / "metadata"

        file_map = {
            'gene': metadata_dir / "gene_metadata.parquet",
            'drug': metadata_dir / "drug_metadata.parquet",
            'cell_line': metadata_dir / "cell_line_metadata.parquet",
            'sample': metadata_dir / "sample_metadata.parquet"
        }

        if metadata_type not in file_map:
            raise ValueError(
                f"Unknown metadata type: {metadata_type}. "
                f"Available: {list(file_map.keys())}"
            )

        file_path = file_map[metadata_type]
        if not file_path.exists():
            raise FileNotFoundError(
                f"Metadata file not found: {file_path}. "
                f"Ensure Tahoe download completed successfully."
            )

        # Load and cache (small files, safe to keep in memory)
        self._cache[cache_key] = pd.read_parquet(file_path)
        logger.info(f"Loaded Tahoe metadata '{metadata_type}': {len(self._cache[cache_key])} rows")
        return self._cache[cache_key]


# Global singleton instance
_loader: Optional[OpenTargetsDataLoader] = None


def get_data_loader(preload_all: bool = True) -> OpenTargetsDataLoader:
    """
    Get or create global data loader instance (singleton pattern)

    Args:
        preload_all: Only used on first call when creating loader

    Returns:
        Global OpenTargetsDataLoader instance

    Note:
        This ensures only one DataLoader instance exists per process,
        which is important for memory efficiency when using MCP servers.
    """
    global _loader
    if _loader is None:
        Config.validate()  # fail fast (clean message) before touching data paths
        _loader = OpenTargetsDataLoader(preload_all=preload_all)
    return _loader
