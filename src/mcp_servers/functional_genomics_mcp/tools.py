"""
Functional Genomics MCP Tools
Provides tools for querying CRISPR screens and DepMap essentiality data

Data Structure:
- Dataset: gene × geneEssentiality
- Each gene has array of essentiality entries with:
  - isEssential: overall essentiality flag
  - depMapEssentiality: array of tissue-level CRISPR screens
    - Each tissue: tissueId, tissueName, screens (array of cell lines)
      - Each screen: cellLineName, depmapId, diseaseFromSource, expression, geneEffect, mutation
"""

import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np
import pyarrow.dataset as ds
import pyarrow.compute as pc

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader

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


def query_gene_essentiality(
    gene_id: str,
    disease: Optional[str] = None,
    tissue: Optional[str] = None
) -> dict:
    """
    Query CRISPR essentiality data for a specific gene

    ⚠️ CRITICAL LIMITATION: ALL DATA IS FROM CANCER CELL LINES ONLY (DepMap)

    This tool shows how essential a gene is for CANCER cell survival. It does NOT
    indicate essentiality in normal, healthy cells. Use this data for:
    - Target validation (is it essential in disease-relevant cancer?)
    - Cancer selectivity (more essential in one cancer type vs another?)

    DO NOT use for:
    - Non-cancer diseases (Alzheimer's, diabetes, etc.) - not applicable
    - Normal tissue safety assessment - we have no normal cell data

    Returns essentiality classification and detailed CRISPR screen results
    across cancer cell lines. Filter by disease or tissue if specified.

    Args:
        gene_id: Ensembl gene ID (e.g., 'ENSG00000139618')
        disease: Optional disease filter (partial match on diseaseFromSource - cancer types only)
        tissue: Optional tissue filter (partial match on tissueName - cancer tissue only)

    Returns:
        Dictionary with:
        - gene_id: Gene identifier
        - found: Whether gene was found
        - is_essential: Overall essentiality classification
        - num_tissues: Number of tissues with screens
        - num_cell_lines: Total number of cell lines screened
        - essentiality_by_tissue: List of tissue-level results with:
            - tissue: {id, name}
            - num_cell_lines: Count in this tissue
            - mean_gene_effect: Average dependency score
            - essential_fraction: Fraction of lines where gene is essential
            - cell_lines: Array of screen details

    Example:
        # Get all essentiality data
        result = query_gene_essentiality("ENSG00000139618")

        # Filter by disease
        result = query_gene_essentiality("ENSG00000139618", disease="Colorectal")

        # Filter by tissue
        result = query_gene_essentiality("ENSG00000139618", tissue="intestine")
    """
    try:
        essentiality = _get_loader().get_dataset("target_essentiality")

        # Find gene
        gene_data = essentiality[essentiality['id'] == gene_id]

        if len(gene_data) == 0:
            return {
                'gene_id': gene_id,
                'found': False,
                'error': 'Gene not found in essentiality dataset'
            }

        gene_ess = gene_data.iloc[0]['geneEssentiality']

        # Handle missing data
        if gene_ess is None or (isinstance(gene_ess, float) and np.isnan(gene_ess)):
            return {
                'gene_id': gene_id,
                'found': True,
                'is_essential': None,
                'num_tissues': 0,
                'num_cell_lines': 0,
                'essentiality_by_tissue': [],
                'note': 'Gene found but no essentiality data'
            }

        # Extract essentiality entries (usually only 1)
        # gene_ess is a numpy array, need to iterate and extract items
        if not hasattr(gene_ess, '__len__'):
            return {
                'gene_id': gene_id,
                'found': True,
                'is_essential': None,
                'num_tissues': 0,
                'num_cell_lines': 0,
                'essentiality_by_tissue': [],
                'note': 'Gene found but no essentiality data'
            }

        # Aggregate across all entries
        is_essential = None
        all_tissues = []

        for entry_array in gene_ess:
            # entry_array is a numpy 0-d array, need to extract the dict
            entry = entry_array.item() if hasattr(entry_array, 'item') else entry_array
            if entry.get('isEssential') is not None:
                is_essential = entry['isEssential']

            depmap_ess_list = entry.get('depMapEssentiality')
            if depmap_ess_list is None:
                continue
            if isinstance(depmap_ess_list, float) and np.isnan(depmap_ess_list):
                continue

            # Process each tissue
            for tissue_entry in depmap_ess_list:
                tissue_id = tissue_entry.get('tissueId')
                tissue_name = tissue_entry.get('tissueName', '')

                # Apply tissue filter
                if tissue and tissue.lower() not in tissue_name.lower():
                    continue

                screens = tissue_entry.get('screens')
                if screens is None or (isinstance(screens, float) and np.isnan(screens)):
                    continue

                # Process cell line screens
                filtered_screens = []
                gene_effects = []

                for screen in screens:
                    # Apply disease filter
                    if disease:
                        disease_from_source = screen.get('diseaseFromSource', '')
                        if disease.lower() not in disease_from_source.lower():
                            continue

                    # Extract screen data
                    gene_effect = screen.get('geneEffect')
                    if gene_effect is not None:
                        gene_effects.append(gene_effect)

                    filtered_screens.append({
                        'cellLineName': screen.get('cellLineName'),
                        'depmapId': screen.get('depmapId'),
                        'disease': screen.get('diseaseFromSource'),
                        'expression': screen.get('expression'),
                        'geneEffect': gene_effect,
                        'mutation': screen.get('mutation')
                    })

                if len(filtered_screens) > 0:
                    # Calculate statistics
                    mean_effect = np.mean(gene_effects) if gene_effects else None

                    # Count essential (gene effect < -0.5 is typical threshold)
                    essential_count = sum(1 for ge in gene_effects if ge is not None and ge < -0.5)
                    essential_fraction = essential_count / len(gene_effects) if gene_effects else 0

                    all_tissues.append({
                        'tissue': {
                            'id': tissue_id,
                            'name': tissue_name
                        },
                        'num_cell_lines': len(filtered_screens),
                        'mean_gene_effect': mean_effect,
                        'essential_fraction': essential_fraction,
                        'cell_lines': filtered_screens
                    })

        # Count total cell lines
        total_cell_lines = sum(t['num_cell_lines'] for t in all_tissues)

        return {
            'gene_id': gene_id,
            'found': True,
            'is_essential': is_essential,
            'num_tissues': len(all_tissues),
            'num_cell_lines': total_cell_lines,
            'essentiality_by_tissue': all_tissues
        }

    except Exception as e:
        return {
            'gene_id': gene_id,
            'found': False,
            'error': f'Query failed: {str(e)}'
        }


