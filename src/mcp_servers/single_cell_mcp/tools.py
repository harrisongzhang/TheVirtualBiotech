"""
Single-Cell Census MCP Server Tools

Provides tools for querying and retrieving single-cell RNA-seq data from
the CELLxGENE Census (https://chanzuckerberg.github.io/cellxgene-census/).

The Census contains 100M+ human cells from thousands of studies, standardized
and queryable via a cloud-based interface.

All tools focus on human data (homo_sapiens) only.
"""

import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any, Union
import os
import json
import sys
from pathlib import Path

# Add project root for OutputManager import
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.utils.output_manager import OutputManager

# Global census connection (lazy loaded)
_census_connection = None
_census_version = "stable"

# Constants
ORGANISM = "homo_sapiens"
MAX_QUERY_SIZE_GB = 2.5
BYTES_PER_GB = 1024**3


def get_census():
    """
    Lazy loader for Census connection.
    Opens Census on first call and caches for subsequent calls.

    CRITICAL: Must be called inside each tool function, not at module import time,
    to avoid MCP SDK handshake timeout.

    Note: cellxgene_census is imported HERE (not at module level) to ensure
    fast MCP server startup (<3 seconds required by MCP protocol).
    """
    global _census_connection
    if _census_connection is None:
        import cellxgene_census  # Import only when needed (7s delay)
        _census_connection = cellxgene_census.open_soma(census_version=_census_version)
    return _census_connection


def convert_to_json_serializable(obj: Any) -> Any:
    """Convert numpy/pandas types to JSON-serializable native Python types"""
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Series):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient='records')
    elif isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    else:
        return obj


def estimate_query_size(n_cells: int, n_genes: int, include_expression: bool = True) -> float:
    """
    Estimate query size in GB.

    Assumptions:
    - Metadata: ~500 bytes per cell
    - Raw expression (sparse): ~5KB per cell on average
    - Gene metadata: ~200 bytes per gene
    """
    metadata_size = n_cells * 500  # bytes
    gene_metadata_size = n_genes * 200  # bytes

    if include_expression:
        # Sparse matrix: ~5KB per cell (typical for single-cell)
        expression_size = n_cells * 5000  # bytes
    else:
        expression_size = 0

    total_bytes = metadata_size + gene_metadata_size + expression_size
    total_gb = total_bytes / BYTES_PER_GB
    return total_gb


# ============================================================================
# TIER 1: Essential Discovery & Retrieval
# ============================================================================

def get_census_info() -> Dict[str, Any]:
    """
    Get Census version information and overall statistics.

    Returns:
        Dictionary containing:
        - census_version: Version string
        - organism: Always 'homo_sapiens'
        - total_cell_count: Total number of human cells
        - total_gene_count: Total number of genes
        - available_layers: List of expression matrix layers
    """
    census = get_census()

    # Get human data
    human = census['census_data'][ORGANISM]
    obs = human['obs']
    rna = human['ms']['RNA']
    var = rna['var']
    X = rna['X']

    result = {
        'census_version': _census_version,
        'organism': ORGANISM,
        'total_cell_count': int(obs.count),
        'total_gene_count': int(var.count),
        'available_layers': list(X.keys()),
        'note': 'This server provides access to human single-cell data only'
    }

    return convert_to_json_serializable(result)


