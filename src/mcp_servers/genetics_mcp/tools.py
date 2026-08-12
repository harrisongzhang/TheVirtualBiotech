"""
Genetics MCP Tools
Tool implementations for genetic association queries
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader
from src.utils.output_manager import OutputManager
import logging
import pandas as pd
import numpy as np
import pyarrow.dataset as ds
import pyarrow.compute as pc
import os

logger = logging.getLogger(__name__)

# ✅ CRITICAL PATTERN: This lazy loading pattern is REQUIRED for all MCP servers
# See: MCP_SERVER_TEMPLATE.md for details
#
# Why: MCP servers must start in <3 seconds to connect successfully.
# Eager loading (preload_all=True at module level) causes startup timeout.
_data_loader = None

def _get_loader():
    """Get or initialize the data loader (lazy initialization)

    This pattern ensures fast MCP server startup. Data is only loaded
    when tools are first called, not at module import time.
    """
    global _data_loader
    if _data_loader is None:
        import os
        preload = os.environ.get('PRELOAD_MCP_DATA', '0') == '1'
        _data_loader = get_data_loader(preload_all=preload)
    return _data_loader


def convert_to_native_types(obj):
    """
    Recursively convert numpy/pandas types to native Python types for JSON serialization

    Args:
        obj: Object to convert (can be dict, list, numpy/pandas types, etc.)

    Returns:
        Object with all numpy/pandas types converted to native Python types
    """
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


def query_gwas_associations(
    output_path: str,
    study_id: str = None,
    variant_id: str = None,
    chromosome: str = None,
    region: str = None,
    limit: int = 100
) -> dict:
    """
    Get GWAS associations from credible sets and save to parquet file

    Query genetic associations by study, variant, chromosome, or genomic region.
    Returns fine-mapped credible sets from GWAS and QTL studies.

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        study_id: Study identifier (e.g., 'GCST90002357', 'gtex_ge_brain_cerebellum_ensg00000241860')
        variant_id: Variant ID (e.g., '1_14677_G_A')
        chromosome: Chromosome (e.g., '1', 'X')
        region: Genomic region (e.g., 'chr1:1000000-2000000')
        limit: Maximum number of results to return (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - query: Query parameters used
        - num_results: Number of associations found
        - size_mb: File size in MB
        - summary_stats: Statistics (mean beta, p-value range)
        - top_associations: Top 10 associations by significance

    Example:
        >>> # Find associations for a specific variant
        >>> result = query_gwas_associations(
        ...     output_path='variant_assocs.parquet',
        ...     variant_id="1_14677_G_A"
        ... )
        >>>
        >>> # Find all associations in a study
        >>> result = query_gwas_associations(
        ...     output_path='study_assocs.parquet',
        ...     study_id="GCST90002357",
        ...     limit=50
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='genetics', tool_name='query_gwas_associations')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        credible_set = _get_loader().get_dataset("credible_set")

        # Build query filter
        mask = pd.Series([True] * len(credible_set))
        query_params = {}

        if study_id is not None:
            mask &= (credible_set['studyId'] == study_id)
            query_params['study_id'] = study_id

        if variant_id is not None:
            mask &= (credible_set['variantId'] == variant_id)
            query_params['variant_id'] = variant_id

        if chromosome is not None:
            mask &= (credible_set['chromosome'] == chromosome)
            query_params['chromosome'] = chromosome

        if region is not None:
            # Parse region format: chr1:1000000-2000000
            try:
                chrom, positions = region.replace('chr', '').split(':')
                start, end = map(int, positions.split('-'))
                mask &= (
                    (credible_set['chromosome'] == chrom) &
                    (credible_set['position'] >= start) &
                    (credible_set['position'] <= end)
                )
                query_params['region'] = region
            except:
                return {
                    "success": False,
                    "error": f"Invalid region format: {region}. Use 'chr1:1000000-2000000' or '1:1000000-2000000'",
                    "query": query_params,
                    "num_results": 0
                }

        if not query_params:
            return {
                "success": False,
                "error": "At least one query parameter required (study_id, variant_id, chromosome, or region)",
                "query": {},
                "num_results": 0
            }

        # Sort by GWAS significance (most significant first) before limiting
        matches = credible_set[mask]
        sort_cols = [c for c in ['pValueExponent', 'pValueMantissa'] if c in matches.columns]
        if sort_cols:
            matches = matches.sort_values(sort_cols, ascending=True, na_position='last')
        matches = matches.head(limit)

        if len(matches) == 0:
            return {
                "success": True,
                "query": query_params,
                "num_results": 0,
                "message": "No associations found"
            }

        # Save to parquet
        matches.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {}
        if 'beta' in matches.columns and matches['beta'].notna().any():
            summary_stats['mean_beta'] = float(matches['beta'].mean())
            summary_stats['beta_range'] = [float(matches['beta'].min()), float(matches['beta'].max())]

        if 'pValueExponent' in matches.columns and matches['pValueExponent'].notna().any():
            summary_stats['most_significant_pval_exp'] = int(matches['pValueExponent'].min())

        # Get top 10 for preview (select key columns)
        key_cols = ['studyLocusId', 'studyId', 'variantId', 'chromosome', 'position',
                   'beta', 'pValueMantissa', 'pValueExponent', 'studyType']
        available_cols = [c for c in key_cols if c in matches.columns]
        top_10 = matches[available_cols].head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params=query_params,
            n_records=len(matches),
            size_mb=file_size_mb,
            additional_metadata={
                'summary_stats': summary_stats
            }
        )

        return convert_to_native_types({
            "success": True,
            "output_path": final_path,
            "query": query_params,
            "num_results": len(matches),
            "size_mb": round(file_size_mb, 2),
            "summary_stats": summary_stats,
            "top_associations": top_10,
            "note": "GWAS associations saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        })

    except Exception as e:
        logger.error(f"Error in query_gwas_associations: {e}")
        return {
            "error": str(e),
            "query": query_params if 'query_params' in locals() else {},
            "num_results": 0,
            "results": []
        }


def query_l2g_predictions(
    gene_id: str = None,
    study_locus_id: str = None,
    min_score: float = 0.5,
    limit: int = 100
) -> dict:
    """
    Get locus-to-gene (L2G) predictions

    Returns machine learning predictions linking GWAS loci to their likely causal genes.
    Higher scores indicate stronger evidence for gene causality.

    Args:
        gene_id: Ensembl gene ID (e.g., 'ENSG00000072864')
        study_locus_id: Study locus ID from credible sets
        min_score: Minimum L2G score threshold (0-1, default: 0.5)
        limit: Maximum number of results to return (default: 100)

    Returns:
        Dictionary with:
        - query: Query parameters used
        - num_results: Number of predictions found
        - results: List of predictions with:
          - studyLocusId: Locus identifier
          - geneId: Target gene ID
          - score: L2G prediction score (0-1)
          - features: Array of feature contributions (SHAP values)
          - shapBaseValue: Baseline prediction value

    Example:
        >>> # Find L2G predictions for a gene
        >>> result = query_l2g_predictions(gene_id="ENSG00000072864", min_score=0.7)
        >>>
        >>> # Find genes predicted for a locus
        >>> result = query_l2g_predictions(study_locus_id="0001c69f7e3cc7461e44e5b3c68bb446")
        >>>
        >>> # Find high-confidence predictions
        >>> result = query_l2g_predictions(min_score=0.8, limit=50)
    """
    try:
        l2g = _get_loader().get_dataset("l2g_prediction")

        # Build query filter
        mask = pd.Series([True] * len(l2g))
        query_params = {'min_score': min_score}

        # Apply score threshold
        mask &= (l2g['score'] >= min_score)

        if gene_id is not None:
            mask &= (l2g['geneId'] == gene_id)
            query_params['gene_id'] = gene_id

        if study_locus_id is not None:
            mask &= (l2g['studyLocusId'] == study_locus_id)
            query_params['study_locus_id'] = study_locus_id

        # Filter and limit results
        matches = l2g[mask].head(limit)

        if len(matches) == 0:
            return convert_to_native_types({
                "query": query_params,
                "num_results": 0,
                "results": [],
                "message": "No L2G predictions found"
            })

        # Sort by score descending
        matches = matches.sort_values('score', ascending=False)

        # Convert to list of dicts
        results = []
        for _, row in matches.iterrows():
            # Handle features - check if it's a scalar NaN first
            features_val = row['features']
            import numpy as np
            if isinstance(features_val, float) and np.isnan(features_val):
                features = None
            elif features_val is None:
                features = None
            else:
                features = features_val.tolist()

            results.append({
                'studyLocusId': row['studyLocusId'],
                'geneId': row['geneId'],
                'score': float(row['score']),
                'features': features,
                'shapBaseValue': float(row['shapBaseValue']) if pd.notna(row['shapBaseValue']) else None
            })

        return convert_to_native_types({
            "query": query_params,
            "num_results": len(results),
            "results": results
        })

    except Exception as e:
        logger.error(f"Error in query_l2g_predictions: {e}")
        return {
            "error": str(e),
            "query": query_params if 'query_params' in locals() else {},
            "num_results": 0,
            "results": []
        }


def get_credible_sets(
    output_path: str,
    study_locus_id: str = None,
    study_id: str = None,
    min_confidence: float = None,
    study_type: str = None,
    limit: int = 50
) -> dict:
    """
    Get fine-mapped credible sets and save to parquet file

    Returns credible sets from fine-mapping analysis, showing which variants
    are likely to be causal at each locus.

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        study_locus_id: Unique locus identifier
        study_id: Study identifier (e.g., 'GCST90002357')
        min_confidence: Minimum credible-set log10 Bayes factor (e.g., 5, 10, 20)
        study_type: Type of study ('gwas', 'eqtl', 'pqtl', 'sqtl')
        limit: Maximum number of credible sets to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - query: Query parameters used
        - num_results: Number of credible sets found
        - size_mb: File size in MB
        - summary_stats: Statistics about credible sets
        - top_credible_sets: Top 10 credible sets by confidence

    Example:
        >>> # Get credible sets for a specific locus
        >>> result = get_credible_sets(
        ...     output_path='locus_credsets.parquet',
        ...     study_locus_id="e62f70cd4aa982aad471ba67e3915ec3"
        ... )
        >>>
        >>> # Get all eQTL credible sets from a study
        >>> result = get_credible_sets(
        ...     output_path='eqtl_credsets.parquet',
        ...     study_id="gtex_ge_brain_cerebellum_ensg00000241860",
        ...     study_type="eqtl"
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='genetics', tool_name='get_credible_sets')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        credible_set = _get_loader().get_dataset("credible_set")

        # Build query filter
        mask = pd.Series([True] * len(credible_set))
        query_params = {}

        # Filter to entries with credible set data (has locus array)
        mask &= credible_set['locus'].notna()

        if study_locus_id is not None:
            mask &= (credible_set['studyLocusId'] == study_locus_id)
            query_params['study_locus_id'] = study_locus_id

        if study_id is not None:
            mask &= (credible_set['studyId'] == study_id)
            query_params['study_id'] = study_id

        if study_type is not None:
            mask &= (credible_set['studyType'] == study_type)
            query_params['study_type'] = study_type

        if min_confidence is not None:
            # Filter based on credible set log10BF
            mask &= (credible_set['credibleSetlog10BF'] >= min_confidence)
            query_params['min_confidence'] = min_confidence

        if not query_params:
            return {
                "success": False,
                "error": "At least one query parameter required (study_locus_id, study_id, study_type, or min_confidence)",
                "query": {},
                "num_results": 0
            }

        # Sort by credible-set log10 Bayes factor (highest confidence first) before limiting
        matches = credible_set[mask]
        if 'credibleSetlog10BF' in matches.columns:
            matches = matches.sort_values('credibleSetlog10BF', ascending=False, na_position='last')
        matches = matches.head(limit)

        if len(matches) == 0:
            return {
                "success": True,
                "query": query_params,
                "num_results": 0,
                "message": "No credible sets found"
            }

        # Save to parquet
        matches.to_parquet(final_path, compression='snappy', index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {}
        if 'credibleSetlog10BF' in matches.columns and matches['credibleSetlog10BF'].notna().any():
            summary_stats['mean_log10BF'] = float(matches['credibleSetlog10BF'].mean())
            summary_stats['max_log10BF'] = float(matches['credibleSetlog10BF'].max())

        if 'studyType' in matches.columns:
            summary_stats['study_types'] = matches['studyType'].value_counts().to_dict()

        # Get top 10 for preview (exclude locus array for preview)
        key_cols = ['studyLocusId', 'studyId', 'variantId', 'chromosome', 'position',
                   'finemappingMethod', 'credibleSetIndex', 'credibleSetlog10BF', 'studyType']
        available_cols = [c for c in key_cols if c in matches.columns]
        top_10 = matches[available_cols].head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params=query_params,
            n_records=len(matches),
            size_mb=file_size_mb,
            additional_metadata={
                'summary_stats': summary_stats
            }
        )

        return convert_to_native_types({
            "success": True,
            "output_path": final_path,
            "query": query_params,
            "num_results": len(matches),
            "size_mb": round(file_size_mb, 2),
            "summary_stats": summary_stats,
            "top_credible_sets": top_10,
            "note": "Credible sets saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
        })

    except Exception as e:
        logger.error(f"Error in get_credible_sets: {e}")
        return {
            "error": str(e),
            "query": query_params if 'query_params' in locals() else {},
            "num_results": 0,
            "results": []
        }


def get_qtl_colocalization(
    study_locus_id: str = None,
    gene_id: str = None,
    qtl_type: str = None,
    min_clpp: float = 0.5,
    limit: int = 50
) -> dict:
    """
    Get QTL colocalization evidence from L2G features

    Returns colocalization between GWAS loci and QTL signals (eQTL, pQTL, sQTL).
    High CLPP (colocalization posterior probability) indicates shared causal variants.

    Args:
        study_locus_id: Study locus ID to find colocalizing QTLs
        gene_id: Gene ID to find colocalizing loci
        qtl_type: QTL type ('eqtl', 'pqtl', 'sqtl')
        min_clpp: Minimum CLPP threshold (0-1, default: 0.5)
        limit: Maximum number of results to return (default: 50)

    Returns:
        Dictionary with:
        - query: Query parameters used
        - num_results: Number of colocalizations found
        - results: List of colocalizations with:
          - studyLocusId: Locus identifier
          - geneId: Gene identifier
          - score: Overall L2G score
          - colocalization: Extracted QTL colocalization features:
            - eQtlColocClppMaximum: eQTL CLPP
            - pQtlColocClppMaximum: pQTL CLPP
            - sQtlColocClppMaximum: sQTL CLPP
            - eQtlColocH4Maximum: eQTL H4 probability
            - etc.

    Example:
        >>> # Find eQTL colocalizations for a gene
        >>> result = get_qtl_colocalization(gene_id="ENSG00000072864", qtl_type="eqtl", min_clpp=0.7)
        >>>
        >>> # Find all QTL colocalizations for a locus
        >>> result = get_qtl_colocalization(study_locus_id="0001c69f7e3cc7461e44e5b3c68bb446")
    """
    try:
        # Guard against unbounded full scan: require a locus or gene filter
        if not study_locus_id and not gene_id:
            return {
                "success": False,
                "error": "get_qtl_colocalization requires study_locus_id or gene_id",
                "query": {},
                "num_results": 0,
                "results": []
            }

        l2g = _get_loader().get_dataset("l2g_prediction")

        # Build query filter
        mask = pd.Series([True] * len(l2g))
        query_params = {'min_clpp': min_clpp}

        if study_locus_id is not None:
            mask &= (l2g['studyLocusId'] == study_locus_id)
            query_params['study_locus_id'] = study_locus_id

        if gene_id is not None:
            mask &= (l2g['geneId'] == gene_id)
            query_params['gene_id'] = gene_id

        if qtl_type is not None:
            query_params['qtl_type'] = qtl_type

        # Initial filter
        matches = l2g[mask]

        if len(matches) == 0:
            return convert_to_native_types({
                "query": query_params,
                "num_results": 0,
                "results": [],
                "message": "No L2G predictions found"
            })

        # Extract colocalization features and filter by CLPP
        results = []
        for _, row in matches.iterrows():
            features_array = row['features']
            # Check if features is nan (scalar float) or None
            import numpy as np
            if features_array is None:
                continue
            if isinstance(features_array, float) and np.isnan(features_array):
                continue

            # Convert features array to dict
            coloc_features = {}
            for feature in features_array:
                feature_name = feature['name']
                if 'Coloc' in feature_name or 'H4' in feature_name:
                    coloc_features[feature_name] = {
                        'value': float(feature['value']),
                        'shapValue': float(feature['shapValue'])
                    }

            # Filter by QTL type and CLPP threshold
            if qtl_type:
                # Check specific QTL type CLPP
                qtl_key = f"{qtl_type[0].lower()}QtlColocClppMaximum"  # eQtlColocClppMaximum, etc.
                if qtl_key in coloc_features:
                    if coloc_features[qtl_key]['value'] < min_clpp:
                        continue
                else:
                    continue
            else:
                # Check if any CLPP exceeds threshold
                max_clpp = max(
                    [v['value'] for k, v in coloc_features.items() if 'Clpp' in k],
                    default=0
                )
                if max_clpp < min_clpp:
                    continue

            results.append({
                'studyLocusId': row['studyLocusId'],
                'geneId': row['geneId'],
                'score': float(row['score']),
                'colocalization': coloc_features
            })

            if len(results) >= limit:
                break

        # Sort by L2G score
        results = sorted(results, key=lambda x: x['score'], reverse=True)

        return convert_to_native_types({
            "query": query_params,
            "num_results": len(results),
            "results": results
        })

    except Exception as e:
        logger.error(f"Error in get_qtl_colocalization: {e}")
        return {
            "error": str(e),
            "query": query_params if 'query_params' in locals() else {},
            "num_results": 0,
            "results": []
        }


def convert_rsid_to_variant_id(rs_id: str) -> dict:
    """
    Convert rsID to variant_id format for use with get_variant_annotation

    This is a helper tool to convert dbSNP rsIDs (e.g., 'rs7412') to the variant_id
    format (e.g., '19_44908822_C_T') required by get_variant_annotation.

    Args:
        rs_id: dbSNP rsID (e.g., 'rs429358', 'rs7412')

    Returns:
        Dictionary with:
        - success: Boolean
        - rs_id: Input rsID
        - variant_id: Converted variant ID (if found)
        - chromosome: Chromosome
        - position: Position
        - message: Helpful message

    Example:
        >>> # Convert rsID to variant_id
        >>> result = convert_rsid_to_variant_id("rs7412")
        >>> if result['success']:
        >>>     variant_id = result['variant_id']
        >>>     # Now use with get_variant_annotation
        >>>     annotation = get_variant_annotation(variant_id=variant_id)

    Note:
        ⚡ OPTIMIZED: Uses PyArrow to read only minimal columns for faster lookup
        Performance: ~10-30 seconds (vs 12 minutes for full variant query)
    """
    try:
        # Use PyArrow to read only the columns we need (much faster)
        base_path = _get_loader()._base_path
        variant_path = base_path / "variant"

        if variant_path.exists():
            try:
                # Read only variantId and rsIds columns (much smaller than full dataset)
                dataset = ds.dataset(str(variant_path), format="parquet")
                scanner = dataset.scanner(columns=['variantId', 'rsIds', 'chromosome', 'position'])
                table = scanner.to_table()

                # Convert to pandas for list handling
                df = table.to_pandas()

                # Explode rsIds list to searchable format
                df_exploded = df.explode('rsIds')

                # Search for the rsID
                matches = df_exploded[df_exploded['rsIds'] == rs_id]

                if matches.empty:
                    return {
                        "success": False,
                        "rs_id": rs_id,
                        "error": f"rsID '{rs_id}' not found in database"
                    }

                # Get first match
                match = matches.iloc[0]

                logger.info(f"✓ Converted {rs_id} to {match['variantId']}")
                print(f"[PERFORMANCE] ✓ rsID converter used (optimized column selection)", file=sys.stderr)

                return {
                    "success": True,
                    "rs_id": rs_id,
                    "variant_id": str(match['variantId']),
                    "chromosome": str(match['chromosome']),
                    "position": int(match['position']),
                    "message": f"Use variant_id '{match['variantId']}' with get_variant_annotation for fast query"
                }

            except Exception as e:
                logger.warning(f"PyArrow rsID conversion failed: {e}")
                # Fall back to pandas full load
                pass

        # Fallback: Use full pandas dataset
        variants = _get_loader().get_dataset("variant")

        # Explode rsIds and search
        df_exploded = variants[['variantId', 'rsIds', 'chromosome', 'position']].explode('rsIds')
        matches = df_exploded[df_exploded['rsIds'] == rs_id]

        if matches.empty:
            return {
                "success": False,
                "rs_id": rs_id,
                "error": f"rsID '{rs_id}' not found in database"
            }

        match = matches.iloc[0]

        return {
            "success": True,
            "rs_id": rs_id,
            "variant_id": str(match['variantId']),
            "chromosome": str(match['chromosome']),
            "position": int(match['position']),
            "message": f"Use variant_id '{match['variantId']}' with get_variant_annotation for fast query"
        }

    except Exception as e:
        logger.error(f"Error converting rsID: {e}", exc_info=True)
        return {
            "success": False,
            "rs_id": rs_id,
            "error": str(e)
        }


def get_variant_annotation(
    variant_id: str = None,
    chromosome: str = None,
    position: int = None
) -> dict:
    """
    Get variant annotations including consequences, allele frequencies, and functional predictions

    Args:
        variant_id: Variant ID in chr_pos_ref_alt format (e.g., '19_44908822_C_T')
        chromosome: Chromosome (for position-based search)
        position: Genomic position (GRCh38, requires chromosome)

    Returns:
        Dictionary with variant annotations

    Example:
        >>> # By variant ID (FAST - 10s)
        >>> variant = get_variant_annotation(variant_id="19_44908822_C_T")
        >>>
        >>> # By position (FAST - 10s)
        >>> variant = get_variant_annotation(chromosome="19", position=44908822)

    Note:
        ⚡ OPTIMIZED: All queries use PyArrow for 80x performance improvement
        Performance: ~10 seconds per query (vs ~12 minutes with rs_id lookups)
    """
    try:
        # Use PyArrow optimization (80x performance improvement)
        base_path = _get_loader()._base_path
        variant_path = base_path / "variant"

        if variant_path.exists():
            try:
                # Use PyArrow for efficient filtering (predicate pushdown)
                dataset = ds.dataset(str(variant_path), format="parquet")

                # Build filter expression
                if variant_id:
                    filter_expr = (pc.field("variantId") == variant_id)
                elif chromosome and position is not None:
                    filter_expr = (
                        (pc.field("chromosome") == str(chromosome)) &
                        (pc.field("position") == position)
                    )
                else:
                    return {
                        "success": False,
                        "error": "Must provide variant_id or chromosome+position"
                    }

                # Scan with filter (only reads matching partitions)
                scanner = dataset.scanner(filter=filter_expr)
                result_table = scanner.to_table()

                if len(result_table) == 0:
                    return {
                        "success": False,
                        "error": "Variant not found"
                    }

                # Convert first match to dict
                result_df = result_table.to_pandas()
                variant_dict = result_df.iloc[0].to_dict()
                variant_dict = convert_to_native_types(variant_dict)
                variant_dict["success"] = True

                logger.info(f"✓ PyArrow optimization used for variant query")
                print(f"[PERFORMANCE] ✓ PyArrow optimization used for variant query", file=sys.stderr)
                return variant_dict

            except Exception as pyarrow_error:
                # Fall back to pandas if PyArrow fails
                logger.warning(f"PyArrow query failed, falling back to pandas: {pyarrow_error}")
                print(f"[PERFORMANCE] ⚠️ PyArrow failed for variant, using pandas: {str(pyarrow_error)[:100]}", file=sys.stderr)

        # Fallback: Original pandas implementation
        variants = _get_loader().get_dataset("variant")

        # Search by variant_id
        if variant_id:
            result_df = variants[variants['variantId'] == variant_id]

        # Search by position
        elif chromosome and position is not None:
            result_df = variants[
                (variants['chromosome'] == str(chromosome)) &
                (variants['position'] == position)
            ]
        else:
            return {
                "success": False,
                "error": "Must provide variant_id or chromosome+position"
            }

        if result_df.empty:
            return {
                "success": False,
                "error": "Variant not found"
            }

        # Return first match
        variant_dict = result_df.iloc[0].to_dict()
        variant_dict = convert_to_native_types(variant_dict)
        variant_dict["success"] = True

        return variant_dict

    except Exception as e:
        logger.error(f"Error getting variant annotation: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_study_metadata(
    study_id: str = None,
    trait: str = None,
    study_type: str = None,
    gene_id: str = None,
    min_sample_size: int = None,
    project_id: str = None,
    limit: int = 20
) -> dict:
    """
    Get GWAS/QTL study metadata with enhanced filtering capabilities

    Searches across both GWAS studies and QTL studies (eQTL, pQTL, sQTL) with
    flexible filtering options for study discovery and selection.

    Args:
        study_id: Study ID (e.g., 'GCST90002357' for GWAS, 'gtex_*' for GTEx)
        trait: Search by trait name (case-insensitive, partial match)
        study_type: Filter by study type (NEW):
            - 'gwas': Genome-wide association studies
            - 'eqtl': Expression QTL studies
            - 'pqtl': Protein QTL studies
            - 'sqtl': Splicing QTL studies
        gene_id: Filter QTL studies for a specific gene (NEW - e.g., 'ENSG00000130203')
        min_sample_size: Minimum total sample size (NEW - e.g., 10000 for large studies)
        project_id: Filter by project (NEW - e.g., 'GTEx', 'FINNGEN_R12', 'GCST')
        limit: Maximum number of studies to return (default: 20)

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of studies found
        - filters_applied: Dictionary of applied filters
        - studies: List of study metadata records

    Example:
        >>> # Get specific study
        >>> study = get_study_metadata(study_id="GCST90002357")
        >>>
        >>> # Find large GWAS studies
        >>> studies = get_study_metadata(study_type="gwas", min_sample_size=100000)
        >>>
        >>> # Find eQTL studies for APOE
        >>> studies = get_study_metadata(study_type="eqtl", gene_id="ENSG00000130203")
        >>>
        >>> # Find GTEx studies
        >>> studies = get_study_metadata(project_id="GTEx", study_type="eqtl")
    """
    try:
        studies = _get_loader().get_dataset("study")

        # Search by study ID (exact match - returns single study)
        if study_id:
            result_df = studies[studies['studyId'] == study_id]

            if result_df.empty:
                return {
                    "success": False,
                    "error": f"Study {study_id} not found"
                }

            study_dict = result_df.iloc[0].to_dict()
            study_dict = convert_to_native_types(study_dict)
            study_dict["success"] = True

            return study_dict

        # Build filter mask for search queries
        mask = pd.Series([True] * len(studies))
        filters_applied = {}

        # Search by trait
        if trait:
            trait_lower = trait.lower()
            mask &= studies['traitFromSource'].str.lower().str.contains(trait_lower, na=False)
            filters_applied['trait'] = trait

        # NEW: Filter by study type
        if study_type:
            mask &= studies['studyType'] == study_type
            filters_applied['study_type'] = study_type

        # NEW: Filter by gene ID (for QTL studies)
        if gene_id:
            mask &= studies['geneId'] == gene_id
            filters_applied['gene_id'] = gene_id

        # NEW: Filter by minimum sample size
        if min_sample_size is not None:
            if 'nSamples' in studies.columns:
                mask &= (studies['nSamples'] >= min_sample_size) | (studies['nSamples'].isna())
                filters_applied['min_sample_size'] = min_sample_size

        # NEW: Filter by project
        if project_id:
            # Support partial match for project (e.g., "GTEx" matches "GTEx_V8")
            mask &= studies['projectId'].str.contains(project_id, case=False, na=False)
            filters_applied['project_id'] = project_id

        result_df = studies[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "filters_applied": filters_applied,
                "studies": [],
                "message": "No studies found matching criteria"
            }

        # Select key fields
        key_fields = [
            'studyId', 'studyType', 'traitFromSource', 'geneId',
            'nCases', 'nControls', 'nSamples', 'projectId',
            'publicationFirstAuthor', 'publicationDate', 'publicationJournal'
        ]
        available_fields = [f for f in key_fields if f in result_df.columns]
        result_df = result_df[available_fields]

        studies_list = result_df.to_dict('records')
        studies_list = convert_to_native_types(studies_list)

        return {
            "success": True,
            "count": len(studies_list),
            "limit": limit,
            "filters_applied": filters_applied,
            "studies": studies_list
        }

    except Exception as e:
        logger.error(f"Error getting study metadata: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def query_regulatory_regions(
    output_path: str,
    gene_id: str = None,
    chromosome: str = None,
    start: int = None,
    end: int = None,
    biosample: str = None,
    interval_type: str = None,
    min_score: float = None,
    max_distance_to_tss: int = None,
    limit: int = 100
) -> dict:
    """
    Query enhancer-gene regulatory interactions from ATAC-seq, ChIP-seq, and other sources
    and save to parquet file

    Enhanced with filtering by interval type, confidence score, and distance to TSS
    for precision filtering of regulatory elements.

    Args:
        output_path: Path where .parquet file will be saved (REQUIRED)
        gene_id: Ensembl gene ID to find regulatory regions for
        chromosome: Chromosome (for region-based search)
        start: Start position (GRCh38, requires chromosome and end)
        end: End position (requires chromosome and start)
        biosample: Filter by tissue/cell type (e.g., 'lung', 'brain')
        interval_type: Filter by genomic feature type:
            - 'promoter': Promoter regions
            - 'enhancer': Enhancer regions
            - 'intergenic': Intergenic regions
            - 'intronic': Intronic regions
        min_score: Minimum regulatory activity score 0-1 (e.g., 0.5 for high confidence)
        max_distance_to_tss: Maximum distance to TSS in bp (e.g., 50000 for proximal)
        limit: Maximum number of results (default: 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .parquet file
        - count: Number of regions found
        - size_mb: File size in MB
        - filters_applied: Dictionary of applied filters
        - summary_stats: Statistics about regulatory regions
        - top_regions: Top 10 regions by score

    Example:
        >>> # Get high-confidence regulatory regions for APOE
        >>> result = query_regulatory_regions(
        ...     output_path='apoe_regulatory.parquet',
        ...     gene_id="ENSG00000130203",
        ...     min_score=0.7
        ... )
        >>>
        >>> # Get proximal promoters for a gene
        >>> result = query_regulatory_regions(
        ...     output_path='tp53_promoters.parquet',
        ...     gene_id="ENSG00000141510",
        ...     interval_type="promoter",
        ...     max_distance_to_tss=2000
        ... )

    Note: File persists for reuse. Use pandas.read_parquet() to load full data.
          ⚡ OPTIMIZED with PyArrow for 10-100x performance improvement on large dataset
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='genetics', tool_name='query_regulatory_regions')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.parquet')

        # Track applied filters
        filters_applied = {}

        # Try PyArrow optimization first (10-100x faster for initial filter)
        base_path = _get_loader()._base_path
        interval_path = base_path / "interval"

        if interval_path.exists():
            try:
                # Use PyArrow for efficient filtering (predicate pushdown)
                dataset = ds.dataset(str(interval_path), format="parquet")

                # Build initial filter expression (only for geneId or region)
                filter_expr = None

                if gene_id:
                    filter_expr = (pc.field("geneId") == gene_id)
                    filters_applied['gene_id'] = gene_id

                elif chromosome and start is not None and end is not None:
                    filter_expr = (
                        (pc.field("chromosome") == str(chromosome)) &
                        (pc.field("start") >= start) &
                        (pc.field("end") <= end)
                    )
                    filters_applied.update({'chromosome': chromosome, 'start': start, 'end': end})

                else:
                    return {
                        "success": False,
                        "error": "Must provide gene_id or chromosome+start+end"
                    }

                # Scan with filter (only reads matching partitions)
                # Read more than limit to allow for additional filtering
                scanner = dataset.scanner(filter=filter_expr)
                result_table = scanner.head(limit * 10)  # 10x buffer for additional filters

                if len(result_table) == 0:
                    return {
                        "success": True,
                        "count": 0,
                        "filters_applied": filters_applied,
                        "message": "No regulatory regions found"
                    }

                # Convert to pandas for additional filtering
                result_df = result_table.to_pandas()

                # Apply additional filters using pandas (these are more complex)
                # Filter by biosample if specified
                if biosample:
                    biosample_lower = biosample.lower()
                    if 'biosampleName' in result_df.columns:
                        result_df = result_df[
                            result_df['biosampleName'].str.lower().str.contains(biosample_lower, na=False)
                        ]
                        filters_applied['biosample'] = biosample

                # Filter by interval type
                if interval_type:
                    if 'intervalType' in result_df.columns:
                        result_df = result_df[result_df['intervalType'] == interval_type]
                        filters_applied['interval_type'] = interval_type

                # Filter by minimum score
                if min_score is not None:
                    if 'score' in result_df.columns:
                        result_df = result_df[
                            (result_df['score'] >= min_score) | (result_df['score'].isna())
                        ]
                        filters_applied['min_score'] = min_score

                # Filter by maximum distance to TSS
                if max_distance_to_tss is not None:
                    if 'distanceToTss' in result_df.columns:
                        result_df = result_df[
                            (result_df['distanceToTss'].abs() <= max_distance_to_tss) |
                            (result_df['distanceToTss'].isna())
                        ]
                        filters_applied['max_distance_to_tss'] = max_distance_to_tss

                # Sort by score (descending, if available)
                if 'score' in result_df.columns:
                    result_df = result_df.sort_values('score', ascending=False, na_position='last')

                # Limit results
                result_df = result_df.head(limit)

                # Save to parquet
                result_df.to_parquet(final_path, compression='snappy', index=False)

                # Get file size
                file_size_bytes = os.path.getsize(final_path)
                file_size_mb = file_size_bytes / (1024**2)

                # Compute summary statistics
                summary_stats = {}
                if 'score' in result_df.columns and result_df['score'].notna().any():
                    summary_stats['mean_score'] = float(result_df['score'].mean())
                    summary_stats['max_score'] = float(result_df['score'].max())

                if 'intervalType' in result_df.columns:
                    summary_stats['interval_types'] = result_df['intervalType'].value_counts().to_dict()

                # Get top 10 for preview
                top_10 = result_df.head(10).to_dict('records')
                top_10 = convert_to_native_types(top_10)

                # Register output
                om.register_output(
                    file_path=final_path,
                    query_params=filters_applied,
                    n_records=len(result_df),
                    size_mb=file_size_mb,
                    additional_metadata={
                        'summary_stats': summary_stats
                    }
                )

                logger.info(f"PyArrow optimization used for regulatory regions query")
                print(f"[PERFORMANCE] ✓ PyArrow optimization used for regulatory regions query", file=sys.stderr)

                return {
                    "success": True,
                    "output_path": final_path,
                    "count": len(result_df),
                    "limit": limit,
                    "size_mb": round(file_size_mb, 2),
                    "filters_applied": filters_applied,
                    "summary_stats": summary_stats,
                    "top_regions": top_10,
                    "note": "Regulatory regions saved to parquet file. Use pandas.read_parquet() to load. File persists for reuse."
                }

            except Exception as pyarrow_error:
                # Fall back to pandas if PyArrow fails
                logger.warning(f"PyArrow query failed, falling back to pandas: {pyarrow_error}")
                print(f"[PERFORMANCE] ⚠️ PyArrow failed for regulatory regions, using pandas: {str(pyarrow_error)[:100]}", file=sys.stderr)

        # Fallback: Original pandas implementation
        intervals = _get_loader().get_dataset("interval")

        # Search by gene ID
        if gene_id:
            result_df = intervals[intervals['geneId'] == gene_id]
            filters_applied['gene_id'] = gene_id

        # Search by genomic region
        elif chromosome and start is not None and end is not None:
            result_df = intervals[
                (intervals['chromosome'] == str(chromosome)) &
                (intervals['start'] >= start) &
                (intervals['end'] <= end)
            ]
            filters_applied.update({'chromosome': chromosome, 'start': start, 'end': end})

        else:
            return {
                "success": False,
                "error": "Must provide gene_id or chromosome+start+end"
            }

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "filters_applied": filters_applied,
                "regions": [],
                "message": "No regulatory regions found"
            }

        # Filter by biosample if specified
        if biosample:
            biosample_lower = biosample.lower()
            result_df = result_df[
                result_df['biosampleName'].str.lower().str.contains(biosample_lower, na=False)
            ]
            filters_applied['biosample'] = biosample

        # NEW: Filter by interval type
        if interval_type:
            if 'intervalType' in result_df.columns:
                result_df = result_df[result_df['intervalType'] == interval_type]
                filters_applied['interval_type'] = interval_type

        # NEW: Filter by minimum score
        if min_score is not None:
            if 'score' in result_df.columns:
                result_df = result_df[
                    (result_df['score'] >= min_score) | (result_df['score'].isna())
                ]
                filters_applied['min_score'] = min_score

        # NEW: Filter by maximum distance to TSS
        if max_distance_to_tss is not None:
            if 'distanceToTss' in result_df.columns:
                result_df = result_df[
                    (result_df['distanceToTss'].abs() <= max_distance_to_tss) |
                    (result_df['distanceToTss'].isna())
                ]
                filters_applied['max_distance_to_tss'] = max_distance_to_tss

        # Sort by score (descending, if available)
        if 'score' in result_df.columns:
            result_df = result_df.sort_values('score', ascending=False, na_position='last')

        # Limit results
        result_df = result_df.head(limit)

        # Convert to list
        regions_list = result_df.to_dict('records')
        regions_list = convert_to_native_types(regions_list)

        return {
            "success": True,
            "count": len(regions_list),
            "limit": limit,
            "filters_applied": filters_applied,
            "regions": regions_list
        }

    except Exception as e:
        logger.error(f"Error querying regulatory regions: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def query_colocalisation(
    study_locus_id: str,
    method: str = None,
    min_h4: float = None,
    min_clpp: float = None,
    limit: int = 50
) -> dict:
    """
    Query colocalisation analysis results

    Colocalisation tests whether two GWAS signals share the same causal variant.
    Supports both COLOC (H4 probability) and eCAVIAR (CLPP) methods.

    Args:
        study_locus_id: Study locus identifier (either left or right)
        method: Filter by method ('coloc' or 'ecaviar')
        min_h4: Minimum H4 probability for COLOC (0-1, typically >0.8 for strong colocalisation)
        min_clpp: Minimum CLPP for eCAVIAR (0-1, typically >0.01)
        limit: Maximum number of results

    Returns:
        Dictionary with:
        - success: Boolean
        - study_locus_id: Query locus
        - count: Number of colocalisations
        - colocalisations: List of colocalisation records

    Example:
        >>> # Find strong COLOC colocalisations
        >>> colocs = query_colocalisation("study_locus_123", method="coloc", min_h4=0.8)
        >>> # Find eCAVIAR colocalisations
        >>> colocs = query_colocalisation("study_locus_123", method="ecaviar", min_clpp=0.01)
    """
    try:
        results = []

        # Query COLOC if requested
        if method is None or method.lower() == 'coloc':
            coloc = _get_loader().get_dataset("colocalisation_coloc")

            mask = (coloc['leftStudyLocusId'] == study_locus_id) | \
                   (coloc['rightStudyLocusId'] == study_locus_id)

            if min_h4 is not None:
                mask &= coloc['h4'] >= min_h4

            coloc_results = coloc[mask].to_dict('records')
            results.extend(coloc_results)

        # Query eCAVIAR if requested
        if method is None or method.lower() == 'ecaviar':
            ecaviar = _get_loader().get_dataset("colocalisation_ecaviar")

            mask = (ecaviar['leftStudyLocusId'] == study_locus_id) | \
                   (ecaviar['rightStudyLocusId'] == study_locus_id)

            if min_clpp is not None:
                mask &= ecaviar['clpp'] >= min_clpp

            ecaviar_results = ecaviar[mask].to_dict('records')
            results.extend(ecaviar_results)

        # Limit results
        results = results[:limit]

        results = convert_to_native_types(results)

        if not results:
            return {
                "success": True,
                "study_locus_id": study_locus_id,
                "count": 0,
                "colocalisations": [],
                "message": "No colocalisations found"
            }

        return {
            "success": True,
            "study_locus_id": study_locus_id,
            "count": len(results),
            "limit": limit,
            "colocalisations": results
        }

    except Exception as e:
        logger.error(f"Error querying colocalisation: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_colocalisation_by_chromosome(
    chromosome: str,
    method: str = 'coloc',
    min_score: float = 0.8,
    limit: int = 50
) -> dict:
    """
    Get colocalisation results for a chromosome

    Args:
        chromosome: Chromosome (e.g., '1', '2', ..., 'X')
        method: 'coloc' or 'ecaviar'
        min_score: Minimum H4 (for coloc) or CLPP (for ecaviar)
        limit: Maximum number of results

    Returns:
        Dictionary with colocalisation records

    Example:
        >>> # Find strong colocalisations on chromosome 19
        >>> colocs = get_colocalisation_by_chromosome("19", method="coloc", min_score=0.8)
    """
    try:
        if method.lower() == 'coloc':
            dataset = _get_loader().get_dataset("colocalisation_coloc")
            score_col = 'h4'
        else:
            dataset = _get_loader().get_dataset("colocalisation_ecaviar")
            score_col = 'clpp'

        mask = (dataset['chromosome'] == str(chromosome)) & \
               (dataset[score_col] >= min_score)

        result_df = dataset[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "chromosome": chromosome,
                "count": 0,
                "colocalisations": [],
                "message": "No colocalisations found"
            }

        results = result_df.to_dict('records')
        results = convert_to_native_types(results)

        return {
            "success": True,
            "chromosome": chromosome,
            "method": method,
            "count": len(results),
            "colocalisations": results
        }

    except Exception as e:
        logger.error(f"Error getting colocalisation by chromosome: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# Export all tools
__all__ = [
    'query_gwas_associations',
    'query_l2g_predictions',
    'get_credible_sets',
    'get_qtl_colocalization',
    'get_variant_annotation',
    'get_study_metadata',
    'query_regulatory_regions',
    'query_colocalisation',
    'get_colocalisation_by_chromosome'
]