def find_essential_genes(
    disease: str,
    min_effect_threshold: float = -0.5,
    min_cell_lines: int = 3,
    limit: int = 100
) -> dict:
    """
    Find genes that are essential in a specific disease context

    ⚠️ CRITICAL LIMITATION: ALL DATA IS FROM CANCER CELL LINES ONLY (DepMap)

    This tool finds genes essential for CANCER cell survival. Only use when:
    - Investigating cancer-related diseases
    - Assessing cancer-type selectivity
    - Validating targets in cancer context

    DO NOT use for non-cancer diseases (Alzheimer's, diabetes, etc.) - not applicable.

    Identifies genes with strong negative gene effect scores (dependencies)
    in cancer cell lines of a specified disease type.

    Args:
        disease: Disease name - MUST BE A CANCER TYPE (partial match on diseaseFromSource)
        min_effect_threshold: Maximum gene effect score (more negative = more essential)
                              Default: -0.5 (typical essentiality threshold)
        min_cell_lines: Minimum number of cell lines to consider
        limit: Maximum number of genes to return

    Returns:
        Dictionary with:
        - disease: Disease queried
        - num_results: Number of essential genes found
        - genes: List of essential genes with:
            - gene_id: Ensembl ID
            - mean_gene_effect: Average dependency score
            - num_cell_lines: Count of cell lines tested
            - essential_fraction: Fraction showing essentiality
            - top_cell_lines: Examples of cell lines with strong dependency

    Example:
        # Find essential genes in colorectal cancer
        result = find_essential_genes("Colorectal", min_effect_threshold=-0.5, limit=50)

        # Find highly essential genes (strong dependencies)
        result = find_essential_genes("Lung", min_effect_threshold=-1.0, limit=20)
    """
    try:
        essentiality = _get_loader().get_dataset("target_essentiality")

        essential_genes = []

        for _, row in essentiality.iterrows():
            gene_id = row['id']
            gene_ess = row['geneEssentiality']

            # Skip if no data
            if gene_ess is None or (isinstance(gene_ess, float) and np.isnan(gene_ess)):
                continue

            # Collect all matching screens for this gene
            gene_effects = []
            cell_line_examples = []

            for entry_array in gene_ess:
                # entry_array is a numpy 0-d array, need to extract the dict
                entry = entry_array.item() if hasattr(entry_array, 'item') else entry_array
                depmap_ess_list = entry.get('depMapEssentiality')
                if depmap_ess_list is None or (isinstance(depmap_ess_list, float) and np.isnan(depmap_ess_list)):
                    continue

                for tissue_entry in depmap_ess_list:
                    screens = tissue_entry.get('screens')
                    if screens is None or (isinstance(screens, float) and np.isnan(screens)):
                        continue

                    for screen in screens:
                        disease_from_source = screen.get('diseaseFromSource', '')

                        # Apply disease filter
                        if disease.lower() not in disease_from_source.lower():
                            continue

                        gene_effect = screen.get('geneEffect')
                        if gene_effect is None:
                            continue

                        gene_effects.append(gene_effect)

                        # Store example if strongly essential
                        if gene_effect <= min_effect_threshold:
                            cell_line_examples.append({
                                'cellLineName': screen.get('cellLineName'),
                                'disease': disease_from_source,
                                'geneEffect': gene_effect,
                                'expression': screen.get('expression')
                            })

            # Check if gene meets criteria
            if len(gene_effects) < min_cell_lines:
                continue

            mean_effect = np.mean(gene_effects)

            # Count essential cell lines
            essential_count = sum(1 for ge in gene_effects if ge <= min_effect_threshold)
            essential_fraction = essential_count / len(gene_effects)

            # Must have at least some essential cell lines
            if essential_count == 0:
                continue

            # Sort examples by gene effect (most negative first)
            cell_line_examples.sort(key=lambda x: x['geneEffect'])

            essential_genes.append({
                'gene_id': gene_id,
                'mean_gene_effect': mean_effect,
                'num_cell_lines': len(gene_effects),
                'essential_fraction': essential_fraction,
                'top_cell_lines': cell_line_examples[:5]
            })

        # Sort by mean gene effect (most negative = most essential)
        essential_genes.sort(key=lambda x: x['mean_gene_effect'])

        return {
            'disease': disease,
            'num_results': len(essential_genes[:limit]),
            'genes': essential_genes[:limit]
        }

    except Exception as e:
        return {
            'disease': disease,
            'error': f'Query failed: {str(e)}',
            'num_results': 0,
            'genes': []
        }


