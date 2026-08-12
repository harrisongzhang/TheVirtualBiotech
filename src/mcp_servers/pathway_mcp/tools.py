"""
Pathway MCP Tools
Tool implementations for pathway and ontology queries (Reactome, GO, SO)
NOTE: Pathways and GO data are stored as nested structures in the target dataset
"""

import sys
from pathlib import Path

# Add project root to path so we can import src modules
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader
import logging
import numpy as np
import pandas as pd
from typing import List, Optional
from collections import Counter, defaultdict

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


def get_gene_pathways(target_id: str) -> dict:
    """
    Get Reactome pathways associated with a gene

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203' for APOE)

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Input gene ID
        - count: Number of pathways
        - pathways: List of pathways with pathwayId, pathway, topLevelTerm

    Example:
        >>> pathways = get_gene_pathways("ENSG00000130203")
        >>> for pw in pathways['pathways']:
        ...     print(f"{pw['pathway']} ({pw['topLevelTerm']})")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if target.empty:
            return {
                "success": False,
                "error": f"Target {target_id} not found"
            }

        pathways = target.iloc[0]['pathways']

        if pathways is None or (isinstance(pathways, (list, tuple)) and len(pathways) == 0):
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "pathways": [],
                "message": "No Reactome pathways found for this gene"
            }

        pathways_list = convert_to_native_types(pathways)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(pathways_list),
            "pathways": pathways_list
        }

    except Exception as e:
        logger.error(f"Error getting gene pathways: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def search_pathways(
    query: Optional[str] = None,
    top_level_term: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    Search Reactome pathways by name or filter by top-level category

    Args:
        query: Search term for pathway name (case-insensitive, partial matches)
        top_level_term: Filter by top-level pathway category
        limit: Maximum number of results (default: 50)

    Returns:
        Dictionary with pathways list containing pathwayId, pathway, topLevelTerm, gene_count

    Example:
        >>> pathways = search_pathways(query="immune")
    """
    try:
        targets = _get_loader().get_dataset("target")

        # Build pathway index from all targets
        pathway_index = defaultdict(lambda: {"genes": [], "pathway": "", "topLevelTerm": ""})

        for _, row in targets.iterrows():
            if row['pathways'] is None:
                continue

            # Handle both list and numpy array
            pathways_data = row['pathways']
            pathways = pathways_data if isinstance(pathways_data, (list, np.ndarray)) else []

            for pw in pathways:
                if not isinstance(pw, dict):
                    continue

                pw_id = pw.get('pathwayId')
                if pw_id:
                    pathway_index[pw_id]["genes"].append(row['id'])
                    pathway_index[pw_id]["pathway"] = pw.get('pathway', '')
                    pathway_index[pw_id]["topLevelTerm"] = pw.get('topLevelTerm', '')

        # Filter pathways
        results = []
        for pw_id, pw_data in pathway_index.items():
            pw_name = pw_data["pathway"]
            pw_top = pw_data["topLevelTerm"]

            # Apply filters
            if query:
                query_lower = query.lower()
                if query_lower not in pw_name.lower():
                    continue

            if top_level_term:
                if pw_top != top_level_term:
                    continue

            results.append({
                "pathwayId": pw_id,
                "pathway": pw_name,
                "topLevelTerm": pw_top,
                "gene_count": len(pw_data["genes"])
            })

        # Sort by gene count
        results.sort(key=lambda x: x['gene_count'], reverse=True)
        results = results[:limit]

        return convert_to_native_types({
            "success": True,
            "query": query,
            "top_level_term": top_level_term,
            "count": len(results),
            "limit": limit,
            "pathways": results
        })

    except Exception as e:
        logger.error(f"Error searching pathways: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_gene_ontology(
    target_id: str,
    aspect: Optional[str] = None
) -> dict:
    """
    Get Gene Ontology (GO) annotations for a gene

    Args:
        target_id: Ensembl gene ID
        aspect: Filter by GO aspect ('C', 'F', 'P' for cellular_component, molecular_function, biological_process)

    Returns:
        Dictionary with GO terms list

    Example:
        >>> go = get_gene_ontology("ENSG00000130203", aspect="P")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if target.empty:
            return {
                "success": False,
                "error": f"Target {target_id} not found"
            }

        go_terms = target.iloc[0]['go']

        if go_terms is None or (isinstance(go_terms, (list, tuple)) and len(go_terms) == 0):
            return {
                "success": True,
                "target_id": target_id,
                "aspect_filter": aspect,
                "count": 0,
                "go_terms": [],
                "message": "No GO annotations found for this gene"
            }

        # Convert to list and filter by aspect if needed
        go_list = go_terms if isinstance(go_terms, list) else []

        if aspect:
            go_list = [g for g in go_list if isinstance(g, dict) and g.get('aspect') == aspect]

        go_list = convert_to_native_types(go_list)

        return {
            "success": True,
            "target_id": target_id,
            "aspect_filter": aspect,
            "count": len(go_list),
            "go_terms": go_list
        }

    except Exception as e:
        logger.error(f"Error getting gene ontology: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def search_go_terms(
    query: str,
    aspect: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    Search Gene Ontology terms by GO ID

    Note: This searches GO IDs (e.g., 'GO:0006915'), not names, due to data structure.

    Args:
        query: Search term for GO ID (e.g., 'GO:0006915' or partial like '0006915')
        aspect: Filter by aspect ('C', 'F', 'P')
        limit: Maximum number of unique GO terms (default: 50)

    Returns:
        Dictionary with GO terms

    Example:
        >>> terms = search_go_terms("0006915", aspect="P")
    """
    try:
        targets = _get_loader().get_dataset("target")

        # Build GO index
        go_index = defaultdict(lambda: {"genes": [], "aspect": None})

        for _, row in targets.iterrows():
            if row['go'] is None:
                continue

            go_terms = row['go'] if isinstance(row['go'], list) else []

            for go_term in go_terms:
                if not isinstance(go_term, dict):
                    continue

                go_id = go_term.get('id')
                if go_id and query.upper() in go_id.upper():
                    go_index[go_id]["genes"].append(row['id'])
                    go_index[go_id]["aspect"] = go_term.get('aspect')

        # Filter by aspect if needed
        results = []
        for go_id, go_data in go_index.items():
            if aspect and go_data["aspect"] != aspect:
                continue

            results.append({
                "goId": go_id,
                "aspect": go_data["aspect"],
                "gene_count": len(go_data["genes"])
            })

        # Sort by gene count
        results.sort(key=lambda x: x['gene_count'], reverse=True)
        results = results[:limit]

        return convert_to_native_types({
            "success": True,
            "query": query,
            "aspect": aspect,
            "count": len(results),
            "limit": limit,
            "go_terms": results
        })

    except Exception as e:
        logger.error(f"Error searching GO terms: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def find_genes_in_pathway(pathway_id: str) -> dict:
    """
    Find all genes in a specific Reactome pathway

    Args:
        pathway_id: Reactome pathway ID (e.g., 'R-HSA-109582')

    Returns:
        Dictionary with pathway info and gene list

    Example:
        >>> genes = find_genes_in_pathway("R-HSA-109582")
    """
    try:
        targets = _get_loader().get_dataset("target")

        genes = []
        pathway_name = None
        top_level_term = None

        for _, row in targets.iterrows():
            if row['pathways'] is None:
                continue

            # Handle both list and numpy array
            pathways_data = row['pathways']
            pathways = pathways_data if isinstance(pathways_data, (list, np.ndarray)) else []

            for pw in pathways:
                if not isinstance(pw, dict):
                    continue

                if pw.get('pathwayId') == pathway_id:
                    genes.append(row['id'])
                    if pathway_name is None:
                        pathway_name = pw.get('pathway', '')
                        top_level_term = pw.get('topLevelTerm', '')

        if not genes:
            return {
                "success": False,
                "error": f"Pathway {pathway_id} not found"
            }

        return {
            "success": True,
            "pathway_id": pathway_id,
            "pathway_name": pathway_name,
            "top_level_term": top_level_term,
            "gene_count": len(genes),
            "genes": genes
        }

    except Exception as e:
        logger.error(f"Error finding genes in pathway: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_pathway_enrichment(
    gene_list: List[str],
    min_pathway_size: int = 5,
    max_pathway_size: int = 500,
    pvalue_threshold: float = 0.05,
    limit: int = 50
) -> dict:
    """
    Perform pathway (Reactome) enrichment analysis on a gene list

    Uses a hypergeometric test to identify pathways that are statistically
    overrepresented in the input gene list compared to the background of all
    genes annotated to any pathway. Multiple testing correction applied using
    Benjamini-Hochberg FDR.

    Args:
        gene_list: List of Ensembl gene IDs
        min_pathway_size: Minimum pathway size (default: 5)
        max_pathway_size: Maximum pathway size (default: 500)
        pvalue_threshold: FDR-corrected p-value threshold (default: 0.05)
        limit: Maximum enriched pathways (default: 50)

    Returns:
        Dictionary with enriched pathways, each including:
            - pvalue: Raw p-value from hypergeometric test
            - fdr: FDR-corrected p-value (Benjamini-Hochberg)
            - overlap_count: Query genes in this pathway
            - enrichment_ratio: overlap_count / pathway_size

    Example:
        >>> genes = ["ENSG00000130203", "ENSG00000171791"]
        >>> enrichment = get_pathway_enrichment(genes)
    """
    try:
        targets = _get_loader().get_dataset("target")
        gene_set = set(gene_list)

        # Build pathway index
        pathway_index = defaultdict(lambda: {"genes": set(), "pathway": "", "topLevelTerm": ""})

        for _, row in targets.iterrows():
            if row['pathways'] is None:
                continue

            # Handle both list and numpy array
            pathways_data = row['pathways']
            pathways = pathways_data if isinstance(pathways_data, (list, np.ndarray)) else []

            for pw in pathways:
                if not isinstance(pw, dict):
                    continue

                pw_id = pw.get('pathwayId')
                if pw_id:
                    pathway_index[pw_id]["genes"].add(row['id'])
                    pathway_index[pw_id]["pathway"] = pw.get('pathway', '')
                    pathway_index[pw_id]["topLevelTerm"] = pw.get('topLevelTerm', '')

        # Background gene universe: all genes annotated to any pathway
        all_pathway_genes = set()
        for pw_data in pathway_index.values():
            all_pathway_genes.update(pw_data["genes"])

        # Background sizes
        N = len(all_pathway_genes)  # Total genes in background
        query_genes = gene_set & all_pathway_genes
        n = len(query_genes)  # Query genes in background
        genes_in_pathways = n

        # Perform hypergeometric test for each pathway
        enrichment_results = []

        for pw_id, pw_data in pathway_index.items():
            pathway_genes = pw_data["genes"]
            pathway_size = len(pathway_genes)

            # Filter by size
            if pathway_size < min_pathway_size or pathway_size > max_pathway_size:
                continue

            # Calculate overlap
            overlap_genes = list(gene_set & pathway_genes)
            overlap_count = len(overlap_genes)

            # Skip pathways with no query genes
            if overlap_count == 0:
                continue

            # Hypergeometric test: P(X >= k)
            # N = population size (background genes)
            # K = successes in population (genes in this pathway)
            # n = number of draws (query genes)
            # k = observed successes (query genes in this pathway)
            K = pathway_size
            k = overlap_count
            pvalue = _hypergeom_sf(k, N, K, n)

            enrichment_results.append({
                "pathwayId": pw_id,
                "pathway": pw_data["pathway"],
                "topLevelTerm": pw_data["topLevelTerm"],
                "pathway_size": pathway_size,
                "overlap_count": overlap_count,
                "overlap_genes": overlap_genes,
                "enrichment_ratio": overlap_count / pathway_size,
                "pvalue": float(pvalue)
            })

        if len(enrichment_results) == 0:
            return convert_to_native_types({
                "success": True,
                "input_gene_count": len(gene_list),
                "genes_in_reactome": genes_in_pathways,
                "pathways_tested": 0,
                "count": 0,
                "limit": limit,
                "enriched_pathways": []
            })

        # Sort by p-value
        enrichment_results.sort(key=lambda x: x['pvalue'])

        # Apply Benjamini-Hochberg FDR correction
        m = len(enrichment_results)
        for rank, result in enumerate(enrichment_results, 1):
            fdr = result['pvalue'] * m / rank
            result['fdr'] = min(float(fdr), 1.0)  # Cap at 1.0

        # Ensure FDR is monotonically increasing (required by BH procedure)
        for i in range(len(enrichment_results) - 2, -1, -1):
            if enrichment_results[i]['fdr'] > enrichment_results[i + 1]['fdr']:
                enrichment_results[i]['fdr'] = enrichment_results[i + 1]['fdr']

        # Filter by FDR threshold
        significant = [r for r in enrichment_results if r['fdr'] <= pvalue_threshold]

        # Limit results
        significant = significant[:limit]

        return convert_to_native_types({
            "success": True,
            "input_gene_count": len(gene_list),
            "genes_in_reactome": genes_in_pathways,
            "pathways_tested": m,
            "count": len(significant),
            "limit": limit,
            "enriched_pathways": significant
        })

    except Exception as e:
        logger.error(f"Error performing pathway enrichment: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_go_term_info(go_id: str) -> dict:
    """
    Get Gene Ontology term definition

    Retrieves the official GO term name/definition from the GO ontology.
    Complements get_gene_ontology() which shows which genes have a GO annotation.

    Args:
        go_id: GO identifier (e.g., 'GO:0005737' for cytoplasm)

    Returns:
        Dictionary with:
        - success: Boolean
        - id: GO identifier
        - name: GO term name/definition

    Example:
        >>> info = get_go_term_info("GO:0005737")
        >>> print(info['name'])  # "cytoplasm"
    """
    try:
        go = _get_loader().get_dataset("go")

        go_term = go[go['id'] == go_id]

        if go_term.empty:
            return {
                "success": False,
                "error": f"GO term {go_id} not found"
            }

        result = go_term.iloc[0].to_dict()
        result = convert_to_native_types(result)
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"Error getting GO term info: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_pathway_info(pathway_id: str) -> dict:
    """
    Get Reactome pathway information with hierarchy

    Retrieves pathway metadata including parent/child relationships,
    ancestors, descendants, and hierarchical path.

    Args:
        pathway_id: Reactome pathway ID (e.g., 'R-HSA-164843')

    Returns:
        Dictionary with:
        - success: Boolean
        - id: Pathway identifier
        - label: Pathway name
        - parents: Direct parent pathways
        - children: Direct child pathways
        - ancestors: All ancestor pathways
        - descendants: All descendant pathways
        - path: Full hierarchical paths

    Example:
        >>> info = get_pathway_info("R-HSA-164843")
    """
    try:
        reactome = _get_loader().get_dataset("reactome")

        pathway = reactome[reactome['id'] == pathway_id]

        if pathway.empty:
            return {
                "success": False,
                "error": f"Pathway {pathway_id} not found"
            }

        result = pathway.iloc[0].to_dict()
        result = convert_to_native_types(result)
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"Error getting pathway info: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_sequence_ontology_term(so_id: str) -> dict:
    """
    Get Sequence Ontology term definition

    Retrieves SO term information used to classify genomic features
    and sequence variants (e.g., missense_variant, intron).

    Args:
        so_id: Sequence Ontology identifier (e.g., 'SO:0001583')

    Returns:
        Dictionary with:
        - success: Boolean
        - id: SO identifier
        - label: SO term label/definition

    Example:
        >>> info = get_sequence_ontology_term("SO:0001583")
        >>> print(info['label'])  # "missense_variant"
    """
    try:
        so = _get_loader().get_dataset("so")

        so_term = so[so['id'] == so_id]

        if so_term.empty:
            return {
                "success": False,
                "error": f"SO term {so_id} not found"
            }

        result = so_term.iloc[0].to_dict()
        result = convert_to_native_types(result)
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"Error getting SO term: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _hypergeom_sf(k, N, K, n):
    """
    Hypergeometric survival function: P(X >= k)

    Computed with scipy.stats.hypergeom in log-gamma space (no factorial
    overflow, no fabricated fallback values).

    Args:
        k: observed successes (query genes with term)
        N: population size (total background genes)
        K: successes in population (genes with term in background)
        n: number of draws (query genes)

    Returns:
        P(X >= k): probability of observing k or more successes
    """
    from scipy.stats import hypergeom

    # sf(k-1) = P(X >= k). scipy evaluates this in log-gamma space internally,
    # so there is no factorial overflow and no fabricated fallback value.
    return float(hypergeom.sf(k - 1, N, K, n))


def get_go_enrichment(
    gene_list: List[str],
    go_type: Optional[str] = None,
    pvalue_threshold: float = 0.05,
    limit: int = 50
) -> dict:
    """
    Perform Gene Ontology (GO) enrichment analysis on a gene list

    Uses hypergeometric test to identify GO terms that are statistically
    overrepresented in the input gene list compared to the background.
    Multiple testing correction applied using Benjamini-Hochberg FDR.

    Args:
        gene_list: List of Ensembl gene IDs (e.g., ['ENSG00000130203', 'ENSG00000142192'])
        go_type: Filter by GO aspect (optional):
            - 'biological_process' or 'P': Biological processes
            - 'molecular_function' or 'F': Molecular functions
            - 'cellular_component' or 'C': Cellular components
            If None, returns enriched terms across all aspects
        pvalue_threshold: FDR-corrected p-value threshold (default: 0.05)
        limit: Maximum number of enriched terms to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of enriched terms found
        - enriched_terms: List of enriched GO terms with:
            - go_id: GO term ID (e.g., 'GO:0006915')
            - go_name: GO term name
            - go_aspect: Aspect (P, F, or C)
            - pvalue: Raw p-value from hypergeometric test
            - fdr: FDR-corrected p-value (Benjamini-Hochberg)
            - query_count: Number of query genes with this term
            - query_genes: List of query gene IDs with this term
            - background_count: Total genes in background with this term
            - enrichment_ratio: (query_count / query_size) / (background_count / background_size)

    Example:
        >>> # Analyze Alzheimer's disease genes
        >>> genes = ["ENSG00000130203", "ENSG00000142192", "ENSG00000105223"]
        >>> result = get_go_enrichment(genes, go_type="biological_process", pvalue_threshold=0.05)
        >>> for term in result['enriched_terms'][:5]:
        ...     print(f"{term['go_name']}: FDR={term['fdr']:.4f}, {term['query_count']} genes")
    """
    try:

        # Load datasets
        targets = _get_loader().get_dataset("target")
        go_terms = _get_loader().get_dataset("go")

        # Map aspect codes to full names
        aspect_map = {
            'P': 'biological_process',
            'F': 'molecular_function',
            'C': 'cellular_component',
            'biological_process': 'P',
            'molecular_function': 'F',
            'cellular_component': 'C'
        }

        # Normalize go_type input
        if go_type:
            go_type_code = aspect_map.get(go_type, go_type)
        else:
            go_type_code = None

        # Filter to genes with GO annotations
        targets_with_go = targets[targets['go'].notna()]

        # Build GO term → gene mapping
        go_to_genes = defaultdict(lambda: {"genes": set(), "aspect": None, "name": None})

        for _, row in targets_with_go.iterrows():
            gene_id = row['id']
            go_annotations = row['go']

            if not isinstance(go_annotations, (list, tuple, np.ndarray)):
                continue

            for annotation in go_annotations:
                if not isinstance(annotation, dict):
                    continue

                go_id = annotation.get('id')
                aspect = annotation.get('aspect')

                if go_id and aspect:
                    # Filter by aspect if specified
                    if go_type_code and aspect != go_type_code:
                        continue

                    go_to_genes[go_id]["genes"].add(gene_id)
                    go_to_genes[go_id]["aspect"] = aspect

        # Get GO term names
        go_name_map = dict(zip(go_terms['id'], go_terms['name']))

        # Filter query genes to those in background
        background_genes = set(targets_with_go['id'])
        query_genes = set(gene_list) & background_genes

        if len(query_genes) == 0:
            return {
                "success": True,
                "count": 0,
                "enriched_terms": [],
                "message": "No query genes found in background with GO annotations",
                "query_size": len(gene_list),
                "background_size": len(background_genes)
            }

        # Background sizes
        N = len(background_genes)  # Total genes in background
        n = len(query_genes)  # Query genes in background

        # Perform hypergeometric test for each GO term
        enrichment_results = []

        for go_id, go_data in go_to_genes.items():
            term_genes = go_data["genes"]
            K = len(term_genes)  # Genes with this term in background

            # Genes in both query and term
            query_term_genes = query_genes & term_genes
            k = len(query_term_genes)

            # Skip terms with no query genes
            if k == 0:
                continue

            # Skip very small or very large terms (not informative)
            if K < 3 or K > 0.5 * N:
                continue

            # Hypergeometric test: P(X >= k)
            # N = population size (background genes)
            # K = successes in population (genes with this term)
            # n = number of draws (query genes)
            # k = observed successes (query genes with this term)
            pvalue = _hypergeom_sf(k, N, K, n)

            # Enrichment ratio
            enrichment_ratio = (k / n) / (K / N)

            enrichment_results.append({
                'go_id': go_id,
                'go_name': go_name_map.get(go_id, go_id),
                'go_aspect': go_data["aspect"],
                'pvalue': float(pvalue),
                'query_count': k,
                'query_genes': list(query_term_genes),
                'background_count': K,
                'enrichment_ratio': float(enrichment_ratio)
            })

        if len(enrichment_results) == 0:
            return {
                "success": True,
                "count": 0,
                "enriched_terms": [],
                "message": "No GO terms passed enrichment criteria",
                "query_size": n,
                "background_size": N
            }

        # Sort by p-value
        enrichment_results.sort(key=lambda x: x['pvalue'])

        # Apply Benjamini-Hochberg FDR correction
        m = len(enrichment_results)
        for rank, result in enumerate(enrichment_results, 1):
            fdr = result['pvalue'] * m / rank
            result['fdr'] = min(float(fdr), 1.0)  # Cap at 1.0

        # Ensure FDR is monotonically increasing (required by BH procedure)
        for i in range(len(enrichment_results) - 2, -1, -1):
            if enrichment_results[i]['fdr'] > enrichment_results[i + 1]['fdr']:
                enrichment_results[i]['fdr'] = enrichment_results[i + 1]['fdr']

        # Filter by FDR threshold
        significant = [r for r in enrichment_results if r['fdr'] <= pvalue_threshold]

        # Limit results
        significant = significant[:limit]

        return {
            "success": True,
            "query_size": n,
            "background_size": N,
            "go_terms_tested": m,
            "count": len(significant),
            "enriched_terms": significant
        }

    except Exception as e:
        logger.error(f"Error performing GO enrichment: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
