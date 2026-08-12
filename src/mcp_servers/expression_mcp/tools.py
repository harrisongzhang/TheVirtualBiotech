"""
Expression MCP Tools
Provides tools for querying and comparing Open Targets baseline expression (RNA baseline + Human Protein Atlas protein IHC) data

Data Structure:
- Dataset: gene × tissues
- Each gene has array of tissue entries with:
  - efo_code, label, organs, anatomical_systems
  - rna: {value, zscore, level, unit}
  - protein: {reliability, level, cell_type}
"""

import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader
from src.utils.output_manager import OutputManager
import os

logger = logging.getLogger(__name__)

# Initialize data loader lazily (don't preload at import time)
# This allows the MCP server to start quickly and respond to SDK handshake
#
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


def list_available_tissues() -> dict:
    """
    Get list of all available tissues in the expression dataset

    Returns comprehensive tissue information including:
    - Tissue labels and EFO codes
    - Organ systems and anatomical systems
    - Number of genes with expression data per tissue

    Returns:
        Dictionary with:
        - num_tissues: Total number of unique tissues
        - tissues: List of tissue info dicts with:
            - efo_code: EFO ontology code
            - label: Human-readable tissue name
            - organs: List of organ names
            - anatomical_systems: List of system names
            - num_expressed_genes: Count of genes with RNA data

    Example:
        result = list_available_tissues()
        # Returns: {
        #   "num_tissues": 185,
        #   "tissues": [
        #     {
        #       "efo_code": "UBERON_0002110",
        #       "label": "gall bladder",
        #       "organs": ["bladder organ"],
        #       "anatomical_systems": ["digestive system"],
        #       "num_expressed_genes": 12345
        #     },
        #     ...
        #   ]
        # }
    """
    try:
        expression = _get_loader().get_dataset("expression")

        # Collect all unique tissues across all genes
        tissue_info = {}

        for _, row in expression.iterrows():
            tissues_array = row['tissues']

            # Skip if no tissue data
            if tissues_array is None:
                continue
            if isinstance(tissues_array, float) and np.isnan(tissues_array):
                continue

            # Process each tissue entry
            for tissue in tissues_array:
                efo_code = tissue.get('efo_code')
                if not efo_code:
                    continue

                # Initialize tissue entry if first time seeing it
                if efo_code not in tissue_info:
                    tissue_info[efo_code] = {
                        'efo_code': efo_code,
                        'label': tissue.get('label', ''),
                        'organs': tissue.get('organs', []),
                        'anatomical_systems': tissue.get('anatomical_systems', []),
                        'num_expressed_genes': 0
                    }

                # Count genes with RNA expression data
                rna_data = tissue.get('rna')
                if rna_data and rna_data.get('value') is not None:
                    tissue_info[efo_code]['num_expressed_genes'] += 1

        # Convert to sorted list
        tissues_list = sorted(tissue_info.values(), key=lambda x: x['label'])

        return convert_to_native_types({
            'num_tissues': len(tissues_list),
            'tissues': tissues_list
        })

    except Exception as e:
        return {
            'error': f'Failed to list tissues: {str(e)}',
            'num_tissues': 0,
            'tissues': []
        }