def query_cell_line_dependency(
    cell_line_name: str,
    min_effect_threshold: Optional[float] = -0.5,
    limit: int = 100
) -> dict:
    """
    Query gene dependencies for a specific cell line

    Finds all genes tested in a cancer cell line and their dependency scores,
    optionally filtering for essential genes.

    Args:
        cell_line_name: Cell line name (partial match)
        min_effect_threshold: If specified, only return genes with effect <= threshold
        limit: Maximum number of genes to return

    Returns:
        Dictionary with:
        - cell_line_name: Cell line queried
        - num_results: Number of gene dependencies found
        - cell_line_info: {
            - disease: Disease type
            - depmap_id: DepMap identifier (if available)
          }
        - dependencies: List of gene dependencies with:
            - gene_id: Ensembl ID
            - gene_effect: Dependency score
            - expression: Gene expression level
            - mutation: Mutation status

    Example:
        # Get all dependencies for a cell line
        result = query_cell_line_dependency("HT-29")

        # Get only essential genes
        result = query_cell_line_dependency("HT-29", min_effect_threshold=-0.5)
    """
    try:
        essentiality = _get_loader().get_dataset("target_essentiality")

        dependencies = []
        cell_line_info = None

        for _, row in essentiality.iterrows():
            gene_id = row['id']
            gene_ess = row['geneEssentiality']

            # Skip if no data
            if gene_ess is None or (isinstance(gene_ess, float) and np.isnan(gene_ess)):
                continue

            for entry_array in gene_ess:
                # entry_array is a numpy 0-d array, need to extract the dict
                entry = entry_array.item() if hasattr(entry_array, 'item') else entry_array
                depmap_ess_list = entry.get('depMapEssentiality')
                if depmap_ess_list is None or (isinstance(depmap_ess_list, float) and np.isnan(depmap_ess_list)):
                    continue

                for tissue_entry in depmap_ess_list:
                    screens = tissue_entry.get('screens')
                    if screens is None or (isinstance(screens, float) and np.isnan(screens)):
                        continue

                    for screen in screens:
                        cl_name = screen.get('cellLineName', '')

                        # Check if this is the cell line we're looking for
                        if cell_line_name.lower() not in cl_name.lower():
                            continue

                        gene_effect = screen.get('geneEffect')

                        # Apply threshold filter
                        if min_effect_threshold is not None:
                            if gene_effect is None or gene_effect > min_effect_threshold:
                                continue

                        # Store cell line info (first time we see it)
                        if cell_line_info is None:
                            cell_line_info = {
                                'disease': screen.get('diseaseFromSource'),
                                'depmap_id': screen.get('depmapId')
                            }

                        dependencies.append({
                            'gene_id': gene_id,
                            'gene_effect': gene_effect,
                            'expression': screen.get('expression'),
                            'mutation': screen.get('mutation')
                        })

        # Sort by gene effect (most essential first)
        dependencies.sort(key=lambda x: x['gene_effect'] if x['gene_effect'] is not None else 0)

        return {
            'cell_line_name': cell_line_name,
            'num_results': len(dependencies[:limit]),
            'cell_line_info': cell_line_info,
            'dependencies': dependencies[:limit]
        }

    except Exception as e:
        return {
            'cell_line_name': cell_line_name,
            'error': f'Query failed: {str(e)}',
            'num_results': 0,
            'dependencies': []
        }


def compare_essentiality_across_diseases(
    gene_id: str,
    diseases: Optional[List[str]] = None
) -> dict:
    """
    Compare gene essentiality across multiple diseases

    ⚠️ CRITICAL LIMITATION: ALL DATA IS FROM CANCER CELL LINES ONLY (DepMap)

    This compares essentiality across CANCER types, not general diseases.
    Use for cancer selectivity analysis only.

    Provides comparative statistics to identify disease-specific vs pan-cancer
    essentiality patterns.

    Args:
        gene_id: Ensembl gene ID
        diseases: Optional list of CANCER TYPES to compare
                  If None, compares across all available cancer types

    Returns:
        Dictionary with:
        - gene_id: Gene identifier
        - found: Whether gene was found
        - num_diseases_compared: Number of diseases in comparison
        - disease_essentiality: List sorted by mean effect with:
            - disease: Disease name
            - num_cell_lines: Count of cell lines
            - mean_gene_effect: Average dependency score
            - essential_fraction: Fraction showing essentiality (effect < -0.5)
            - rank: Rank by essentiality (1=most essential)

    Example:
        # Compare across all diseases
        result = compare_essentiality_across_diseases("ENSG00000139618")

        # Compare specific diseases
        result = compare_essentiality_across_diseases(
            "ENSG00000139618",
            diseases=["Colorectal", "Lung", "Breast"]
        )
    """
    try:
        essentiality = _get_loader().get_dataset("target_essentiality")

        # Find gene
        gene_data = essentiality[essentiality['id'] == gene_id]

        if len(gene_data) == 0:
            return {
                'gene_id': gene_id,
                'found': False,
                'error': 'Gene not found'
            }

        gene_ess = gene_data.iloc[0]['geneEssentiality']

        # Handle missing data
        if gene_ess is None or (isinstance(gene_ess, float) and np.isnan(gene_ess)):
            return {
                'gene_id': gene_id,
                'found': True,
                'num_diseases_compared': 0,
                'note': 'Gene found but no essentiality data'
            }

        # Aggregate by disease
        disease_data = {}

        for entry_array in gene_ess:
            # entry_array is a numpy 0-d array, need to extract the dict
            entry = entry_array.item() if hasattr(entry_array, 'item') else entry_array
            depmap_ess_list = entry.get('depMapEssentiality')
            if depmap_ess_list is None or (isinstance(depmap_ess_list, float) and np.isnan(depmap_ess_list)):
                continue

            for tissue_entry in depmap_ess_list:
                screens = tissue_entry.get('screens')
                if screens is None or (isinstance(screens, float) and np.isnan(screens)):
                    continue

                for screen in screens:
                    disease = screen.get('diseaseFromSource', 'Unknown')

                    # Apply disease filter
                    if diseases:
                        match = any(d.lower() in disease.lower() for d in diseases)
                        if not match:
                            continue

                    gene_effect = screen.get('geneEffect')
                    if gene_effect is None:
                        continue

                    if disease not in disease_data:
                        disease_data[disease] = []

                    disease_data[disease].append(gene_effect)

        # Calculate statistics for each disease
        disease_list = []

        for disease, effects in disease_data.items():
            mean_effect = np.mean(effects)
            essential_count = sum(1 for e in effects if e < -0.5)
            essential_fraction = essential_count / len(effects)

            disease_list.append({
                'disease': disease,
                'num_cell_lines': len(effects),
                'mean_gene_effect': mean_effect,
                'essential_fraction': essential_fraction
            })

        # Sort by mean gene effect (most essential first)
        disease_list.sort(key=lambda x: x['mean_gene_effect'])

        # Add ranks
        for i, d in enumerate(disease_list):
            d['rank'] = i + 1

        return {
            'gene_id': gene_id,
            'found': True,
            'num_diseases_compared': len(disease_list),
            'disease_essentiality': disease_list
        }

    except Exception as e:
        return {
            'gene_id': gene_id,
            'found': False,
            'error': f'Comparison failed: {str(e)}'
        }


