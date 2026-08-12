"""
Association MCP Tools
Tool implementations for target-disease association queries
"""

import sys
from pathlib import Path

# Add project root to path so we can import src modules
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader
from src.utils.output_manager import OutputManager
import logging
import numpy as np
import pandas as pd
from typing import List, Optional
import os

logger = logging.getLogger(__name__)

# ✅ CRITICAL PATTERN: This lazy loading pattern is REQUIRED for all MCP servers
_data_loader = None

def _get_loader():
    """Get or initialize the data loader (lazy initialization)"""
    global _data_loader
    if _data_loader is None:
        import os
        preload = os.environ.get('PRELOAD_MCP_DATA', '0') == '1'
        _data_loader = get_data_loader(preload_all=preload)
    return _data_loader


def convert_to_native_types(obj):
    """Recursively convert numpy/pandas types to native Python types for JSON serialization"""
    if isinstance(obj, dict):
        return {k: convert_to_native_types(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_to_native_types(item) for item in obj]
    elif isinstance(obj, np.ndarray):
        return convert_to_native_types(obj.tolist())
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.str_):
        return str(obj)
    elif pd.isna(obj):
        return None
    else:
        return obj


def query_associations(
    output_path: str,
    target_id: Optional[str] = None,
    disease_id: Optional[str] = None,
    min_score: float = 0.0,
    include_indirect: bool = False,
    limit: int = 100
) -> dict:
    """
    Query target-disease associations with optional filters and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        target_id: Filter by Ensembl gene ID (e.g., 'ENSG00000130203')
        disease_id: Filter by EFO disease ID (e.g., 'EFO_0000685')
        min_score: Minimum association score (0-1, default: 0.0)
        include_indirect: Include indirect associations from disease ontology (default: False)
        limit: Maximum number of results (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - count: Number of associations found
        - size_mb: File size in MB
        - summary_stats: Score statistics (mean, median, max, min)
        - top_associations: Top 10 associations by score

    Example:
        >>> # Get associations for APOE
        >>> result = query_associations(
        ...     output_path='apoe_assocs.parquet',
        ...     target_id="ENSG00000130203",
        ...     min_score=0.5
        ... )
        >>>
        >>> # Get targets for Alzheimer's disease
        >>> result = query_associations(
        ...     output_path='alzheimers_targets.parquet',
        ...     disease_id="EFO_0000249",
        ...     min_score=0.3
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='association', tool_name='query_associations')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        # Load appropriate dataset
        if include_indirect:
            associations = _get_loader().get_dataset("association_by_overall_indirect")
            assoc_type = "indirect"
        else:
            associations = _get_loader().get_dataset("association_overall_direct")
            assoc_type = "direct"

        # Apply filters
        results_df = associations

        if target_id:
            results_df = results_df[results_df['targetId'] == target_id]

        if disease_id:
            results_df = results_df[results_df['diseaseId'] == disease_id]

        if min_score > 0:
            results_df = results_df[results_df['score'] >= min_score]

        if results_df.empty:
            return {
                "success": True,
                "target_id": target_id,
                "disease_id": disease_id,
                "min_score": min_score,
                "include_indirect": include_indirect,
                "count": 0,
                "message": "No associations found matching criteria"
            }

        # Sort by score descending
        results_df = results_df.sort_values('score', ascending=False)

        # Limit results
        results_df = results_df.head(limit)

        # Add association type
        results_df = results_df.copy()
        results_df['association_type'] = assoc_type

        # Save to parquet
        results_df.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {
            'mean_score': float(results_df['score'].mean()),
            'median_score': float(results_df['score'].median()),
            'max_score': float(results_df['score'].max()),
            'min_score': float(results_df['score'].min())
        }

        # Get top 10 for preview
        top_10 = results_df.head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'target_id': target_id,
                'disease_id': disease_id,
                'min_score': min_score,
                'include_indirect': include_indirect,
                'limit': limit
            },
            n_records=len(results_df),
            size_mb=file_size_mb,
            additional_metadata={
                'association_type': assoc_type,
                'summary_stats': summary_stats
            }
        )

        return {
            "success": True,
            "output_path": final_path,
            "target_id": target_id,
            "disease_id": disease_id,
            "min_score": min_score,
            "include_indirect": include_indirect,
            "count": len(results_df),
            "limit": limit,
            "size_mb": round(file_size_mb, 2),
            "summary_stats": summary_stats,
            "top_associations": top_10,
            "note": "Associations saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        }

    except Exception as e:
        logger.error(f"Error querying associations: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def get_associations_for_disease(
    output_path: str,
    disease_id: str,
    min_score: float = 0.0,
    include_indirect: bool = False,
    limit: int = 100
) -> dict:
    """
    Get all target-disease associations for a specific disease and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        disease_id: EFO disease ID (e.g., 'EFO_0000249' for Alzheimer's)
        min_score: Minimum association score (0-1, default: 0.0)
        include_indirect: Include indirect evidence (default: False)
        limit: Maximum number of targets to return (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - disease_id: Input disease ID
        - count: Number of associated targets
        - size_mb: File size in MB
        - summary_stats: Score statistics
        - top_associations: Top 10 targets by score

    Example:
        >>> result = get_associations_for_disease(
        ...     output_path='alzheimers_targets.parquet',
        ...     disease_id="EFO_0000249",
        ...     min_score=0.3
        ... )
        >>> print(f"Found {result['count']} targets for Alzheimer's disease")

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    return query_associations(
        output_path=output_path,
        disease_id=disease_id,
        min_score=min_score,
        include_indirect=include_indirect,
        limit=limit
    )


def get_associations_for_target(
    output_path: str,
    target_id: str,
    min_score: float = 0.0,
    include_indirect: bool = False,
    limit: int = 100
) -> dict:
    """
    Get all target-disease associations for a specific target and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203' for APOE)
        min_score: Minimum association score (0-1, default: 0.0)
        include_indirect: Include indirect evidence (default: False)
        limit: Maximum number of diseases to return (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - target_id: Input gene ID
        - count: Number of associated diseases
        - size_mb: File size in MB
        - summary_stats: Score statistics
        - top_associations: Top 10 diseases by score

    Example:
        >>> result = get_associations_for_target(
        ...     output_path='apoe_diseases.parquet',
        ...     target_id="ENSG00000130203",
        ...     min_score=0.5
        ... )
        >>> print(f"APOE is associated with {result['count']} diseases")

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    return query_associations(
        output_path=output_path,
        target_id=target_id,
        min_score=min_score,
        include_indirect=include_indirect,
        limit=limit
    )


def compare_direct_indirect(
    output_path: str,
    target_id: Optional[str] = None,
    disease_id: Optional[str] = None,
    min_score: float = 0.0,
    limit: int = 50
) -> dict:
    """
    Compare direct vs indirect associations for a target or disease and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        target_id: Filter by Ensembl gene ID
        disease_id: Filter by EFO disease ID
        min_score: Minimum score threshold (default: 0.0)
        limit: Maximum results per category (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - direct_count: Number of direct associations
        - indirect_count: Number of indirect associations
        - unique_to_direct_count: Count of associations only in direct
        - unique_to_indirect_count: Count of associations only in indirect
        - shared_count: Count of associations in both
        - size_mb: File size in MB
        - comparison_summary: Statistics about overlap

    Example:
        >>> result = compare_direct_indirect(
        ...     output_path='alzheimers_comparison.parquet',
        ...     disease_id="EFO_0000249"
        ... )
        >>> print(f"Direct: {result['direct_count']}, Indirect: {result['indirect_count']}")

    Note: File contains all associations with 'type' column ('direct', 'indirect', or 'both').
          Use pandas.read_parquet() to load full data. File persists for reuse.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='association', tool_name='compare_direct_indirect')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        # Load both datasets
        direct_df = _get_loader().get_dataset("association_overall_direct")
        indirect_df = _get_loader().get_dataset("association_by_overall_indirect")

        # Apply filters to both
        if target_id:
            direct_df = direct_df[direct_df['targetId'] == target_id]
            indirect_df = indirect_df[indirect_df['targetId'] == target_id]

        if disease_id:
            direct_df = direct_df[direct_df['diseaseId'] == disease_id]
            indirect_df = indirect_df[indirect_df['diseaseId'] == disease_id]

        if min_score > 0:
            direct_df = direct_df[direct_df['score'] >= min_score]
            indirect_df = indirect_df[indirect_df['score'] >= min_score]

        # Sort and limit
        direct_df = direct_df.sort_values('score', ascending=False).head(limit)
        indirect_df = indirect_df.sort_values('score', ascending=False).head(limit)

        # Create comparison sets
        direct_pairs = set(zip(direct_df['targetId'], direct_df['diseaseId']))
        indirect_pairs = set(zip(indirect_df['targetId'], indirect_df['diseaseId']))

        only_direct = direct_pairs - indirect_pairs
        only_indirect = indirect_pairs - direct_pairs
        shared = direct_pairs & indirect_pairs

        # Tag rows with type
        direct_df = direct_df.copy()
        indirect_df = indirect_df.copy()

        direct_df['comparison_type'] = direct_df.apply(
            lambda row: 'both' if (row['targetId'], row['diseaseId']) in shared else 'direct_only',
            axis=1
        )
        indirect_df['comparison_type'] = indirect_df.apply(
            lambda row: 'both' if (row['targetId'], row['diseaseId']) in shared else 'indirect_only',
            axis=1
        )

        # Combine into single dataframe
        combined_df = pd.concat([direct_df, indirect_df], ignore_index=True)
        combined_df = combined_df.drop_duplicates(subset=['targetId', 'diseaseId'], keep='first')

        # Save to parquet
        combined_df.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'target_id': target_id,
                'disease_id': disease_id,
                'min_score': min_score,
                'limit': limit
            },
            n_records=len(combined_df),
            size_mb=file_size_mb,
            additional_metadata={
                'direct_count': len(direct_df),
                'indirect_count': len(indirect_df),
                'unique_to_direct_count': len(only_direct),
                'unique_to_indirect_count': len(only_indirect),
                'shared_count': len(shared)
            }
        )

        return {
            "success": True,
            "output_path": final_path,
            "target_id": target_id,
            "disease_id": disease_id,
            "min_score": min_score,
            "direct_count": len(direct_df),
            "indirect_count": len(indirect_df),
            "unique_to_direct_count": len(only_direct),
            "unique_to_indirect_count": len(only_indirect),
            "shared_count": len(shared),
            "size_mb": round(file_size_mb, 2),
            "comparison_summary": {
                'total_unique_associations': len(combined_df),
                'overlap_percentage': round(len(shared) / max(len(direct_pairs), 1) * 100, 1)
            },
            "note": "Comparison saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        }

    except Exception as e:
        logger.error(f"Error comparing direct/indirect: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def filter_by_datatype(
    output_path: str,
    target_id: Optional[str] = None,
    disease_id: Optional[str] = None,
    datatype: str = None,
    min_score: float = 0.0,
    include_indirect: bool = False,
    limit: int = 100
) -> dict:
    """
    Filter associations by evidence datatype and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        target_id: Filter by Ensembl gene ID
        disease_id: Filter by EFO disease ID
        datatype: Evidence datatype, one of:
            - 'genetic_association' (GWAS, gene burden, somatic)
            - 'known_drug' (ChEMBL drug evidence)
            - 'affected_pathway' (Pathway evidence)
            - 'rna_expression' (Expression-based)
            - 'literature' (Text mining)
            - 'animal_model' (Mouse phenotypes)
            - 'somatic_mutation' (Cancer mutations)
        min_score: Minimum datatype-specific score (default: 0.0)
        include_indirect: Include indirect associations (default: False)
        limit: Maximum results (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - datatype: Filtered datatype
        - count: Number of associations
        - size_mb: File size in MB
        - summary_stats: Score statistics
        - top_associations: Top 10 associations by score

    Example:
        >>> # Get genetic associations for a disease
        >>> result = filter_by_datatype(
        ...     output_path='alzheimers_genetic.parquet',
        ...     disease_id="EFO_0000249",
        ...     datatype="genetic_association",
        ...     min_score=0.3
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='association', tool_name='filter_by_datatype')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        # Load datatype-specific dataset
        if include_indirect:
            associations = _get_loader().get_dataset("association_by_datatype_indirect")
        else:
            associations = _get_loader().get_dataset("association_by_datatype_direct")

        # Apply filters
        results_df = associations

        if datatype:
            results_df = results_df[results_df['datatypeId'] == datatype]

        if target_id:
            results_df = results_df[results_df['targetId'] == target_id]

        if disease_id:
            results_df = results_df[results_df['diseaseId'] == disease_id]

        if min_score > 0:
            results_df = results_df[results_df['score'] >= min_score]

        if results_df.empty:
            return {
                "success": True,
                "datatype": datatype,
                "target_id": target_id,
                "disease_id": disease_id,
                "count": 0,
                "message": "No associations found for this datatype"
            }

        # Sort by score descending
        results_df = results_df.sort_values('score', ascending=False)

        # Limit results
        results_df = results_df.head(limit)

        # Save to parquet
        results_df.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {
            'mean_score': float(results_df['score'].mean()),
            'median_score': float(results_df['score'].median()),
            'max_score': float(results_df['score'].max()),
            'min_score': float(results_df['score'].min())
        }

        # Get top 10 for preview
        top_10 = results_df.head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'target_id': target_id,
                'disease_id': disease_id,
                'datatype': datatype,
                'min_score': min_score,
                'include_indirect': include_indirect,
                'limit': limit
            },
            n_records=len(results_df),
            size_mb=file_size_mb,
            additional_metadata={
                'datatype': datatype,
                'summary_stats': summary_stats
            }
        )

        return {
            "success": True,
            "output_path": final_path,
            "datatype": datatype,
            "target_id": target_id,
            "disease_id": disease_id,
            "include_indirect": include_indirect,
            "count": len(results_df),
            "limit": limit,
            "size_mb": round(file_size_mb, 2),
            "summary_stats": summary_stats,
            "top_associations": top_10,
            "note": "Associations saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        }

    except Exception as e:
        logger.error(f"Error filtering by datatype: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def filter_by_datasource(
    output_path: str,
    target_id: Optional[str] = None,
    disease_id: Optional[str] = None,
    datasource: str = None,
    min_score: float = 0.0,
    include_indirect: bool = False,
    limit: int = 100
) -> dict:
    """
    Filter associations by specific data source and save to parquet file

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        target_id: Filter by Ensembl gene ID
        disease_id: Filter by EFO disease ID
        datasource: Data source ID, examples:
            - 'eva' (ClinVar)
            - 'gwas_catalog' (GWAS Catalog)
            - 'gene_burden' (Gene burden studies)
            - 'chembl' (ChEMBL drugs)
            - 'intogen' (Cancer drivers)
            - 'expression_atlas' (Expression data)
            - and many more...
        min_score: Minimum source-specific score (default: 0.0)
        include_indirect: Include indirect associations (default: False)
        limit: Maximum results (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - datasource: Filtered data source
        - count: Number of associations
        - size_mb: File size in MB
        - summary_stats: Score statistics
        - top_associations: Top 10 associations by score

    Example:
        >>> # Get GWAS Catalog associations for a target
        >>> result = filter_by_datasource(
        ...     output_path='apoe_gwas.parquet',
        ...     target_id="ENSG00000130203",
        ...     datasource="gwas_catalog"
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='association', tool_name='filter_by_datasource')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        # Load datasource-specific dataset
        if include_indirect:
            associations = _get_loader().get_dataset("association_by_datasource_indirect")
        else:
            associations = _get_loader().get_dataset("association_by_datasource_direct")

        # Apply filters
        results_df = associations

        if datasource:
            results_df = results_df[results_df['datasourceId'] == datasource]

        if target_id:
            results_df = results_df[results_df['targetId'] == target_id]

        if disease_id:
            results_df = results_df[results_df['diseaseId'] == disease_id]

        if min_score > 0:
            results_df = results_df[results_df['score'] >= min_score]

        if results_df.empty:
            return {
                "success": True,
                "datasource": datasource,
                "target_id": target_id,
                "disease_id": disease_id,
                "count": 0,
                "message": "No associations found for this data source"
            }

        # Sort by score descending
        results_df = results_df.sort_values('score', ascending=False)

        # Limit results
        results_df = results_df.head(limit)

        # Save to parquet
        results_df.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {
            'mean_score': float(results_df['score'].mean()),
            'median_score': float(results_df['score'].median()),
            'max_score': float(results_df['score'].max()),
            'min_score': float(results_df['score'].min())
        }

        # Get top 10 for preview
        top_10 = results_df.head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'target_id': target_id,
                'disease_id': disease_id,
                'datasource': datasource,
                'min_score': min_score,
                'include_indirect': include_indirect,
                'limit': limit
            },
            n_records=len(results_df),
            size_mb=file_size_mb,
            additional_metadata={
                'datasource': datasource,
                'summary_stats': summary_stats
            }
        )

        return {
            "success": True,
            "output_path": final_path,
            "datasource": datasource,
            "target_id": target_id,
            "disease_id": disease_id,
            "include_indirect": include_indirect,
            "count": len(results_df),
            "limit": limit,
            "size_mb": round(file_size_mb, 2),
            "summary_stats": summary_stats,
            "top_associations": top_10,
            "note": "Associations saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        }

    except Exception as e:
        logger.error(f"Error filtering by datasource: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def query_evidence(
    target_id: str = None,
    disease_id: str = None,
    datasource_id: str = None,
    datatype_id: str = None,
    min_score: float = None,
    min_year: int = None,
    max_year: int = None,
    require_pubmed: bool = False,
    limit: int = 50
) -> dict:
    """
    Query detailed evidence strings linking targets to diseases

    Evidence strings provide granular support for target-disease associations,
    including experimental data, clinical trials, genetics, literature, etc.

    Args:
        target_id: Ensembl gene ID
        disease_id: Disease/phenotype ID (EFO)
        datasource_id: Filter by data source (e.g., 'chembl', 'europepmc')
        datatype_id: Filter by evidence type (e.g., 'genetic_association', 'known_drug')
        min_score: Minimum evidence score (0-1)
        min_year: Minimum publication year (NEW - e.g., 2020 for recent evidence)
        max_year: Maximum publication year (NEW - e.g., 2023)
        require_pubmed: Only return evidence with PubMed citations (NEW - default: False)
        limit: Maximum number of evidence strings

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of evidence strings
        - filters_applied: Dictionary of filters that were applied
        - evidence: List of evidence records with:
            - id: Evidence string ID
            - targetId, diseaseId: Target-disease pair
            - score: Evidence score
            - datasourceId, datatypeId: Source and type
            - literature: Associated publications (PMIDs)
            - publicationYear: Publication year (if available)
            - studyId, variantId: Associated study/variant

    Example:
        >>> # Get recent genetic evidence for a target-disease pair
        >>> evidence = query_evidence(
        ...     target_id="ENSG00000130203",
        ...     disease_id="EFO_0000249",
        ...     datatype_id="genetic_association",
        ...     min_score=0.5,
        ...     min_year=2020,
        ...     require_pubmed=True
        ... )
    """
    try:
        evidence = _get_loader().get_dataset("evidence")

        # Build filter mask
        mask = pd.Series([True] * len(evidence))
        filters_applied = {}

        if target_id:
            mask &= evidence['targetId'] == target_id
            filters_applied['target_id'] = target_id

        if disease_id:
            mask &= evidence['diseaseId'] == disease_id
            filters_applied['disease_id'] = disease_id

        if datasource_id:
            mask &= evidence['datasourceId'] == datasource_id
            filters_applied['datasource_id'] = datasource_id

        if datatype_id:
            mask &= evidence['datatypeId'] == datatype_id
            filters_applied['datatype_id'] = datatype_id

        if min_score is not None:
            mask &= evidence['score'] >= min_score
            filters_applied['min_score'] = min_score

        # NEW: Year filtering
        if min_year is not None:
            if 'publicationYear' in evidence.columns:
                mask &= (evidence['publicationYear'] >= min_year) | (evidence['publicationYear'].isna())
                filters_applied['min_year'] = min_year

        if max_year is not None:
            if 'publicationYear' in evidence.columns:
                mask &= (evidence['publicationYear'] <= max_year) | (evidence['publicationYear'].isna())
                filters_applied['max_year'] = max_year

        # NEW: PubMed requirement
        if require_pubmed:
            if 'literature' in evidence.columns:
                # Filter to evidence with non-null, non-empty literature field
                def has_literature(lit):
                    if lit is None or (isinstance(lit, float) and pd.isna(lit)):
                        return False
                    if isinstance(lit, (list, tuple, np.ndarray)):
                        return len(lit) > 0
                    return False

                mask &= evidence['literature'].apply(has_literature)
                filters_applied['require_pubmed'] = True

        result_df = evidence[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "filters_applied": filters_applied,
                "evidence": [],
                "message": "No evidence found matching criteria"
            }

        # Select key fields to avoid returning all 90 columns
        key_fields = [
            'id', 'targetId', 'diseaseId', 'score', 'datasourceId', 'datatypeId',
            'literature', 'publicationYear', 'studyId', 'variantId'
        ]
        available_fields = [f for f in key_fields if f in result_df.columns]
        result_df = result_df[available_fields]

        evidence_list = result_df.to_dict('records')
        evidence_list = convert_to_native_types(evidence_list)

        return {
            "success": True,
            "count": len(evidence_list),
            "limit": limit,
            "filters_applied": filters_applied,
            "evidence": evidence_list
        }

    except Exception as e:
        logger.error(f"Error querying evidence: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_evidence_by_publication(pmid: str, limit: int = 50) -> dict:
    """
    Get evidence strings associated with a specific publication

    Args:
        pmid: PubMed ID
        limit: Maximum number of evidence strings

    Returns:
        Dictionary with evidence strings citing this publication

    Example:
        >>> evidence = get_evidence_by_publication("25533513")
    """
    try:
        evidence = _get_loader().get_dataset("evidence")

        # Literature field is a list of PMIDs
        def has_pmid(lit_list):
            if lit_list is None or (isinstance(lit_list, float) and pd.isna(lit_list)):
                return False
            if isinstance(lit_list, (list, tuple)):
                return pmid in lit_list
            return False

        mask = evidence['literature'].apply(has_pmid)
        result_df = evidence[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "pmid": pmid,
                "count": 0,
                "evidence": [],
                "message": "No evidence found for this publication"
            }

        # Select key fields
        key_fields = ['id', 'targetId', 'diseaseId', 'score', 'datasourceId', 'datatypeId']
        available_fields = [f for f in key_fields if f in result_df.columns]
        result_df = result_df[available_fields]

        evidence_list = result_df.to_dict('records')
        evidence_list = convert_to_native_types(evidence_list)

        return {
            "success": True,
            "pmid": pmid,
            "count": len(evidence_list),
            "evidence": evidence_list
        }

    except Exception as e:
        logger.error(f"Error getting evidence by publication: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def search_literature(
    pmid: str = None,
    year: int = None,
    keyword_id: str = None,
    limit: int = 50
) -> dict:
    """
    Search publication metadata

    Args:
        pmid: Search for specific PubMed ID
        year: Filter by publication year
        keyword_id: Filter by keyword/topic ID
        limit: Maximum number of results

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of publications
        - publications: List with:
            - pmid: PubMed ID
            - pmcid: PubMed Central ID
            - date: Publication date
            - year, month, day: Date components
            - keywordId: Associated keywords
            - relevance: Keyword relevance score
            - keywordType: Type of keyword

    Example:
        >>> # Search by PMID
        >>> pub = search_literature(pmid="25533513")
        >>> # Search by year
        >>> pubs = search_literature(year=2020, limit=100)
    """
    try:
        literature = _get_loader().get_dataset("literature")

        # Build filter mask
        mask = pd.Series([True] * len(literature))

        if pmid:
            mask &= literature['pmid'] == pmid

        if year is not None:
            mask &= literature['year'] == year

        if keyword_id:
            mask &= literature['keywordId'] == keyword_id

        result_df = literature[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "publications": [],
                "message": "No publications found matching criteria"
            }

        pubs_list = result_df.to_dict('records')
        pubs_list = convert_to_native_types(pubs_list)

        return {
            "success": True,
            "count": len(pubs_list),
            "limit": limit,
            "publications": pubs_list
        }

    except Exception as e:
        logger.error(f"Error searching literature: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def find_similar_entities(
    entity_id: str,
    category: Optional[str] = None,
    top_k: int = 20
) -> dict:
    """
    Find entities with similar semantic embeddings from biomedical literature

    Uses pre-trained 100-dimensional vector embeddings to compute semantic similarity
    via cosine similarity. Enables discovery of similar diseases, genes, or drugs
    based on their co-occurrence patterns in scientific literature.

    Args:
        entity_id: Entity identifier to find similar entities for. Examples:
            - Gene/target: 'ENSG00000130203' (APOE)
            - Disease: 'EFO_0000249', 'MONDO_0004975', 'HP_0001250'
            - Drug: 'CHEMBL1201201' (aspirin)
        category: Filter results by category (optional):
            - 'target' - genes/proteins
            - 'disease' - diseases and phenotypes
            - 'drug' - chemical compounds
            If None, searches across all categories
        top_k: Number of most similar entities to return (default: 20)

    Returns:
        Dictionary with:
        - success: Boolean
        - query_entity_id: Input entity ID
        - query_category: Category of input entity
        - count: Number of similar entities found
        - similar_entities: List of similar entities with:
            - entity_id: Entity identifier
            - category: Entity category
            - similarity: Cosine similarity score (0-1, higher = more similar)
            - norm: Vector magnitude

    Example:
        >>> # Find diseases similar to Alzheimer's
        >>> results = find_similar_entities("EFO_0000249", category="disease", top_k=10)
        >>>
        >>> # Find targets similar to APOE
        >>> results = find_similar_entities("ENSG00000130203", category="target")
        >>>
        >>> # Find drugs similar to aspirin (any category)
        >>> results = find_similar_entities("CHEMBL25", top_k=20)
    """
    try:
        vectors = _get_loader().get_dataset("literature_vector")

        # Find query entity
        query_row = vectors[vectors['word'] == entity_id]

        if query_row.empty:
            return {
                "success": False,
                "error": f"Entity '{entity_id}' not found in literature vector dataset",
                "message": "Entity may not have literature embedding. Check entity ID format."
            }

        query_row = query_row.iloc[0]
        query_vector = query_row['vector']
        query_category = query_row['category']

        # Filter by category if specified
        if category:
            candidate_vectors = vectors[vectors['category'] == category]
        else:
            candidate_vectors = vectors

        # Remove query entity from candidates
        candidate_vectors = candidate_vectors[candidate_vectors['word'] != entity_id]

        if candidate_vectors.empty:
            return {
                "success": True,
                "query_entity_id": entity_id,
                "query_category": query_category,
                "count": 0,
                "similar_entities": [],
                "message": f"No other entities found in category '{category}'"
            }

        # Compute cosine similarity: cos(a,b) = a·b / (||a|| ||b||)
        # Since vectors are stored pre-normalized by their norms, we can compute dot product directly
        similarities = []
        query_norm = query_row['norm']

        for idx, row in candidate_vectors.iterrows():
            candidate_vector = row['vector']
            candidate_norm = row['norm']

            # Cosine similarity = dot product / (norm_a * norm_b)
            dot_product = np.dot(query_vector, candidate_vector)
            cosine_sim = dot_product / (query_norm * candidate_norm)

            similarities.append({
                'entity_id': row['word'],
                'category': row['category'],
                'similarity': float(cosine_sim),  # Convert to native float
                'norm': float(candidate_norm)
            })

        # Sort by similarity descending
        similarities.sort(key=lambda x: x['similarity'], reverse=True)

        # Take top k
        top_similar = similarities[:top_k]

        return {
            "success": True,
            "query_entity_id": entity_id,
            "query_category": query_category,
            "filter_category": category,
            "count": len(top_similar),
            "similar_entities": top_similar
        }

    except Exception as e:
        logger.error(f"Error finding similar entities: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def compute_entity_similarity(entity_a: str, entity_b: str) -> dict:
    """
    Compute semantic similarity between two specific entities

    Calculates cosine similarity between the literature-derived embeddings of two
    entities. Higher scores indicate entities that co-occur in similar contexts
    in biomedical literature.

    Args:
        entity_a: First entity ID (e.g., 'ENSG00000130203' for APOE)
        entity_b: Second entity ID (e.g., 'EFO_0000249' for Alzheimer's)

    Returns:
        Dictionary with:
        - success: Boolean
        - entity_a: First entity ID
        - entity_a_category: Category of first entity
        - entity_b: Second entity ID
        - entity_b_category: Category of second entity
        - similarity: Cosine similarity score (0-1)
        - interpretation: Text description of similarity level

    Interpretation:
        - > 0.8: Very high similarity
        - 0.6-0.8: High similarity
        - 0.4-0.6: Moderate similarity
        - 0.2-0.4: Low similarity
        - < 0.2: Very low/no similarity

    Example:
        >>> # Check similarity between APOE and Alzheimer's
        >>> sim = compute_entity_similarity("ENSG00000130203", "EFO_0000249")
        >>> print(f"Similarity: {sim['similarity']:.3f} - {sim['interpretation']}")
        >>>
        >>> # Compare two drugs
        >>> sim = compute_entity_similarity("CHEMBL25", "CHEMBL1201201")
    """
    try:
        vectors = _get_loader().get_dataset("literature_vector")

        # Find both entities
        row_a = vectors[vectors['word'] == entity_a]
        row_b = vectors[vectors['word'] == entity_b]

        if row_a.empty:
            return {
                "success": False,
                "error": f"Entity '{entity_a}' not found in literature vector dataset"
            }

        if row_b.empty:
            return {
                "success": False,
                "error": f"Entity '{entity_b}' not found in literature vector dataset"
            }

        row_a = row_a.iloc[0]
        row_b = row_b.iloc[0]

        vector_a = row_a['vector']
        vector_b = row_b['vector']
        norm_a = row_a['norm']
        norm_b = row_b['norm']

        # Compute cosine similarity
        dot_product = np.dot(vector_a, vector_b)
        cosine_sim = dot_product / (norm_a * norm_b)

        # Interpret similarity level
        if cosine_sim > 0.8:
            interpretation = "Very high similarity"
        elif cosine_sim > 0.6:
            interpretation = "High similarity"
        elif cosine_sim > 0.4:
            interpretation = "Moderate similarity"
        elif cosine_sim > 0.2:
            interpretation = "Low similarity"
        else:
            interpretation = "Very low/no similarity"

        return {
            "success": True,
            "entity_a": entity_a,
            "entity_a_category": row_a['category'],
            "entity_b": entity_b,
            "entity_b_category": row_b['category'],
            "similarity": float(cosine_sim),
            "interpretation": interpretation
        }

    except Exception as e:
        logger.error(f"Error computing entity similarity: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def list_available_datatypes(include_counts: bool = True) -> dict:
    """
    List all available evidence datatypes with optional association counts

    Evidence datatypes represent broad categories of evidence linking targets
    to diseases. Understanding available datatypes is essential for filtering
    and analyzing associations effectively.

    Args:
        include_counts: Include number of associations per datatype (default: True)

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of distinct datatypes
        - datatypes: List of datatype information with:
            - datatype_id: Datatype identifier
            - description: Human-readable description
            - direct_associations: Count in direct dataset (if include_counts=True)
            - indirect_associations: Count in indirect dataset (if include_counts=True)

    Example:
        >>> # List all datatypes with counts
        >>> datatypes = list_available_datatypes()
        >>> for dt in datatypes['datatypes']:
        ...     print(f"{dt['datatype_id']}: {dt['direct_associations']} associations")
        >>>
        >>> # Just list datatypes without counts (faster)
        >>> datatypes = list_available_datatypes(include_counts=False)
    """
    try:
        # Datatype descriptions
        datatype_descriptions = {
            'genetic_association': 'GWAS, gene burden, somatic mutations - genetic evidence linking genes to diseases',
            'known_drug': 'ChEMBL drug data - approved and clinical-stage drugs',
            'affected_pathway': 'Pathway-based evidence - gene pathway perturbations in disease',
            'rna_expression': 'Expression-based evidence - differential gene expression',
            'literature': 'Text mining evidence - co-mentions in scientific literature',
            'animal_model': 'Mouse phenotype evidence - knockout/mutation models',
            'somatic_mutation': 'Cancer somatic mutation evidence - driver genes'
        }

        if include_counts:
            # Load datatype datasets
            direct_assocs = _get_loader().get_dataset("association_by_datatype_direct")
            indirect_assocs = _get_loader().get_dataset("association_by_datatype_indirect")

            # Get unique datatypes and their counts
            direct_counts = direct_assocs['datatypeId'].value_counts().to_dict()
            indirect_counts = indirect_assocs['datatypeId'].value_counts().to_dict()

            # Get all unique datatypes from both datasets
            all_datatypes = set(direct_counts.keys()) | set(indirect_counts.keys())

            datatypes_list = []
            for dt in sorted(all_datatypes):
                datatypes_list.append({
                    'datatype_id': dt,
                    'description': datatype_descriptions.get(dt, 'No description available'),
                    'direct_associations': direct_counts.get(dt, 0),
                    'indirect_associations': indirect_counts.get(dt, 0)
                })

        else:
            # Just list datatypes without counts
            datatypes_list = [
                {
                    'datatype_id': dt,
                    'description': desc
                }
                for dt, desc in sorted(datatype_descriptions.items())
            ]

        return {
            "success": True,
            "count": len(datatypes_list),
            "datatypes": datatypes_list,
            "note": "Use these datatype IDs with filter_by_datatype() to focus on specific evidence types"
        }

    except Exception as e:
        logger.error(f"Error listing datatypes: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def list_available_datasources(
    datatype: Optional[str] = None,
    include_counts: bool = True
) -> dict:
    """
    List all available data sources with optional filtering and counts

    Data sources represent the original databases or studies contributing evidence.
    Open Targets integrates 23+ sources ranging from GWAS Catalog to ChEMBL.

    Args:
        datatype: Filter sources by datatype (e.g., 'genetic_association')
        include_counts: Include number of associations per source (default: True)

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of data sources
        - datatype_filter: Applied datatype filter (if any)
        - datasources: List of source information with:
            - datasource_id: Source identifier
            - datatype_id: Associated datatype
            - direct_associations: Count in direct dataset (if include_counts=True)
            - indirect_associations: Count in indirect dataset (if include_counts=True)

    Example:
        >>> # List all data sources
        >>> sources = list_available_datasources()
        >>> print(f"Found {sources['count']} data sources")
        >>>
        >>> # List only genetic association sources
        >>> genetic_sources = list_available_datasources(datatype='genetic_association')
        >>> for src in genetic_sources['datasources']:
        ...     print(f"{src['datasource_id']}: {src['direct_associations']} associations")
    """
    try:
        if include_counts:
            # Load datasource datasets
            direct_assocs = _get_loader().get_dataset("association_by_datasource_direct")
            indirect_assocs = _get_loader().get_dataset("association_by_datasource_indirect")

            # Filter by datatype if specified
            if datatype:
                direct_assocs = direct_assocs[direct_assocs['datatypeId'] == datatype]
                indirect_assocs = indirect_assocs[indirect_assocs['datatypeId'] == datatype]

            # Get counts by datasource
            direct_counts = direct_assocs.groupby(['datasourceId', 'datatypeId']).size().to_dict()
            indirect_counts = indirect_assocs.groupby(['datasourceId', 'datatypeId']).size().to_dict()

            # Get all unique (datasource, datatype) pairs
            all_pairs = set(direct_counts.keys()) | set(indirect_counts.keys())

            datasources_list = []
            for (datasource_id, datatype_id) in sorted(all_pairs):
                datasources_list.append({
                    'datasource_id': datasource_id,
                    'datatype_id': datatype_id,
                    'direct_associations': direct_counts.get((datasource_id, datatype_id), 0),
                    'indirect_associations': indirect_counts.get((datasource_id, datatype_id), 0)
                })

        else:
            # Just list unique datasources without counts
            assocs = _get_loader().get_dataset("association_by_datasource_direct")

            if datatype:
                assocs = assocs[assocs['datatypeId'] == datatype]

            unique_sources = assocs[['datasourceId', 'datatypeId']].drop_duplicates()

            datasources_list = [
                {
                    'datasource_id': row['datasourceId'],
                    'datatype_id': row['datatypeId']
                }
                for _, row in unique_sources.iterrows()
            ]
            datasources_list = sorted(datasources_list, key=lambda x: (x['datasource_id'], x['datatype_id']))

        return {
            "success": True,
            "count": len(datasources_list),
            "datatype_filter": datatype,
            "datasources": datasources_list,
            "note": "Use these datasource IDs with filter_by_datasource() to focus on specific evidence sources"
        }

    except Exception as e:
        logger.error(f"Error listing datasources: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def list_evidence_sources() -> dict:
    """
    List all evidence source partitions with statistics

    The evidence dataset is partitioned by sourceId into 23 separate directories.
    This function provides metadata about each source including estimated sizes
    and file counts.

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of evidence sources
        - sources: List of source information with:
            - source_id: Evidence source identifier
            - file_count: Number of parquet files
            - approximate_size_gb: Estimated size in gigabytes

    Example:
        >>> # Discover available evidence sources
        >>> sources = list_evidence_sources()
        >>> print(f"Evidence dataset has {sources['count']} sources")
        >>> for src in sources['sources']:
        ...     print(f"{src['source_id']}: {src['file_count']} files, ~{src['approximate_size_gb']:.2f} GB")
    """
    try:
        from pathlib import Path
        from src.config.env import Config

        evidence_path = Path(Config.OPEN_TARGETS_PATH) / "evidence"

        if not evidence_path.exists():
            return {
                "success": False,
                "error": f"Evidence dataset path not found: {evidence_path}"
            }

        # Get all source directories
        source_dirs = [d for d in evidence_path.iterdir() if d.is_dir() and d.name.startswith("sourceId=")]

        sources_list = []
        for source_dir in sorted(source_dirs):
            source_id = source_dir.name.replace("sourceId=", "")

            # Count parquet files
            parquet_files = list(source_dir.glob("*.parquet"))
            file_count = len(parquet_files)

            # Calculate approximate size
            total_size_bytes = sum(f.stat().st_size for f in parquet_files)
            size_gb = total_size_bytes / (1024**3)

            sources_list.append({
                'source_id': source_id,
                'file_count': file_count,
                'approximate_size_gb': round(size_gb, 3)
            })

        return {
            "success": True,
            "count": len(sources_list),
            "sources": sources_list,
            "total_size_gb": round(sum(s['approximate_size_gb'] for s in sources_list), 2),
            "note": "Use query_evidence() with datasource_id parameter to access specific sources"
        }

    except Exception as e:
        logger.error(f"Error listing evidence sources: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