def query_expression_by_gene(
    gene_id: str,
    tissue: Optional[str] = None
) -> dict:
    """
    Query expression data for a specific gene across tissues

    Args:
        gene_id: Ensembl gene ID (e.g., 'ENSG00000139618')
        tissue: Optional tissue filter (EFO code or label)
                If None, returns all tissues

    Returns:
        Dictionary with:
        - gene_id: Gene identifier
        - found: Whether gene was found
        - num_tissues: Number of tissues with data
        - expression: List of tissue expression entries with:
            - tissue: {efo_code, label, organs, anatomical_systems}
            - rna: {value, zscore, level} (if available)
            - protein: {reliability, level} (if available)

    Example:
        # Get all tissues
        result = query_expression_by_gene("ENSG00000139618")

        # Get specific tissue
        result = query_expression_by_gene("ENSG00000139618", tissue="brain")
    """
    try:
        expression = _get_loader().get_dataset("expression")

        # Find gene
        gene_data = expression[expression['id'] == gene_id]

        if len(gene_data) == 0:
            return {
                'gene_id': gene_id,
                'found': False,
                'error': 'Gene not found in expression dataset',
                'num_tissues': 0,
                'expression': []
            }

        tissues_array = gene_data.iloc[0]['tissues']

        # Handle missing tissue data
        if tissues_array is None:
            return {
                'gene_id': gene_id,
                'found': True,
                'num_tissues': 0,
                'expression': [],
                'note': 'Gene found but no tissue expression data'
            }

        if isinstance(tissues_array, float) and np.isnan(tissues_array):
            return {
                'gene_id': gene_id,
                'found': True,
                'num_tissues': 0,
                'expression': [],
                'note': 'Gene found but no tissue expression data'
            }

        # Filter by tissue if specified
        expression_list = []
        for tissue_entry in tissues_array:
            # Apply tissue filter
            if tissue:
                tissue_label = tissue_entry.get('label', '').lower()
                tissue_efo = tissue_entry.get('efo_code', '').lower()
                if tissue.lower() not in tissue_label and tissue.lower() not in tissue_efo:
                    continue

            # Extract RNA data
            rna_data = tissue_entry.get('rna')
            rna_info = None
            if rna_data:
                rna_info = {
                    'value': rna_data.get('value'),
                    'zscore': rna_data.get('zscore'),
                    'level': rna_data.get('level'),
                    'unit': rna_data.get('unit', '')
                }

            # Extract protein data
            protein_data = tissue_entry.get('protein')
            protein_info = None
            if protein_data:
                protein_info = {
                    'reliability': protein_data.get('reliability'),
                    'level': protein_data.get('level')
                }

            expression_list.append({
                'tissue': {
                    'efo_code': tissue_entry.get('efo_code'),
                    'label': tissue_entry.get('label'),
                    'organs': tissue_entry.get('organs', []),
                    'anatomical_systems': tissue_entry.get('anatomical_systems', [])
                },
                'rna': rna_info,
                'protein': protein_info
            })

        return convert_to_native_types({
            'gene_id': gene_id,
            'found': True,
            'num_tissues': len(expression_list),
            'expression': expression_list
        })

    except Exception as e:
        return {
            'gene_id': gene_id,
            'found': False,
            'error': f'Query failed: {str(e)}',
            'num_tissues': 0,
            'expression': []
        }