def find_selective_dependencies(
    target_disease: str,
    comparison_disease: str,
    min_effect_difference: float = 0.3,
    min_cell_lines: int = 3,
    limit: int = 50
) -> dict:
    """
    Find genes with disease-selective essentiality

    ⚠️ CRITICAL LIMITATION: ALL DATA IS FROM CANCER CELL LINES ONLY (DepMap)

    This finds selectivity between CANCER TYPES, not general diseases.
    Only use when comparing cancer types (e.g., breast vs colorectal cancer).

    Identifies genes that are essential in one cancer type but not in another,
    revealing potential therapeutic targets with cancer selectivity.

    Args:
        target_disease: CANCER TYPE where gene should be essential
        comparison_disease: CANCER TYPE where gene should NOT be essential
        min_effect_difference: Minimum difference in mean gene effect
                               (target_effect - comparison_effect)
        min_cell_lines: Minimum cell lines in each disease to consider
        limit: Maximum number of genes to return

    Returns:
        Dictionary with:
        - target_disease: Disease with selective essentiality
        - comparison_disease: Disease without essentiality
        - num_results: Number of selective genes found
        - genes: List sorted by selectivity with:
            - gene_id: Ensembl ID
            - target_effect: Mean gene effect in target disease
            - comparison_effect: Mean gene effect in comparison disease
            - effect_difference: Selectivity score (negative = more selective)
            - target_cell_lines: Count in target disease
            - comparison_cell_lines: Count in comparison disease

    Example:
        # Find genes essential in lung but not breast cancer
        result = find_selective_dependencies(
            target_disease="Lung",
            comparison_disease="Breast",
            min_effect_difference=0.3,
            limit=20
        )
    """
    try:
        essentiality = _get_loader().get_dataset("target_essentiality")

        selective_genes = []

        for _, row in essentiality.iterrows():
            gene_id = row['id']
            gene_ess = row['geneEssentiality']

            # Skip if no data
            if gene_ess is None or (isinstance(gene_ess, float) and np.isnan(gene_ess)):
                continue

            # Collect effects for both diseases
            target_effects = []
            comparison_effects = []

            for entry_array in gene_ess:
                # entry_array is a numpy 0-d array, need to extract the dict
                entry = entry_array.item() if hasattr(entry_array, 'item') else entry_array
                depmap_ess_list = entry.get('depMapEssentiality')
                if depmap_ess_list is None or (isinstance(depmap_ess_list, float) and np.isnan(depmap_ess_list)):
                    continue

                for tissue_entry in depmap_ess_list:
                    screens = tissue_entry.get('screens')
                    if screens is None or (isinstance(screens, float) and np.isnan(screens)):
                        continue

                    for screen in screens:
                        disease = screen.get('diseaseFromSource', '')
                        gene_effect = screen.get('geneEffect')

                        if gene_effect is None:
                            continue

                        if target_disease.lower() in disease.lower():
                            target_effects.append(gene_effect)
                        elif comparison_disease.lower() in disease.lower():
                            comparison_effects.append(gene_effect)

            # Check if we have enough data
            if len(target_effects) < min_cell_lines or len(comparison_effects) < min_cell_lines:
                continue

            target_mean = np.mean(target_effects)
            comparison_mean = np.mean(comparison_effects)

            # Calculate selectivity (negative values mean essential in target, not in comparison)
            effect_difference = target_mean - comparison_mean

            # Check if difference meets threshold (target should be more negative)
            if effect_difference >= -min_effect_difference:
                continue

            selective_genes.append({
                'gene_id': gene_id,
                'target_effect': target_mean,
                'comparison_effect': comparison_mean,
                'effect_difference': effect_difference,
                'target_cell_lines': len(target_effects),
                'comparison_cell_lines': len(comparison_effects)
            })

        # Sort by effect difference (most negative = most selective)
        selective_genes.sort(key=lambda x: x['effect_difference'])

        return {
            'target_disease': target_disease,
            'comparison_disease': comparison_disease,
            'num_results': len(selective_genes[:limit]),
            'genes': selective_genes[:limit]
        }

    except Exception as e:
        return {
            'target_disease': target_disease,
            'comparison_disease': comparison_disease,
            'error': f'Query failed: {str(e)}',
            'num_results': 0,
            'genes': []
        }