def list_metadata_values(
    column_name: str,
    value_filter: Optional[str] = None,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    List unique values for a cell metadata column with cell counts.

    Args:
        column_name: Metadata column to query (e.g., 'cell_type', 'tissue', 'disease', 'assay')
        value_filter: Optional filter to apply before listing values (SOMA syntax)
        limit: Maximum number of unique values to return (default: 1000)

    Returns:
        Dictionary containing:
        - column_name: The queried column
        - value_filter_applied: The filter used (if any)
        - value_counts: List of {value, count} dictionaries
        - total_unique_values: Total number of unique values
        - limited: Whether results were limited

    Example:
        list_metadata_values('cell_type')
        list_metadata_values('tissue', value_filter="disease == 'COVID-19'")
    """
    census = get_census()
    human = census['census_data'][ORGANISM]
    obs = human['obs']

    # Query with optional filter
    if value_filter:
        query = obs.read(column_names=[column_name], value_filter=value_filter)
    else:
        query = obs.read(column_names=[column_name])

    # Get data
    data_table = query.concat()
    df = data_table.to_pandas()

    # Count values
    value_counts = df[column_name].value_counts()

    # Limit results
    if len(value_counts) > limit:
        value_counts = value_counts.head(limit)
        limited = True
    else:
        limited = False

    # Format results
    value_count_list = [
        {'value': str(value), 'count': int(count)}
        for value, count in value_counts.items()
    ]

    result = {
        'column_name': column_name,
        'value_filter_applied': value_filter if value_filter else None,
        'value_counts': value_count_list,
        'total_unique_values': len(value_counts),
        'limited': limited,
        'limit': limit
    }

    return convert_to_json_serializable(result)


def search_genes(
    gene_symbols: Optional[List[str]] = None,
    ensembl_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Search for genes by symbol or Ensembl ID.

    Args:
        gene_symbols: List of gene symbols (e.g., ['CD4', 'CD8A', 'FOXP3'])
        ensembl_ids: List of Ensembl IDs (e.g., ['ENSG00000010610'])

    Returns:
        Dictionary containing:
        - genes_found: List of gene records with:
            - feature_id: Ensembl ID
            - feature_name: Gene symbol
            - feature_length: Gene length in bp
            - n_measured_obs: Number of cells where gene was measured
            - nnz: Number of non-zero measurements
        - genes_not_found: List of queried symbols/IDs not found

    Example:
        search_genes(gene_symbols=['CD4', 'CD8A'])
    """
    if not gene_symbols and not ensembl_ids:
        raise ValueError("Must provide either gene_symbols or ensembl_ids")

    census = get_census()
    human = census['census_data'][ORGANISM]
    rna = human['ms']['RNA']
    var = rna['var']

    # Read all gene metadata
    var_query = var.read(column_names=['feature_id', 'feature_name', 'feature_length',
                                        'n_measured_obs', 'nnz'])
    var_table = var_query.concat()
    var_df = var_table.to_pandas()

    genes_found = []
    genes_not_found = []

    # Search by gene symbols
    if gene_symbols:
        for symbol in gene_symbols:
            matches = var_df[var_df['feature_name'] == symbol]
            if len(matches) > 0:
                for _, row in matches.iterrows():
                    genes_found.append({
                        'feature_id': str(row['feature_id']),
                        'feature_name': str(row['feature_name']),
                        'feature_length': int(row['feature_length']) if pd.notna(row['feature_length']) else None,
                        'n_measured_obs': int(row['n_measured_obs']),
                        'nnz': int(row['nnz'])
                    })
            else:
                genes_not_found.append(symbol)

    # Search by Ensembl IDs
    if ensembl_ids:
        for ensembl_id in ensembl_ids:
            matches = var_df[var_df['feature_id'] == ensembl_id]
            if len(matches) > 0:
                for _, row in matches.iterrows():
                    genes_found.append({
                        'feature_id': str(row['feature_id']),
                        'feature_name': str(row['feature_name']),
                        'feature_length': int(row['feature_length']) if pd.notna(row['feature_length']) else None,
                        'n_measured_obs': int(row['n_measured_obs']),
                        'nnz': int(row['nnz'])
                    })
            else:
                genes_not_found.append(ensembl_id)

    result = {
        'genes_found': genes_found,
        'genes_not_found': genes_not_found,
        'total_found': len(genes_found),
        'total_not_found': len(genes_not_found)
    }

    return convert_to_json_serializable(result)


def query_cell_metadata(
    output_path: str,
    value_filter: Optional[str] = None,
    columns: Optional[List[str]] = None,
    limit: int = 50000
) -> Dict[str, Any]:
    """
    Query cell metadata and save to parquet file.
    Use this to explore data before calling get_anndata().

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        value_filter: Filter expression (SOMA syntax, e.g., "cell_type == 'T cell' and tissue == 'blood'")
        columns: List of metadata columns to return (default: common columns)
        limit: Maximum number of cells to return (default: 50000)

    Returns:
        Dictionary containing:
        - output_path: Path to saved .parquet file
        - n_cells_returned: Number of cells in file
        - n_cells_total: Total cells matching filter
        - columns_returned: List of column names
        - limited: Whether results were truncated
        - size_mb: File size in MB
        - column_stats: Summary of categorical columns

    Example:
        result = query_cell_metadata(
            output_path='tcells_metadata.parquet',
            value_filter="tissue == 'lung' and disease == 'COVID-19'",
            limit=10000
        )
        # Agent can then: Read file with pandas or analyze

    Note: File persists for reuse. Use pandas.read_parquet() to load.
    """
    # Initialize OutputManager
    om = OutputManager(server_name='single_cell', tool_name='query_cell_metadata')
    final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

    census = get_census()
    human = census['census_data'][ORGANISM]
    obs = human['obs']

    # Default columns if not specified
    if columns is None:
        columns = ['cell_type', 'tissue', 'disease', 'assay', 'sex', 'development_stage',
                  'dataset_id', 'donor_id', 'suspension_type']

    # Query with optional filter
    if value_filter:
        query = obs.read(column_names=columns, value_filter=value_filter)
    else:
        query = obs.read(column_names=columns)

    # Get data
    data_table = query.concat()
    df = data_table.to_pandas()

    # Check total count
    n_cells_total = len(df)

    # Limit results
    if len(df) > limit:
        df = df.iloc[:limit]
        limited = True
    else:
        limited = False

    n_cells_returned = len(df)

    # Save to parquet file
    df.to_parquet(final_path, compression='snappy', index=False)

    # Compute column statistics for categorical columns
    column_stats = {}
    for col in columns:
        if col in df.columns:
            unique_count = df[col].nunique()
            column_stats[col] = {
                'unique_values': int(unique_count),
                'top_value': str(df[col].mode()[0]) if len(df) > 0 and not df[col].isna().all() else None
            }

    # Get file size
    file_size_bytes = os.path.getsize(final_path)
    file_size_mb = file_size_bytes / (1024**2)

    # Register output with OutputManager
    om.register_output(
        file_path=final_path,
        query_params={
            'value_filter': value_filter,
            'columns': columns,
            'limit': limit
        },
        n_records=n_cells_returned,
        size_mb=file_size_mb,
        additional_metadata={
            'n_cells_total': n_cells_total,
            'limited': limited,
            'column_stats': column_stats
        }
    )

    result = {
        'output_path': final_path,
        'n_cells_returned': n_cells_returned,
        'n_cells_total': n_cells_total,
        'columns_returned': columns,
        'value_filter_applied': value_filter if value_filter else None,
        'limited': limited,
        'limit': limit,
        'size_mb': round(file_size_mb, 2),
        'column_stats': column_stats,
        'note': 'Metadata saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse.'
    }

    return convert_to_json_serializable(result)


def get_anndata(
    output_path: str,
    value_filter: Optional[str] = None,
    gene_symbols: Optional[List[str]] = None,
    ensembl_ids: Optional[List[str]] = None,
    obs_columns: Optional[List[str]] = None,
    layer: str = 'raw'
) -> Dict[str, Any]:
    """
    Retrieve filtered AnnData object and save to .h5ad file.
    This is the main data retrieval tool.

    Args:
        output_path: Path where .h5ad file will be saved (REQUIRED)
        value_filter: Cell filter (SOMA syntax, e.g., "tissue == 'lung' and disease == 'COVID-19'")
        gene_symbols: Optional list of gene symbols to subset (default: all genes)
        ensembl_ids: Optional list of Ensembl IDs to subset (default: all genes)
        obs_columns: Cell metadata columns to include (default: all available)
        layer: Expression layer to retrieve ('raw' or 'normalized', default: 'raw')

    Returns:
        Dictionary containing:
        - output_path: Path to saved .h5ad file
        - n_cells: Number of cells
        - n_genes: Number of genes
        - layer_retrieved: Which layer was retrieved
        - size_mb: File size in MB
        - estimated_memory_gb: Estimated memory required to load

    Example:
        get_anndata(
            output_path='/path/to/output.h5ad',
            value_filter="tissue == 'lung' and cell_type == 'T cell'",
            gene_symbols=['CD4', 'CD8A', 'FOXP3'],
            layer='normalized'
        )

    Note: Max query size is 2GB. Use count_cells() first to check query size.
    """
    # Initialize OutputManager
    om = OutputManager(server_name='single_cell', tool_name='get_anndata')
    final_path = om.get_output_path(user_path=output_path, auto_suffix='.h5ad')

    # Validate layer
    if layer not in ['raw', 'normalized']:
        raise ValueError(f"layer must be 'raw' or 'normalized', got: {layer}")

    # Build gene filter if provided
    var_value_filter = None
    if gene_symbols or ensembl_ids:
        filter_parts = []
        if gene_symbols:
            symbols_str = "', '".join(gene_symbols)
            filter_parts.append(f"feature_name in ['{symbols_str}']")
        if ensembl_ids:
            ids_str = "', '".join(ensembl_ids)
            filter_parts.append(f"feature_id in ['{ids_str}']")
        var_value_filter = " or ".join(filter_parts)

    # Import cellxgene_census here (lazy loading)
    import cellxgene_census

    # Use CELLxGENE Census get_anndata function
    census = get_census()
    adata = cellxgene_census.get_anndata(
        census=census,
        organism=ORGANISM,
        measurement_name='RNA',
        X_name=layer,
        obs_value_filter=value_filter,
        var_value_filter=var_value_filter,
        column_names={"obs": obs_columns} if obs_columns else None
    )

    # Check size before saving
    n_cells = adata.n_obs
    n_genes = adata.n_vars
    estimated_size_gb = estimate_query_size(n_cells, n_genes, include_expression=True)

    if estimated_size_gb > MAX_QUERY_SIZE_GB:
        raise ValueError(
            f"Query size ({estimated_size_gb:.2f} GB) exceeds maximum ({MAX_QUERY_SIZE_GB} GB). "
            f"Cells: {n_cells:,}, Genes: {n_genes:,}. "
            "Please apply more restrictive filters or subset genes."
        )

    # Save to file
    adata.write_h5ad(final_path, compression='gzip')

    # Get file size
    file_size_bytes = os.path.getsize(final_path)
    file_size_mb = file_size_bytes / (1024**2)

    # Register output with OutputManager
    om.register_output(
        file_path=final_path,
        query_params={
            'value_filter': value_filter,
            'gene_symbols': gene_symbols,
            'ensembl_ids': ensembl_ids,
            'obs_columns': obs_columns,
            'layer': layer
        },
        n_records=n_cells,
        size_mb=file_size_mb,
        additional_metadata={
            'n_genes': n_genes,
            'layer_retrieved': layer,
            'estimated_memory_gb': round(estimated_size_gb, 2),
            'genes_subsetted': bool(gene_symbols or ensembl_ids)
        }
    )

    result = {
        'output_path': final_path,
        'n_cells': n_cells,
        'n_genes': n_genes,
        'layer_retrieved': layer,
        'size_mb': round(file_size_mb, 2),
        'estimated_memory_gb': round(estimated_size_gb, 2),
        'value_filter_applied': value_filter if value_filter else None,
        'genes_subsetted': bool(gene_symbols or ensembl_ids),
        'obs_columns_included': obs_columns if obs_columns else 'all',
        'note': 'AnnData saved to h5ad file. File persists for reuse.'
    }

    return convert_to_json_serializable(result)


# ============================================================================
# TIER 2: Query Planning & Statistics
# ============================================================================

def count_cells(
    value_filter: Optional[str] = None
) -> Dict[str, Any]:
    """
    Count cells matching a filter WITHOUT retrieving data.
    Use this to preview query size before calling get_anndata().

    Args:
        value_filter: Filter expression (SOMA syntax)

    Returns:
        Dictionary containing:
        - n_cells: Number of cells matching filter
        - estimated_size_gb: Estimated size if retrieved with all genes
        - within_size_limit: Whether query is within 2GB limit
        - recommendation: Suggested action based on size

    Example:
        count_cells(value_filter="tissue == 'lung'")
    """
    census = get_census()
    human = census['census_data'][ORGANISM]
    obs = human['obs']
    rna = human['ms']['RNA']
    var = rna['var']

    # Count cells
    if value_filter:
        query = obs.read(column_names=['soma_joinid'], value_filter=value_filter)
    else:
        query = obs.read(column_names=['soma_joinid'])

    data_table = query.concat()
    n_cells = len(data_table)

    # Get gene count
    n_genes = var.count

    # Estimate size
    estimated_size_gb = estimate_query_size(n_cells, n_genes, include_expression=True)
    within_limit = estimated_size_gb <= MAX_QUERY_SIZE_GB

    # Generate recommendation
    if within_limit:
        recommendation = "Query size is within limit. Safe to call get_anndata()."
    else:
        recommendation = (
            f"Query size ({estimated_size_gb:.2f} GB) exceeds {MAX_QUERY_SIZE_GB} GB limit. "
            "Consider: (1) Apply more restrictive filters, (2) Subset to specific genes, "
            "or (3) Split into multiple smaller queries."
        )

    result = {
        'n_cells': int(n_cells),
        'n_genes': int(n_genes),
        'estimated_size_gb': round(estimated_size_gb, 2),
        'max_size_gb': MAX_QUERY_SIZE_GB,
        'within_size_limit': within_limit,
        'value_filter_applied': value_filter if value_filter else None,
        'recommendation': recommendation
    }

    return convert_to_json_serializable(result)


def get_gene_statistics(
    gene_symbols: Optional[List[str]] = None,
    ensembl_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Get detailed statistics for specific genes.

    Args:
        gene_symbols: List of gene symbols
        ensembl_ids: List of Ensembl IDs

    Returns:
        Dictionary containing:
        - gene_statistics: List of gene records with:
            - feature_id, feature_name, feature_length
            - n_measured_obs: Number of cells where gene was measured
            - nnz: Number of non-zero measurements
            - sparsity: Fraction of zero values (nnz / n_measured_obs)

    Example:
        get_gene_statistics(gene_symbols=['CD4', 'CD8A', 'FOXP3'])
    """
    # Use search_genes to get base info
    gene_info = search_genes(gene_symbols=gene_symbols, ensembl_ids=ensembl_ids)

    # Add sparsity calculation
    gene_statistics = []
    for gene in gene_info['genes_found']:
        n_measured = gene['n_measured_obs']
        nnz = gene['nnz']

        sparsity = 1.0 - (nnz / n_measured) if n_measured > 0 else 1.0

        gene_statistics.append({
            **gene,
            'sparsity': round(sparsity, 4),
            'percent_nonzero': round((1 - sparsity) * 100, 2)
        })

    result = {
        'gene_statistics': gene_statistics,
        'genes_not_found': gene_info['genes_not_found'],
        'total_found': gene_info['total_found']
    }

    return convert_to_json_serializable(result)


def summarize_datasets(
    value_filter: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Get information about datasets matching a filter.

    Args:
        value_filter: Filter to apply (SOMA syntax)
        limit: Maximum number of datasets to return

    Returns:
        Dictionary containing:
        - datasets: List of dataset records with:
            - dataset_id
            - n_cells: Number of cells in this dataset
            - cell_types: Unique cell types
            - tissues: Unique tissues
            - assays: Assays used
            - diseases: Diseases studied
        - total_datasets: Total number of datasets

    Example:
        summarize_datasets(value_filter="tissue == 'lung'")
    """
    census = get_census()
    human = census['census_data'][ORGANISM]
    obs = human['obs']

    # Query metadata
    columns = ['dataset_id', 'cell_type', 'tissue', 'assay', 'disease']
    if value_filter:
        query = obs.read(column_names=columns, value_filter=value_filter)
    else:
        query = obs.read(column_names=columns)

    data_table = query.concat()
    df = data_table.to_pandas()

    # Group by dataset
    dataset_summaries = []
    for dataset_id in df['dataset_id'].unique()[:limit]:
        dataset_df = df[df['dataset_id'] == dataset_id]

        dataset_summaries.append({
            'dataset_id': str(dataset_id),
            'n_cells': int(len(dataset_df)),
            'cell_types': list(dataset_df['cell_type'].unique()),
            'tissues': list(dataset_df['tissue'].unique()),
            'assays': list(dataset_df['assay'].unique()),
            'diseases': list(dataset_df['disease'].unique())
        })

    result = {
        'datasets': dataset_summaries,
        'total_datasets': int(df['dataset_id'].nunique()),
        'total_cells': int(len(df)),
        'limited': df['dataset_id'].nunique() > limit,
        'value_filter_applied': value_filter if value_filter else None
    }

    return convert_to_json_serializable(result)


# ============================================================================
# TIER 3: Advanced/Specialized
# ============================================================================

def get_cell_type_tissue_matrix(
    output_path: str,
    value_filter: Optional[str] = None,
    top_n_cell_types: int = 50,
    top_n_tissues: int = 50
) -> Dict[str, Any]:
    """
    Get crosstab matrix of cell types × tissues and save to parquet file.
    Useful for exploring what data is available.

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        value_filter: Optional filter to apply first
        top_n_cell_types: Number of top cell types to include (default: 50)
        top_n_tissues: Number of top tissues to include (default: 50)

    Returns:
        Dictionary containing:
        - output_path: Path to saved .parquet file
        - n_cell_types: Number of unique cell types
        - n_tissues: Number of unique tissues
        - total_combinations: Total non-zero combinations
        - size_mb: File size in MB
        - top_combinations: Top 10 cell_type × tissue combinations by count

    Example:
        result = get_cell_type_tissue_matrix(
            output_path='cell_tissue_matrix.parquet',
            value_filter="disease == 'normal'"
        )
        # Agent can load with: pandas.read_parquet(result['output_path'])

    Note: Matrix saved as long-format table with columns: cell_type, tissue, count.
          File persists for reuse.
    """
    # Initialize OutputManager
    om = OutputManager(server_name='single_cell', tool_name='get_cell_type_tissue_matrix')
    final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

    census = get_census()
    human = census['census_data'][ORGANISM]
    obs = human['obs']

    # Query metadata
    columns = ['cell_type', 'tissue']
    if value_filter:
        query = obs.read(column_names=columns, value_filter=value_filter)
    else:
        query = obs.read(column_names=columns)

    data_table = query.concat()
    df = data_table.to_pandas()

    # Get top cell types and tissues by count
    top_cell_types = df['cell_type'].value_counts().head(top_n_cell_types).index.tolist()
    top_tissues = df['tissue'].value_counts().head(top_n_tissues).index.tolist()

    # Filter to top
    df_filtered = df[
        df['cell_type'].isin(top_cell_types) &
        df['tissue'].isin(top_tissues)
    ]

    # Create crosstab
    crosstab = pd.crosstab(df_filtered['cell_type'], df_filtered['tissue'])

    # Convert to long format for parquet
    matrix_df = crosstab.stack().reset_index()
    matrix_df.columns = ['cell_type', 'tissue', 'count']
    # Filter out zero counts
    matrix_df = matrix_df[matrix_df['count'] > 0]
    # Sort by count descending
    matrix_df = matrix_df.sort_values('count', ascending=False)

    # Save to parquet
    matrix_df.to_parquet(final_path, compression='snappy', index=False)

    # Get top 10 combinations for summary
    top_10 = matrix_df.head(10).to_dict(orient='records')
    for record in top_10:
        record['count'] = int(record['count'])

    # Get file size
    file_size_bytes = os.path.getsize(final_path)
    file_size_mb = file_size_bytes / (1024**2)

    # Register output with OutputManager
    om.register_output(
        file_path=final_path,
        query_params={
            'value_filter': value_filter,
            'top_n_cell_types': top_n_cell_types,
            'top_n_tissues': top_n_tissues
        },
        n_records=len(matrix_df),
        size_mb=file_size_mb,
        additional_metadata={
            'n_cell_types': len(crosstab.index),
            'n_tissues': len(crosstab.columns),
            'top_combinations': top_10
        }
    )

    result = {
        'output_path': final_path,
        'n_cell_types': len(crosstab.index),
        'n_tissues': len(crosstab.columns),
        'total_combinations': len(matrix_df),
        'value_filter_applied': value_filter if value_filter else None,
        'size_mb': round(file_size_mb, 2),
        'top_combinations': top_10,
        'note': 'Matrix saved to parquet file in long format. Use pandas.read_parquet() to load. File persists for reuse.'
    }

    return convert_to_json_serializable(result)


def get_expression_for_genes(
    gene_symbols: List[str],
    output_path: str,
    value_filter: Optional[str] = None,
    layer: str = 'raw',
    max_cells: int = 20000
) -> Dict[str, Any]:
    """
    Get expression matrix for specific genes and save to .h5ad file.
    Returns file path and summary statistics (NOT full data).

    Args:
        gene_symbols: List of gene symbols to retrieve
        output_path: Path where .h5ad file will be saved (REQUIRED)
        value_filter: Cell filter (SOMA syntax)
        layer: Expression layer ('raw' or 'normalized')
        max_cells: Maximum number of cells to return (default: 20000)

    Returns:
        Dictionary containing:
        - output_path: Path to saved .h5ad file
        - n_cells: Number of cells retrieved
        - n_genes: Number of genes retrieved
        - n_nonzero_values: Number of non-zero expression values
        - genes_found: List of genes successfully retrieved
        - genes_not_found: List of genes not found
        - summary_stats: Expression statistics (mean, median, percentiles, etc.)
        - size_mb: File size in MB

    Example:
        result = get_expression_for_genes(
            gene_symbols=['CD4', 'CD8A'],
            output_path='/path/to/output.h5ad',
            value_filter="cell_type == 'T cell'",
            max_cells=1000
        )
        # Agent can then: Read(result['output_path']) or analyze with scanpy

    Note: Saves data to file. Agent can access file for further analysis.
          File is NOT auto-deleted - user manages cleanup.
    """
    # Initialize OutputManager
    om = OutputManager(server_name='single_cell', tool_name='get_expression_for_genes')
    final_path = om.get_output_path(user_path=output_path, auto_suffix='.h5ad')

    # Safety check: require a value_filter to prevent accidentally querying all cells
    if value_filter is None:
        # Default to a small sample if no filter provided
        value_filter = f"soma_joinid < {max_cells}"

    # Pre-check query size
    count_result = count_cells(value_filter=value_filter)
    if not count_result['within_size_limit']:
        # Query too large - provide guidance
        raise ValueError(
            f"Query would retrieve {count_result['n_cells']:,} cells ({count_result['estimated_size_gb']:.2f} GB), "
            f"exceeding the {count_result['max_size_gb']} GB limit. "
            f"Please apply a more restrictive value_filter. "
            f"Suggestions: filter by tissue, cell_type, disease, or reduce max_cells parameter. "
            f"Current filter: {value_filter}"
        )

    # Retrieve data and save to user-specified file (PERSISTS - not deleted)
    anndata_result = get_anndata(
        output_path=final_path,
        value_filter=value_filter,
        gene_symbols=gene_symbols,
        layer=layer
    )

    # Load to compute summary statistics
    import anndata as ad
    adata = ad.read_h5ad(final_path)

    # Limit cells if needed
    if adata.n_obs > max_cells:
        adata = adata[:max_cells]
        # Re-save the limited version
        adata.write_h5ad(final_path, compression='gzip')

    # Compute summary statistics on expression values
    X = adata.X
    if hasattr(X, 'toarray'):
        X_dense = X.toarray()
    else:
        X_dense = X

    # Get non-zero values for stats
    nonzero_mask = X_dense != 0
    nonzero_values = X_dense[nonzero_mask]

    summary_stats = {
        'mean': float(np.mean(nonzero_values)) if len(nonzero_values) > 0 else 0,
        'median': float(np.median(nonzero_values)) if len(nonzero_values) > 0 else 0,
        'min': float(np.min(nonzero_values)) if len(nonzero_values) > 0 else 0,
        'max': float(np.max(nonzero_values)) if len(nonzero_values) > 0 else 0,
        'percentile_25': float(np.percentile(nonzero_values, 25)) if len(nonzero_values) > 0 else 0,
        'percentile_75': float(np.percentile(nonzero_values, 75)) if len(nonzero_values) > 0 else 0,
        'std': float(np.std(nonzero_values)) if len(nonzero_values) > 0 else 0
    }

    # Get file size
    file_size_bytes = os.path.getsize(final_path)
    file_size_mb = file_size_bytes / (1024**2)

    genes_not_found = [g for g in gene_symbols if g not in adata.var_names]

    # Register output with OutputManager
    om.register_output(
        file_path=final_path,
        query_params={
            'gene_symbols': gene_symbols,
            'value_filter': value_filter,
            'layer': layer,
            'max_cells': max_cells
        },
        n_records=int(adata.n_obs),
        size_mb=file_size_mb,
        additional_metadata={
            'n_genes': int(adata.n_vars),
            'n_nonzero_values': int(np.sum(nonzero_mask)),
            'genes_found': adata.var_names.tolist(),
            'genes_not_found': genes_not_found,
            'summary_stats': summary_stats
        }
    )

    # Return metadata and summary (NO full expression data)
    result = {
        'output_path': final_path,
        'n_cells': int(adata.n_obs),
        'n_genes': int(adata.n_vars),
        'n_nonzero_values': int(np.sum(nonzero_mask)),
        'genes_found': adata.var_names.tolist(),
        'genes_not_found': genes_not_found,
        'summary_stats': summary_stats,
        'size_mb': round(file_size_mb, 2),
        'layer': layer,
        'note': 'Data saved to file. Use Read tool or scanpy to analyze. File persists for reuse.'
    }

    return convert_to_json_serializable(result)


def get_anndata_donor_balanced(
    output_path: str,
    value_filter: str,
    max_cells: int = 20000,
    gene_symbols: Optional[List[str]] = None,
    ensembl_ids: Optional[List[str]] = None,
    obs_columns: Optional[List[str]] = None,
    layer: str = 'raw'
) -> Dict[str, Any]:
    """
    Retrieve an AnnData object with donor-balanced, cell-type-stratified sampling.

    Uses a metadata-first strategy for efficiency:
      1. Query obs metadata only (fast, no expression transfer)
      2. Subsample up to max_cells, balanced across donors within each cell type
      3. Fetch expression data only for selected cells via obs_coords

    This produces data suitable for pseudobulk differential expression (DESeq2),
    where statistical power depends on the number of biological replicates (donors),
    not total cell count.

    **WHEN TO USE**: When you need representative data for DE analysis, gene program
    scoring, or any analysis requiring balanced donor representation. For simple
    expression queries or full unfiltered retrieval, use get_anndata() instead.

    Args:
        output_path: Path to save the .h5ad file
        value_filter: SOMA value filter (REQUIRED). Example: "disease == 'Crohn disease'"
        max_cells: Maximum cells to sample (default 20000). Cells are allocated
                   proportionally across cell types, then balanced across donors
                   within each type.
        gene_symbols: Optional list of gene symbols to include. If None, retrieves
                      all genes (full transcriptome).
        ensembl_ids: Optional list of Ensembl IDs to include.
        obs_columns: Metadata columns to include. Defaults to cell_type, tissue,
                     disease, donor_id, assay, dataset_id, sex, development_stage.
        layer: Expression layer - 'raw' (default) or 'normalized'

    Returns:
        Dictionary with:
        - output_path: Path to saved h5ad file
        - n_cells_total: Total cells matching filter (before sampling)
        - n_cells_sampled: Cells in the output file
        - n_donors: Number of unique donors represented
        - n_cell_types: Number of cell types represented
        - n_genes: Number of genes
        - donor_summary: Dict of donor_id -> cell count
        - cell_type_summary: Top 20 cell types with counts
        - genes_found / genes_not_found: Gene matching results (if gene_symbols provided)
        - file_size_mb: Output file size
    """
    import cellxgene_census

    if layer not in ['raw', 'normalized']:
        raise ValueError(f"layer must be 'raw' or 'normalized', got '{layer}'")

    census = get_census()
    human = census["census_data"]["homo_sapiens"]

    om = OutputManager(server_name='single_cell', tool_name='get_anndata_donor_balanced')
    final_path = om.get_output_path(user_path=output_path, auto_suffix='.h5ad')

    # Default obs columns — include donor_id and dataset_id for DE
    if obs_columns is None:
        obs_columns = ["cell_type", "tissue", "disease", "donor_id",
                       "assay", "dataset_id", "sex", "development_stage"]
    if "donor_id" not in obs_columns:
        obs_columns.append("donor_id")
    if "cell_type" not in obs_columns:
        obs_columns.append("cell_type")

    # ---- Step 1: Metadata-only query (fast) ----
    meta_columns = list(set(["soma_joinid", "cell_type", "donor_id"] + obs_columns))
    obs_df = human.obs.read(
        value_filter=value_filter,
        column_names=meta_columns,
    ).concat().to_pandas()

    # Remove unused Census categoricals
    for col in obs_df.columns:
        if hasattr(obs_df[col], 'cat'):
            obs_df[col] = obs_df[col].cat.remove_unused_categories()

    n_total = len(obs_df)
    if n_total == 0:
        return convert_to_json_serializable({
            'output_path': final_path,
            'n_cells_total': 0,
            'n_cells_sampled': 0,
            'n_donors': 0,
            'n_cell_types': 0,
            'error': f'No cells found for filter: {value_filter}'
        })

    # ---- Step 2: Donor-balanced, cell-type-stratified subsample ----
    rng = np.random.RandomState(42)

    if n_total <= max_cells:
        selected_ids = obs_df['soma_joinid'].values
    else:
        ct_counts = obs_df['cell_type'].value_counts()
        ct_counts = ct_counts[ct_counts > 0]
        n_types = len(ct_counts)

        min_per_type = min(20, max_cells // max(n_types, 1))
        remaining = max(0, max_cells - min_per_type * n_types)

        selected_ids_list = []
        for ct, ct_count in ct_counts.items():
            ct_df = obs_df[obs_df['cell_type'] == ct]
            n_proportional = int(remaining * (ct_count / n_total))
            budget = min(ct_count, min_per_type + n_proportional)
            budget = max(budget, min(ct_count, min_per_type))

            # Balance across donors within this cell type
            donors = ct_df['donor_id'].value_counts()
            donors = donors[donors > 0]
            n_donors_ct = len(donors)

            if n_donors_ct == 0 or budget == 0:
                continue

            per_donor = max(1, budget // n_donors_ct)
            donor_samples = []
            for donor, _ in donors.items():
                donor_df = ct_df[ct_df['donor_id'] == donor]
                n_take = min(len(donor_df), per_donor)
                sampled = rng.choice(
                    donor_df['soma_joinid'].values, size=n_take, replace=False
                )
                donor_samples.append(sampled)

            combined = np.concatenate(donor_samples)

            # Fill remaining budget from undersampled donors
            if len(combined) < budget:
                remaining_pool = np.setdiff1d(ct_df['soma_joinid'].values, combined)
                n_extra = min(budget - len(combined), len(remaining_pool))
                if n_extra > 0:
                    extra = rng.choice(remaining_pool, size=n_extra, replace=False)
                    combined = np.concatenate([combined, extra])

            if len(combined) > budget:
                combined = rng.choice(combined, size=budget, replace=False)

            selected_ids_list.append(combined)

        selected_ids = np.concatenate(selected_ids_list)
        if len(selected_ids) > max_cells:
            selected_ids = rng.choice(selected_ids, size=max_cells, replace=False)

    # ---- Step 3: Fetch expression for selected cells only ----
    var_value_filter = None
    if gene_symbols or ensembl_ids:
        filter_parts = []
        if gene_symbols:
            symbols_str = "', '".join(gene_symbols)
            filter_parts.append(f"feature_name in ['{symbols_str}']")
        if ensembl_ids:
            ids_str = "', '".join(ensembl_ids)
            filter_parts.append(f"feature_id in ['{ids_str}']")
        var_value_filter = " or ".join(filter_parts)

    adata = cellxgene_census.get_anndata(
        census=census,
        organism=ORGANISM,
        measurement_name='RNA',
        X_name=layer,
        obs_coords=selected_ids.tolist(),
        var_value_filter=var_value_filter,
        column_names={"obs": obs_columns} if obs_columns else None,
    )

    # Fix categoricals in the result
    for col in adata.obs.columns:
        if hasattr(adata.obs[col], 'cat'):
            adata.obs[col] = adata.obs[col].cat.remove_unused_categories()

    # ---- Step 4: Save ----
    adata.write_h5ad(final_path, compression='gzip')

    # ---- Step 5: Build return metadata ----
    file_size_mb = os.path.getsize(final_path) / (1024**2)

    donor_counts = adata.obs['donor_id'].value_counts().to_dict()
    ct_counts_result = adata.obs['cell_type'].value_counts().head(20).to_dict()

    genes_found = adata.var_names.tolist() if gene_symbols else []
    genes_not_found = []
    if gene_symbols:
        genes_not_found = [g for g in gene_symbols if g not in adata.var_names]

    om.register_output(
        file_path=final_path,
        query_params={
            'value_filter': value_filter,
            'max_cells': max_cells,
            'gene_symbols': gene_symbols,
            'layer': layer,
            'sampling': 'donor_balanced'
        },
        n_records=int(adata.n_obs),
        size_mb=file_size_mb,
        additional_metadata={
            'n_cells_total': n_total,
            'n_donors': int(adata.obs['donor_id'].nunique()),
            'n_cell_types': int(adata.obs['cell_type'].nunique()),
            'sampling_method': 'donor_balanced_cell_type_stratified'
        }
    )

    result = {
        'output_path': final_path,
        'n_cells_total': n_total,
        'n_cells_sampled': int(adata.n_obs),
        'n_donors': int(adata.obs['donor_id'].nunique()),
        'n_cell_types': int(adata.obs['cell_type'].nunique()),
        'n_genes': int(adata.n_vars),
        'donor_summary': donor_counts,
        'cell_type_summary': ct_counts_result,
        'file_size_mb': round(file_size_mb, 2),
        'layer': layer,
        'note': (
            'Donor-balanced sampling: cells distributed evenly across donors '
            'within each cell type. Suitable for pseudobulk DE (DESeq2). '
            'File persists for reuse.'
        )
    }

    if gene_symbols:
        result['genes_found'] = genes_found
        result['genes_not_found'] = genes_not_found

    return convert_to_json_serializable(result)