def query_expression_by_tissue(
    output_path: str,
    tissue: str,
    min_expression: Optional[float] = None,
    limit: int = 100
) -> dict:
    """
    Find genes expressed in a specific tissue and save to CSV file

    Args:
        output_path: Path where .csv file will be saved (REQUIRED)
        tissue: Tissue name (EFO code or label, case-insensitive)
        min_expression: Minimum RNA expression value threshold
        limit: Maximum number of genes to return

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .csv file
        - tissue: Tissue identifier searched
        - num_results: Number of genes found
        - size_mb: File size in MB
        - summary_stats: Expression statistics
        - top_genes: Top 10 genes by expression

    Example:
        # Find highly expressed genes in brain
        result = query_expression_by_tissue(
            output_path='brain_genes.csv',
            tissue="brain",
            min_expression=100.0,
            limit=50
        )

    Note: File persists for reuse. Use pandas.read_csv() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='expression', tool_name='query_expression_by_tissue')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.csv')

        expression = _get_loader().get_dataset("expression")

        genes_data = []

        for _, row in expression.iterrows():
            gene_id = row['id']
            tissues_array = row['tissues']

            # Skip if no tissue data
            if tissues_array is None:
                continue
            if isinstance(tissues_array, float) and np.isnan(tissues_array):
                continue

            # Search for matching tissue
            for tissue_entry in tissues_array:
                tissue_label = tissue_entry.get('label', '').lower()
                tissue_efo = tissue_entry.get('efo_code', '').lower()

                # Check if tissue matches
                if tissue.lower() not in tissue_label and tissue.lower() not in tissue_efo:
                    continue

                # Extract RNA data
                rna_data = tissue_entry.get('rna')
                if not rna_data:
                    continue

                rna_value = rna_data.get('value')
                if rna_value is None:
                    continue

                # Apply expression threshold
                if min_expression is not None and rna_value < min_expression:
                    continue

                # Extract protein data
                protein_data = tissue_entry.get('protein')

                genes_data.append({
                    'gene_id': gene_id,
                    'rna_value': rna_value,
                    'rna_zscore': rna_data.get('zscore'),
                    'rna_level': rna_data.get('level'),
                    'rna_unit': rna_data.get('unit', ''),
                    'protein_reliability': protein_data.get('reliability') if protein_data else None,
                    'protein_level': protein_data.get('level') if protein_data else None
                })

        if len(genes_data) == 0:
            return {
                'success': True,
                'tissue': tissue,
                'num_results': 0,
                'message': 'No genes found matching criteria'
            }

        # Convert to DataFrame, sort by expression value, then keep the top `limit`
        df = pd.DataFrame(genes_data)
        df = df.sort_values('rna_value', ascending=False)
        df = df.head(limit)

        # Save to CSV
        df.to_csv(final_path, index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {
            'mean_expression': float(df['rna_value'].mean()),
            'median_expression': float(df['rna_value'].median()),
            'max_expression': float(df['rna_value'].max()),
            'min_expression': float(df['rna_value'].min())
        }

        # Get top 10 for preview
        top_10 = df.head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'tissue': tissue,
                'min_expression': min_expression,
                'limit': limit
            },
            n_records=len(df),
            size_mb=file_size_mb,
            additional_metadata={
                'summary_stats': summary_stats
            }
        )

        return {
            'success': True,
            'output_path': final_path,
            'tissue': tissue,
            'num_results': len(df),
            'size_mb': round(file_size_mb, 2),
            'summary_stats': summary_stats,
            'top_genes': top_10,
            'note': 'Expression data saved to CSV file. Use pandas.read_csv() to load. File persists for reuse.'
        }

    except Exception as e:
        return {
            'tissue': tissue,
            'error': f'Query failed: {str(e)}',
            'num_results': 0,
            'genes': []
        }


def compare_expression_across_tissues(
    gene_id: str,
    tissues: Optional[List[str]] = None
) -> dict:
    """
    Compare expression of a gene across multiple tissues

    Provides comparative statistics and rankings to identify
    tissue-specific or ubiquitous expression patterns.

    Args:
        gene_id: Ensembl gene ID
        tissues: Optional list of tissue names/codes to compare
                 If None, compares across all tissues

    Returns:
        Dictionary with:
        - gene_id: Gene identifier
        - found: Whether gene was found
        - num_tissues_compared: Number of tissues in comparison
        - statistics: {
            - mean_expression: Average across tissues
            - median_expression: Median value
            - max_expression: Maximum value
            - min_expression: Minimum value
            - std_expression: Standard deviation
          }
        - tissue_expression: List sorted by expression level with:
            - tissue: {efo_code, label}
            - rna_value: Expression value
            - zscore: Z-score
            - rank: Rank by expression (1=highest)
            - percentile: Percentile (0-100)

    Example:
        # Compare across all tissues
        result = compare_expression_across_tissues("ENSG00000139618")

        # Compare specific tissues
        result = compare_expression_across_tissues(
            "ENSG00000139618",
            tissues=["brain", "liver", "heart"]
        )
    """
    try:
        expression = _get_loader().get_dataset("expression")

        # Find gene
        gene_data = expression[expression['id'] == gene_id]

        if len(gene_data) == 0:
            return {
                'gene_id': gene_id,
                'found': False,
                'error': 'Gene not found',
                'num_tissues_compared': 0
            }

        tissues_array = gene_data.iloc[0]['tissues']

        # Handle missing data
        if tissues_array is None or (isinstance(tissues_array, float) and np.isnan(tissues_array)):
            return {
                'gene_id': gene_id,
                'found': True,
                'num_tissues_compared': 0,
                'note': 'Gene found but no tissue expression data'
            }

        # Extract expression values
        tissue_values = []
        for tissue_entry in tissues_array:
            # Apply tissue filter if specified
            if tissues:
                tissue_label = tissue_entry.get('label', '').lower()
                tissue_efo = tissue_entry.get('efo_code', '').lower()
                match = any(t.lower() in tissue_label or t.lower() in tissue_efo for t in tissues)
                if not match:
                    continue

            rna_data = tissue_entry.get('rna')
            if not rna_data:
                continue

            rna_value = rna_data.get('value')
            if rna_value is None:
                continue

            tissue_values.append({
                'tissue': {
                    'efo_code': tissue_entry.get('efo_code'),
                    'label': tissue_entry.get('label')
                },
                'rna_value': rna_value,
                'zscore': rna_data.get('zscore'),
                'unit': rna_data.get('unit', '')
            })

        # Restrict statistics/ranks to a single consistent unit population.
        # tissues[] mixes normalizations (e.g. unit=='TPM' and blank-unit rows),
        # which must not be pooled. Prefer TPM, else fall back to blank-unit.
        tpm_values = [tv for tv in tissue_values if tv['unit'] == 'TPM']
        if tpm_values:
            tissue_values = tpm_values
            unit_used = 'TPM'
        else:
            tissue_values = [tv for tv in tissue_values if tv['unit'] == '']
            unit_used = ''

        if len(tissue_values) == 0:
            return {
                'gene_id': gene_id,
                'found': True,
                'num_tissues_compared': 0,
                'note': 'No expression data for specified tissues'
            }

        # Calculate statistics
        values = [tv['rna_value'] for tv in tissue_values]
        stats = {
            'mean_expression': float(np.mean(values)),
            'median_expression': float(np.median(values)),
            'max_expression': float(np.max(values)),
            'min_expression': float(np.min(values)),
            'std_expression': float(np.std(values))
        }

        # Sort by expression and add ranks
        tissue_values.sort(key=lambda x: x['rna_value'], reverse=True)

        for i, tv in enumerate(tissue_values):
            tv['rank'] = i + 1
            tv['percentile'] = round((1 - i / len(tissue_values)) * 100, 1)

        return {
            'gene_id': gene_id,
            'found': True,
            'num_tissues_compared': len(tissue_values),
            'unit': unit_used,
            'statistics': stats,
            'tissue_expression': tissue_values
        }

    except Exception as e:
        return {
            'gene_id': gene_id,
            'found': False,
            'error': f'Comparison failed: {str(e)}',
            'num_tissues_compared': 0
        }


def find_tissue_specific_genes(
    output_path: str,
    tissue: str,
    fold_change_threshold: float = 2.0,
    limit: int = 100
) -> dict:
    """
    Find genes with tissue-enriched or tissue-specific expression and save to CSV file

    Identifies genes that are highly expressed in the target tissue
    relative to other tissues (fold-change based specificity).

    Args:
        output_path: Path where .csv file will be saved (REQUIRED)
        tissue: Target tissue name (EFO code or label)
        fold_change_threshold: Minimum fold-change vs median of other tissues
                               (2.0 = 2x higher than median of others)
        limit: Maximum number of genes to return

    Returns:
        Dictionary with:
        - success: Boolean
        - output_path: Path to saved .csv file
        - tissue: Tissue identifier
        - fold_change_threshold: Threshold used
        - num_results: Number of tissue-specific genes found
        - size_mb: File size in MB
        - summary_stats: Statistics about specificity
        - top_genes: Top 10 genes by fold-change

    Example:
        # Find brain-specific genes (2x higher than other tissues)
        result = find_tissue_specific_genes(
            output_path='brain_specific.csv',
            tissue="brain",
            fold_change_threshold=2.0,
            limit=50
        )

        # Find highly specific genes (5x enrichment)
        result = find_tissue_specific_genes(
            output_path='liver_specific.csv',
            tissue="liver",
            fold_change_threshold=5.0,
            limit=20
        )

    Note: File persists for reuse. Use pandas.read_csv() to load full data.
    """
    try:
        # Initialize OutputManager
        om = OutputManager(server_name='expression', tool_name='find_tissue_specific_genes')
        final_path = om.get_output_path(user_path=output_path, auto_suffix='.csv')

        expression = _get_loader().get_dataset("expression")

        specific_genes_data = []

        for _, row in expression.iterrows():
            gene_id = row['id']
            tissues_array = row['tissues']

            # Skip if no tissue data
            if tissues_array is None or (isinstance(tissues_array, float) and np.isnan(tissues_array)):
                continue

            # Collect tissue RNA entries (with unit) for this gene
            tissue_entries = []
            for tissue_entry in tissues_array:
                tissue_label = tissue_entry.get('label', '').lower()
                tissue_efo = tissue_entry.get('efo_code', '').lower()

                rna_data = tissue_entry.get('rna')
                if not rna_data:
                    continue

                rna_value = rna_data.get('value')
                if rna_value is None:
                    continue

                # Check if this is the target tissue
                is_target = tissue.lower() in tissue_label or tissue.lower() in tissue_efo

                tissue_entries.append({
                    'is_target': is_target,
                    'value': rna_value,
                    'zscore': rna_data.get('zscore'),
                    'unit': rna_data.get('unit', '')
                })

            # Restrict the fold-change comparison to a single consistent unit
            # population. tissues[] mixes normalizations (e.g. unit=='TPM' and
            # blank-unit rows), which must not be pooled. Prefer TPM, else fall
            # back to blank-unit.
            tpm_entries = [e for e in tissue_entries if e['unit'] == 'TPM']
            if tpm_entries:
                tissue_entries = tpm_entries
                unit_used = 'TPM'
            else:
                tissue_entries = [e for e in tissue_entries if e['unit'] == '']
                unit_used = ''

            # Find target tissue and collect other tissue values
            target_value = None
            target_zscore = None
            other_values = []
            for e in tissue_entries:
                if e['is_target']:
                    target_value = e['value']
                    target_zscore = e['zscore']
                else:
                    other_values.append(e['value'])

            # Skip if target tissue not found or no comparison data
            if target_value is None or len(other_values) == 0:
                continue

            # Calculate median of other tissues
            median_other = np.median(other_values)

            # Avoid division by zero
            if median_other == 0:
                # If other tissues have 0 expression but target has expression, it's highly specific
                if target_value > 0:
                    fold_change = 999.9  # Cap at very high value
                else:
                    continue
            else:
                fold_change = target_value / median_other

            # Apply threshold
            if fold_change >= fold_change_threshold:
                specific_genes_data.append({
                    'gene_id': gene_id,
                    'tissue_expression': target_value,
                    'median_other_tissues': median_other,
                    'fold_change': fold_change,
                    'zscore_in_tissue': target_zscore,
                    'unit': unit_used
                })

        if len(specific_genes_data) == 0:
            return {
                'success': True,
                'tissue': tissue,
                'fold_change_threshold': fold_change_threshold,
                'num_results': 0,
                'message': 'No tissue-specific genes found matching criteria'
            }

        # Convert to DataFrame and sort by fold-change
        df = pd.DataFrame(specific_genes_data)
        df = df.sort_values('fold_change', ascending=False)
        df = df.head(limit)

        # Save to CSV
        df.to_csv(final_path, index=False)

        # Get file size
        file_size_bytes = os.path.getsize(final_path)
        file_size_mb = file_size_bytes / (1024**2)

        # Compute summary statistics
        summary_stats = {
            'mean_fold_change': float(df['fold_change'].mean()),
            'median_fold_change': float(df['fold_change'].median()),
            'max_fold_change': float(df['fold_change'].max()),
            'min_fold_change': float(df['fold_change'].min())
        }

        # Get top 10 for preview
        top_10 = df.head(10).to_dict('records')
        top_10 = convert_to_native_types(top_10)

        # Register output
        om.register_output(
            file_path=final_path,
            query_params={
                'tissue': tissue,
                'fold_change_threshold': fold_change_threshold,
                'limit': limit
            },
            n_records=len(df),
            size_mb=file_size_mb,
            additional_metadata={
                'summary_stats': summary_stats
            }
        )

        return {
            'success': True,
            'output_path': final_path,
            'tissue': tissue,
            'fold_change_threshold': fold_change_threshold,
            'num_results': len(df),
            'size_mb': round(file_size_mb, 2),
            'summary_stats': summary_stats,
            'top_genes': top_10,
            'note': 'Tissue-specific genes saved to CSV file. Use pandas.read_csv() to load. File persists for reuse.'
        }

    except Exception as e:
        return {
            'tissue': tissue,
            'fold_change_threshold': fold_change_threshold,
            'error': f'Query failed: {str(e)}',
            'num_results': 0,
            'genes': []
        }


def search_biosample_ontology(
    query: str = None,
    biosample_id: str = None,
    limit: int = 20
) -> dict:
    """
    Search biosample/tissue ontology or get specific biosample information

    Args:
        query: Search term for biosample name (case-insensitive, partial match)
        biosample_id: Specific biosample ID to retrieve
        limit: Maximum number of results (default: 20)

    Returns:
        Dictionary with biosample information including:
        - biosampleId: Ontology ID
        - biosampleName: Standardized name
        - description: Description
        - synonyms: Alternative names
        - parents: Parent terms
        - children: Child terms

    Example:
        >>> # Search for lung tissues
        >>> biosamples = search_biosample_ontology(query="lung")
        >>> # Get specific biosample
        >>> biosample = search_biosample_ontology(biosample_id="UBERON_0002048")
    """
    try:
        biosamples = _get_loader().get_dataset("biosample")

        # Get specific biosample by ID
        if biosample_id:
            result_df = biosamples[biosamples['biosampleId'] == biosample_id]

            if result_df.empty:
                return {
                    "success": False,
                    "error": f"Biosample {biosample_id} not found"
                }

            biosample_dict = result_df.iloc[0].to_dict()
            biosample_dict = convert_to_native_types(biosample_dict)
            biosample_dict["success"] = True

            return biosample_dict

        # Search by name
        elif query:
            query_lower = query.lower()

            # Search in biosampleName
            name_mask = biosamples['biosampleName'].str.lower().str.contains(query_lower, na=False)

            # Also search in synonyms
            def search_synonyms(syns):
                # Safe handling for None, NaN, empty arrays
                if syns is None:
                    return False
                if isinstance(syns, (list, tuple)) and len(syns) == 0:
                    return False
                # Try pd.isna but handle arrays safely
                try:
                    if pd.isna(syns):
                        return False
                except (ValueError, TypeError):
                    # If pd.isna fails on array-like, continue
                    pass

                if isinstance(syns, str):
                    return query_lower in syns.lower()
                if isinstance(syns, (list, tuple)):
                    return any(query_lower in str(s).lower() for s in syns)
                return False

            synonym_mask = biosamples['synonyms'].apply(search_synonyms)

            # Combine masks
            result_df = biosamples[name_mask | synonym_mask].head(limit)

            if result_df.empty:
                return {
                    "success": True,
                    "count": 0,
                    "biosamples": [],
                    "message": f"No biosamples found matching '{query}'"
                }

            # Select key fields
            key_fields = ['biosampleId', 'biosampleName', 'description', 'synonyms']
            available_fields = [f for f in key_fields if f in result_df.columns]
            result_df = result_df[available_fields]

            biosamples_list = result_df.to_dict('records')
            biosamples_list = convert_to_native_types(biosamples_list)

            return {
                "success": True,
                "query": query,
                "count": len(biosamples_list),
                "limit": limit,
                "biosamples": biosamples_list
            }

        else:
            return {
                "success": False,
                "error": "Must provide query or biosample_id"
            }

    except Exception as e:
        logger.error(f"Error searching biosample ontology: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