# =============================================================================
# TAHOE DRUG PERTURBATION TOOLS
# =============================================================================

def _get_drug_metadata(drug_name: str) -> Optional[dict]:
    """Helper: Get drug metadata with error handling"""
    try:
        drug_meta = _get_loader().get_tahoe_metadata('drug')
        drug_row = drug_meta[drug_meta['drug'] == drug_name]
        
        if len(drug_row) == 0:
            return None
        
        row = drug_row.iloc[0]
        return convert_to_native_types({
            'name': row['drug'],
            'moa_broad': row.get('moa-broad', None),
            'moa_fine': row.get('moa-fine', None),
            'targets': row.get('targets', None),
            'human_approved': row.get('human-approved', None),
            'clinical_trials': row.get('clinical-trials', None),
            'canonical_smiles': row.get('canonical_smiles', None),
            'pubchem_cid': row.get('pubchem_cid', None)
        })
    except Exception as e:
        return {'name': drug_name, 'error': str(e)}


def _get_cell_line_metadata(cell_line_id: str) -> Optional[dict]:
    """Helper: Get cell line metadata with error handling"""
    try:
        cell_meta = _get_loader().get_tahoe_metadata('cell_line')
        cell_row = cell_meta[cell_meta['Cell_ID_DepMap'] == cell_line_id]
        
        if len(cell_row) == 0:
            return None
        
        row = cell_row.iloc[0]
        return convert_to_native_types({
            'depmap_id': row['Cell_ID_DepMap'],
            'cell_name': row.get('cell_name', None),
            'organ': row.get('Organ', None),
            'driver_gene': row.get('Driver_Gene_Symbol', None),
            'driver_mutations': row.get('Driver mutations', None)
        })
    except Exception as e:
        return {'depmap_id': cell_line_id, 'error': str(e)}


def query_drug_perturbation(
    drug_name: str,
    cell_line_id: Optional[str] = None,
    top_n: int = 50,
    min_abs_log2fc: float = 0.0,
    max_padj: float = 0.10
) -> dict:
    """
    Query drug perturbation effects from Tahoe-100M

    Returns top differentially expressed genes for a drug treatment,
    with drug and cell line metadata automatically embedded.

    Data source: Tahoe-100M perturbation atlas (4B single-cell perturbations)
    All data from CANCER CELL LINES only (DepMap collection).

    Args:
        drug_name: Drug name (e.g., 'Doxorubicin', 'Vorinostat')
        cell_line_id: Optional DepMap cell line ID filter (e.g., 'ACH-000956')
                     If None, aggregates across all cell lines
        top_n: Number of top genes to return per direction (default: 50)
        min_abs_log2fc: Minimum absolute log2 fold change (default: 0.0)
        max_padj: Maximum adjusted p-value (default: 0.10)

    Returns:
        Dictionary with:
        - drug_info: {name, MOA, targets, clinical_status, SMILES, etc.}
        - cell_line_info: {name, DepMap_ID, cancer_type, tissue} (if filtered)
        - top_upregulated: List of top upregulated genes with stats
        - top_downregulated: List of top downregulated genes with stats
        - num_total_significant: Total genes meeting criteria
        - query_params: Parameters used for query

    Example:
        result = query_drug_perturbation("Doxorubicin", cell_line_id="ACH-000956", top_n=20)
    """
    try:
        # Get dataset and metadata
        dataset = _get_loader().get_tahoe_dataset('tahoe_pseudobulk_permissive')
        drug_meta = _get_drug_metadata(drug_name)
        
        # Build filter
        filter_expr = (
            (ds.field('drug') == drug_name) &
            (ds.field('padj') <= max_padj)
        )
        
        if cell_line_id:
            filter_expr = filter_expr & (ds.field('Cell_ID_DepMap') == cell_line_id)
        
        if min_abs_log2fc > 0:
            filter_expr = filter_expr & (
                (ds.field('log2FoldChange') > min_abs_log2fc) |
                (ds.field('log2FoldChange') < -min_abs_log2fc)
            )
        
        # Execute query
        results = dataset.to_table(filter=filter_expr).to_pandas()
        
        if len(results) == 0:
            return {
                'drug_info': drug_meta,
                'cell_line_info': _get_cell_line_metadata(cell_line_id) if cell_line_id else None,
                'top_upregulated': [],
                'top_downregulated': [],
                'num_total_significant': 0,
                'message': 'No significant perturbations found matching criteria',
                'query_params': {
                    'drug_name': drug_name,
                    'cell_line_id': cell_line_id,
                    'top_n': top_n,
                    'min_abs_log2fc': min_abs_log2fc,
                    'max_padj': max_padj
                }
            }
        
        # Split up/downregulated
        upregulated = results[results['log2FoldChange'] > 0].nsmallest(top_n, 'padj')
        downregulated = results[results['log2FoldChange'] < 0].nsmallest(top_n, 'padj')
        
        # Format results
        def format_gene_result(row):
            return convert_to_native_types({
                'gene_name': row['gene_name'],
                'log2FoldChange': row['log2FoldChange'],
                'padj': row['padj'],
                'pvalue': row['pvalue'],
                'baseMean': row['baseMean'],
                'stat': row['stat'],
                'cell_line': row.get('Cell_ID_DepMap', None),
                'n_cells_treatment': row.get('n_cells_trt', None),
                'n_cells_control': row.get('n_cells_ctrl', None)
            })
        
        return {
            'drug_info': drug_meta,
            'cell_line_info': _get_cell_line_metadata(cell_line_id) if cell_line_id else None,
            'top_upregulated': [format_gene_result(row) for _, row in upregulated.iterrows()],
            'top_downregulated': [format_gene_result(row) for _, row in downregulated.iterrows()],
            'num_total_significant': len(results),
            'num_cell_lines_tested': results['Cell_ID_DepMap'].nunique() if 'Cell_ID_DepMap' in results.columns else 1,
            'query_params': {
                'drug_name': drug_name,
                'cell_line_id': cell_line_id,
                'top_n': top_n,
                'min_abs_log2fc': min_abs_log2fc,
                'max_padj': max_padj
            }
        }
        
    except Exception as e:
        return {
            'error': f'Query failed: {str(e)}',
            'drug_name': drug_name,
            'cell_line_id': cell_line_id
        }


def find_drugs_affecting_gene(
    gene_name: str,
    cell_line_filter: Optional[str] = None,
    min_abs_log2fc: float = 0.5,
    max_padj: float = 0.10,
    top_n: int = 20
) -> dict:
    """
    Find drugs that significantly affect a specific gene

    Reverse query: given a gene, find which drugs modulate its expression.
    Useful for target validation and finding chemical modulators.

    Data source: Tahoe-100M (cancer cell lines only).

    Args:
        gene_name: Gene symbol (e.g., 'TP53', 'EGFR', 'MYC')
        cell_line_filter: Optional cell line ID for filtering
        min_abs_log2fc: Minimum absolute fold change (default: 0.5 = 1.4x)
        max_padj: Maximum adjusted p-value (default: 0.10)
        top_n: Number of top drugs to return per direction (default: 20)

    Returns:
        Dictionary with:
        - gene_name: Query gene
        - num_drugs_found: Total drugs affecting this gene
        - top_upregulators: Drugs that upregulate (with stats and metadata)
        - top_downregulators: Drugs that downregulate (with stats and metadata)
        - cell_line_specificity: Effect consistency across cell lines

    Example:
        result = find_drugs_affecting_gene("TP53", min_abs_log2fc=1.0)
    """
    try:
        # Get dataset
        dataset = _get_loader().get_tahoe_dataset('tahoe_pseudobulk_permissive')
        
        # Build filter
        filter_expr = (
            (ds.field('gene_name') == gene_name) &
            (ds.field('padj') <= max_padj) &
            ((ds.field('log2FoldChange') > min_abs_log2fc) |
             (ds.field('log2FoldChange') < -min_abs_log2fc))
        )
        
        if cell_line_filter:
            filter_expr = filter_expr & (ds.field('Cell_ID_DepMap') == cell_line_filter)
        
        # Execute query
        results = dataset.to_table(filter=filter_expr).to_pandas()
        
        if len(results) == 0:
            return {
                'gene_name': gene_name,
                'num_drugs_found': 0,
                'top_upregulators': [],
                'top_downregulators': [],
                'message': 'No drugs found affecting this gene with specified criteria'
            }
        
        # Split by direction
        upregulators = results[results['log2FoldChange'] > 0].nsmallest(top_n, 'padj')
        downregulators = results[results['log2FoldChange'] < 0].nsmallest(top_n, 'padj')
        
        # Format with metadata
        def format_drug_result(row):
            drug_info = _get_drug_metadata(row['drug'])
            return convert_to_native_types({
                'drug_name': row['drug'],
                'log2FoldChange': row['log2FoldChange'],
                'padj': row['padj'],
                'cell_line': row.get('Cell_ID_DepMap', None),
                'drug_metadata': drug_info
            })
        
        return convert_to_native_types({
            'gene_name': gene_name,
            'num_drugs_found': results['drug'].nunique(),
            'num_cell_lines_tested': results['Cell_ID_DepMap'].nunique() if 'Cell_ID_DepMap' in results.columns else 0,
            'top_upregulators': [format_drug_result(row) for _, row in upregulators.iterrows()],
            'top_downregulators': [format_drug_result(row) for _, row in downregulators.iterrows()],
            'query_params': {
                'gene_name': gene_name,
                'cell_line_filter': cell_line_filter,
                'min_abs_log2fc': min_abs_log2fc,
                'max_padj': max_padj,
                'top_n': top_n
            }
        })
        
    except Exception as e:
        return {
            'error': f'Query failed: {str(e)}',
            'gene_name': gene_name
        }


def compare_drug_effects(
    drug_a: str,
    drug_b: str,
    cell_line_id: Optional[str] = None,
    min_abs_log2fc: float = 0.5,
    max_padj: float = 0.10
) -> dict:
    """
    Compare transcriptomic signatures between two drugs

    Analyzes similarity and differences in drug effects. Useful for:
    - MOA similarity assessment
    - Drug repositioning opportunities
    - Understanding off-target effects

    Data source: Tahoe-100M (cancer cell lines only).

    Args:
        drug_a: First drug name
        drug_b: Second drug name
        cell_line_id: Optional cell line filter (recommended for cleaner comparison)
        min_abs_log2fc: Minimum fold change for comparison (default: 0.5)
        max_padj: Maximum p-value (default: 0.10)

    Returns:
        Dictionary with:
        - drug_a_info: Metadata for drug A
        - drug_b_info: Metadata for drug B
        - signature_correlation: Pearson correlation of log2FC values
        - shared_targets_same_direction: Genes affected by both (same direction)
        - opposite_effects: Genes affected opposite directions
        - unique_to_a: Genes only affected by drug A
        - unique_to_b: Genes only affected by drug B
        - similarity_score: Overall signature similarity (0-1)

    Example:
        result = compare_drug_effects("Vorinostat", "Romidepsin", cell_line_id="ACH-000911")
    """
    try:
        dataset = _get_loader().get_tahoe_dataset('tahoe_pseudobulk_permissive')
        
        # Build filters for each drug
        base_filter = (ds.field('padj') <= max_padj)
        if min_abs_log2fc > 0:
            base_filter = base_filter & (
                (ds.field('log2FoldChange') > min_abs_log2fc) |
                (ds.field('log2FoldChange') < -min_abs_log2fc)
            )
        if cell_line_id:
            base_filter = base_filter & (ds.field('Cell_ID_DepMap') == cell_line_id)
        
        filter_a = base_filter & (ds.field('drug') == drug_a)
        filter_b = base_filter & (ds.field('drug') == drug_b)
        
        # Query both drugs
        results_a = dataset.to_table(filter=filter_a).to_pandas()
        results_b = dataset.to_table(filter=filter_b).to_pandas()
        
        if len(results_a) == 0 or len(results_b) == 0:
            return {
                'drug_a_info': _get_drug_metadata(drug_a),
                'drug_b_info': _get_drug_metadata(drug_b),
                'error': f'Insufficient data: drug_a has {len(results_a)} hits, drug_b has {len(results_b)} hits'
            }
        
        # Merge on gene for comparison
        merged = results_a[['gene_name', 'log2FoldChange', 'padj']].merge(
            results_b[['gene_name', 'log2FoldChange', 'padj']],
            on='gene_name',
            suffixes=('_a', '_b')
        )
        
        # Calculate correlation
        correlation = merged['log2FoldChange_a'].corr(merged['log2FoldChange_b']) if len(merged) > 1 else 0.0
        
        # Identify shared vs unique genes
        genes_a = set(results_a['gene_name'])
        genes_b = set(results_b['gene_name'])
        shared_genes = genes_a & genes_b
        unique_a = genes_a - genes_b
        unique_b = genes_b - genes_a
        
        # Categorize shared genes by direction
        shared_same_direction = merged[
            ((merged['log2FoldChange_a'] > 0) & (merged['log2FoldChange_b'] > 0)) |
            ((merged['log2FoldChange_a'] < 0) & (merged['log2FoldChange_b'] < 0))
        ]
        
        shared_opposite = merged[
            ((merged['log2FoldChange_a'] > 0) & (merged['log2FoldChange_b'] < 0)) |
            ((merged['log2FoldChange_a'] < 0) & (merged['log2FoldChange_b'] > 0))
        ]
        
        # Format shared targets
        def format_shared(row):
            return convert_to_native_types({
                'gene_name': row['gene_name'],
                'log2FC_drug_a': row['log2FoldChange_a'],
                'log2FC_drug_b': row['log2FoldChange_b'],
                'padj_drug_a': row['padj_a'],
                'padj_drug_b': row['padj_b']
            })
        
        # Calculate similarity score (based on correlation and overlap)
        overlap_score = len(shared_genes) / max(len(genes_a), len(genes_b)) if genes_a or genes_b else 0
        # Use the SIGNED correlation clamped at 0: anti-correlated (opposite-MoA)
        # signatures contribute 0 similarity, not maximal similarity as abs() gave.
        similarity_score = (max(0.0, correlation) + overlap_score) / 2.0
        
        return convert_to_native_types({
            'drug_a_info': _get_drug_metadata(drug_a),
            'drug_b_info': _get_drug_metadata(drug_b),
            'cell_line_info': _get_cell_line_metadata(cell_line_id) if cell_line_id else None,
            'signature_correlation': correlation,
            'shared_targets_same_direction': [format_shared(row) for _, row in shared_same_direction.head(30).iterrows()],
            'opposite_effects': [format_shared(row) for _, row in shared_opposite.head(20).iterrows()],
            'unique_to_a': list(unique_a)[:30],
            'unique_to_b': list(unique_b)[:30],
            'similarity_score': similarity_score,
            'stats': {
                'num_shared_genes': len(shared_genes),
                'num_shared_same_direction': len(shared_same_direction),
                'num_opposite_effects': len(shared_opposite),
                'num_unique_to_a': len(unique_a),
                'num_unique_to_b': len(unique_b),
                'num_genes_a': len(genes_a),
                'num_genes_b': len(genes_b)
            }
        })
        
    except Exception as e:
        return {
            'error': f'Query failed: {str(e)}',
            'drug_a': drug_a,
            'drug_b': drug_b
        }


def find_cell_line_selective_effects(
    drug_name: str,
    cell_line_of_interest: str,
    comparison_cell_lines: Optional[List[str]] = None,
    min_abs_log2fc: float = 0.5,
    max_padj: float = 0.10,
    selectivity_threshold: float = 2.0,
    top_n: int = 30
) -> dict:
    """
    Find genes with cell line-selective drug effects

    Identifies genes that respond differently in one cell line vs others.
    Useful for understanding cancer-type selective vulnerabilities.

    Data source: Tahoe-100M (cancer cell lines only).

    Args:
        drug_name: Drug to analyze
        cell_line_of_interest: DepMap ID of cell line to analyze
        comparison_cell_lines: Optional list of cell lines to compare against
                             (if None, uses all other available cell lines)
        min_abs_log2fc: Minimum fold change in cell line of interest (default: 0.5)
        max_padj: Maximum p-value for significance (default: 0.10)
        selectivity_threshold: Fold change ratio threshold for selectivity (default: 2.0)
                              Selectivity = |FC_interest| / mean(|FC_others|)
        top_n: Number of top selective genes to return (default: 30)

    Returns:
        Dictionary with:
        - drug_info: Drug metadata
        - cell_line_of_interest_info: Target cell line metadata
        - num_comparison_lines: Number of cell lines compared
        - selective_genes: Genes with selective effects, sorted by selectivity
          Each with: gene, log2FC_target, mean_log2FC_others, selectivity_ratio, padj
        - num_selective: Total selective genes found

    Example:
        result = find_cell_line_selective_effects(
            "Doxorubicin",
            "ACH-000956",
            selectivity_threshold=3.0
        )
    """
    try:
        dataset = _get_loader().get_tahoe_dataset('tahoe_pseudobulk_permissive')
        
        # Query drug across all cell lines
        drug_filter = (
            (ds.field('drug') == drug_name) &
            (ds.field('padj') <= max_padj)
        )
        
        results = dataset.to_table(filter=drug_filter).to_pandas()
        
        if len(results) == 0:
            return {
                'drug_info': _get_drug_metadata(drug_name),
                'cell_line_of_interest_info': _get_cell_line_metadata(cell_line_of_interest),
                'error': 'No perturbation data found for this drug'
            }
        
        # Split target vs comparison
        target_data = results[results['Cell_ID_DepMap'] == cell_line_of_interest]
        
        if comparison_cell_lines:
            comparison_data = results[results['Cell_ID_DepMap'].isin(comparison_cell_lines)]
        else:
            comparison_data = results[results['Cell_ID_DepMap'] != cell_line_of_interest]
        
        if len(target_data) == 0:
            return {
                'drug_info': _get_drug_metadata(drug_name),
                'cell_line_of_interest_info': _get_cell_line_metadata(cell_line_of_interest),
                'error': f'No data for cell line {cell_line_of_interest}'
            }
        
        if len(comparison_data) == 0:
            return {
                'drug_info': _get_drug_metadata(drug_name),
                'cell_line_of_interest_info': _get_cell_line_metadata(cell_line_of_interest),
                'error': 'No comparison cell lines with data'
            }
        
        # Filter target by min FC
        target_data = target_data[target_data['log2FoldChange'].abs() >= min_abs_log2fc].copy()
        
        # Calculate mean effect in comparison lines per gene
        comparison_data = comparison_data.copy()
        comparison_data['abs_log2FoldChange'] = comparison_data['log2FoldChange'].abs()
        comparison_stats = comparison_data.groupby('gene_name').agg({
            'log2FoldChange': ['mean', 'std', 'count'],
            'abs_log2FoldChange': 'mean'
        }).reset_index()
        comparison_stats.columns = ['gene_name', 'mean_log2FC', 'std_log2FC', 'n_cell_lines', 'mean_abs_log2FC']
        
        # Merge with target data
        selective_analysis = target_data.merge(
            comparison_stats,
            on='gene_name',
            how='left'
        )
        
        # Calculate selectivity ratio.
        # Use mean(|FC|) across comparison lines (per the docstring), NOT |mean(FC)| —
        # otherwise opposite-direction responses cancel and inflate selectivity.
        selective_analysis['abs_target_fc'] = selective_analysis['log2FoldChange'].abs()
        selective_analysis['abs_mean_comparison_fc'] = selective_analysis['mean_abs_log2FC']
        
        # Avoid division by zero
        selective_analysis['selectivity_ratio'] = selective_analysis.apply(
            lambda row: row['abs_target_fc'] / max(row['abs_mean_comparison_fc'], 0.1),
            axis=1
        )
        
        # Filter by selectivity threshold
        selective_genes = selective_analysis[
            selective_analysis['selectivity_ratio'] >= selectivity_threshold
        ].sort_values('selectivity_ratio', ascending=False)
        
        # Format results
        def format_selective_gene(row):
            return convert_to_native_types({
                'gene_name': row['gene_name'],
                'log2FC_target': row['log2FoldChange'],
                'mean_log2FC_comparison': row['mean_log2FC'],
                'selectivity_ratio': row['selectivity_ratio'],
                'padj_target': row['padj'],
                'n_comparison_cell_lines': row['n_cell_lines']
            })
        
        return convert_to_native_types({
            'drug_info': _get_drug_metadata(drug_name),
            'cell_line_of_interest_info': _get_cell_line_metadata(cell_line_of_interest),
            'num_comparison_lines': comparison_data['Cell_ID_DepMap'].nunique(),
            'selective_genes': [format_selective_gene(row) for _, row in selective_genes.head(top_n).iterrows()],
            'num_selective': len(selective_genes),
            'query_params': {
                'drug_name': drug_name,
                'cell_line_of_interest': cell_line_of_interest,
                'selectivity_threshold': selectivity_threshold,
                'min_abs_log2fc': min_abs_log2fc,
                'max_padj': max_padj,
                'top_n': top_n
            }
        })
        
    except Exception as e:
        return {
            'error': f'Query failed: {str(e)}',
            'drug_name': drug_name,
            'cell_line_of_interest': cell_line_of_interest
        }
