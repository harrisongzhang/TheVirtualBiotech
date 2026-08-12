"""
Disease MCP Tools
Tool implementations for disease ontology and phenotype queries
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


def get_disease_info(disease_id: str) -> dict:
    """
    Get comprehensive disease annotations from Open Targets

    Args:
        disease_id: EFO disease ID (e.g., 'EFO_0000685' for rheumatoid arthritis)

    Returns:
        Dictionary with disease information:
        - id: EFO disease ID
        - name: Disease name
        - description: Detailed description
        - therapeuticAreas: High-level therapeutic categories
        - synonyms: Alternative names
        - parents: Direct parent disease terms
        - children: Direct child disease terms
        - ancestors: All ancestor terms in hierarchy
        - descendants: All descendant terms
        - ontology: Ontology metadata (isTherapeuticArea, leaf, sources)
        - dbXRefs: Cross-references to other databases

    Example:
        >>> info = get_disease_info("EFO_0000685")
        >>> print(info['name'])  # rheumatoid arthritis
    """
    try:
        diseases = _get_loader().get_dataset("disease")

        # Filter to specific disease
        disease = diseases[diseases['id'] == disease_id]

        if disease.empty:
            return {
                "success": False,
                "error": f"Disease {disease_id} not found",
                "suggestion": "Use search_diseases_by_name to find diseases"
            }

        # Convert to dictionary
        disease_dict = disease.iloc[0].to_dict()

        # Convert to native types for JSON serialization
        result = convert_to_native_types(disease_dict)
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"Error getting disease info: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def search_diseases_by_name(
    query: str,
    limit: int = 20,
    therapeutic_area: Optional[str] = None
) -> dict:
    """
    Search for diseases by name or synonym

    Args:
        query: Search term (case-insensitive, partial matches)
        limit: Maximum number of results to return (default: 20)
        therapeutic_area: Optional filter by therapeutic area (e.g., 'MONDO_0045024' for cancer)

    Returns:
        Dictionary with:
        - success: Boolean indicating if search succeeded
        - count: Number of results found
        - results: List of matching diseases with id, name, description, therapeuticAreas, synonyms

    Example:
        >>> results = search_diseases_by_name("alzheimer")
        >>> for disease in results['results']:
        ...     print(f"{disease['id']}: {disease['name']}")
    """
    try:
        diseases = _get_loader().get_dataset("disease")

        # Case-insensitive search in name
        query_lower = query.lower()
        mask = diseases['name'].str.lower().str.contains(query_lower, na=False)

        # Also search in synonyms if available
        if 'synonyms' in diseases.columns:
            # Handle both string and list types for synonyms
            def search_synonyms(syns):
                if pd.isna(syns):
                    return False
                if isinstance(syns, str):
                    return query_lower in syns.lower()
                if isinstance(syns, (list, tuple)):
                    return any(query_lower in str(s).lower() for s in syns)
                return False

            synonym_mask = diseases['synonyms'].apply(search_synonyms)
            mask = mask | synonym_mask

        results_df = diseases[mask]

        # Filter by therapeutic area if specified
        if therapeutic_area:
            def has_therapeutic_area(areas):
                if pd.isna(areas):
                    return False
                if isinstance(areas, (list, tuple)):
                    return therapeutic_area in areas
                return False

            ta_mask = results_df['therapeuticAreas'].apply(has_therapeutic_area)
            results_df = results_df[ta_mask]

        # Limit results
        results_df = results_df.head(limit)

        # Select key fields
        key_fields = ['id', 'name', 'description', 'therapeuticAreas', 'synonyms']
        available_fields = [f for f in key_fields if f in results_df.columns]
        results_df = results_df[available_fields]

        # Convert to list of dictionaries
        results_list = results_df.to_dict('records')
        results_list = convert_to_native_types(results_list)

        return {
            "success": True,
            "query": query,
            "count": len(results_list),
            "limit": limit,
            "therapeutic_area_filter": therapeutic_area,
            "results": results_list
        }

    except Exception as e:
        logger.error(f"Error searching diseases: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def get_disease_hierarchy(disease_id: str) -> dict:
    """
    Get disease hierarchy information (parents, children, ancestors, descendants)

    Args:
        disease_id: EFO disease ID (e.g., 'EFO_0000685')

    Returns:
        Dictionary with:
        - success: Boolean
        - disease_id: Input disease ID
        - disease_name: Disease name
        - parents: List of direct parent disease IDs and names
        - children: List of direct child disease IDs and names
        - ancestors: List of all ancestor disease IDs (up to root)
        - descendants: List of all descendant disease IDs
        - is_therapeutic_area: Boolean
        - is_leaf: Boolean (true if no children)

    Example:
        >>> hierarchy = get_disease_hierarchy("EFO_0000685")
        >>> print(f"Parents: {hierarchy['parents']}")
        >>> print(f"Is leaf node: {hierarchy['is_leaf']}")
    """
    try:
        diseases = _get_loader().get_dataset("disease")

        # Get the disease
        disease = diseases[diseases['id'] == disease_id]

        if disease.empty:
            return {
                "success": False,
                "error": f"Disease {disease_id} not found"
            }

        disease_row = disease.iloc[0]
        disease_name = disease_row['name']

        # Extract hierarchy information
        parents = disease_row.get('parents', [])
        children = disease_row.get('children', [])
        ancestors = disease_row.get('ancestors', [])
        descendants = disease_row.get('descendants', [])

        # Get ontology metadata
        ontology = disease_row.get('ontology', {})
        if isinstance(ontology, dict):
            is_therapeutic_area = ontology.get('isTherapeuticArea', False)
            is_leaf = ontology.get('leaf', False)
        else:
            is_therapeutic_area = False
            is_leaf = False

        # Enrich parents and children with names
        def enrich_with_names(disease_ids):
            # Handle None, NaN, or empty lists
            if disease_ids is None:
                return []
            if isinstance(disease_ids, (list, tuple)) and len(disease_ids) == 0:
                return []
            # Check for scalar NaN
            try:
                if pd.isna(disease_ids):
                    return []
            except (ValueError, TypeError):
                # If pd.isna fails (e.g., on list), continue
                pass

            result = []
            for did in disease_ids:
                d = diseases[diseases['id'] == did]
                if not d.empty:
                    result.append({
                        "id": did,
                        "name": d.iloc[0]['name']
                    })
                else:
                    result.append({"id": did, "name": "Unknown"})
            return result

        parents_enriched = enrich_with_names(parents)
        children_enriched = enrich_with_names(children)

        # Helper to safely convert to list
        def safe_to_list(val):
            if val is None:
                return []
            if isinstance(val, (list, tuple)):
                return list(val)
            try:
                if pd.isna(val):
                    return []
            except (ValueError, TypeError):
                pass
            return list(val) if hasattr(val, '__iter__') and not isinstance(val, str) else []

        return convert_to_native_types({
            "success": True,
            "disease_id": disease_id,
            "disease_name": disease_name,
            "parents": parents_enriched,
            "children": children_enriched,
            "ancestors": safe_to_list(ancestors),
            "descendants": safe_to_list(descendants),
            "is_therapeutic_area": is_therapeutic_area,
            "is_leaf": is_leaf
        })

    except Exception as e:
        logger.error(f"Error getting disease hierarchy: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def get_disease_phenotypes(disease_id: str, limit: int = 50) -> dict:
    """
    Get Human Phenotype Ontology (HPO) terms associated with a disease

    Args:
        disease_id: Disease ID (e.g., 'MONDO_0004975' for Alzheimer's)
        limit: Maximum number of phenotypes to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - disease_id: Input disease ID
        - count: Number of associated phenotypes
        - phenotypes: List of HPO terms with:
            - phenotype: HPO term ID
            - evidence: Evidence for the association

    Example:
        >>> phenotypes = get_disease_phenotypes("MONDO_0004975")
        >>> for pheno in phenotypes['phenotypes']:
        ...     print(f"{pheno['phenotype']}")
    """
    try:
        disease_phenotype = _get_loader().get_dataset("disease_phenotype")

        # Filter to specific disease
        phenotypes_df = disease_phenotype[disease_phenotype['disease'] == disease_id]

        if phenotypes_df.empty:
            # Check if disease exists
            diseases = _get_loader().get_dataset("disease")
            disease = diseases[diseases['id'] == disease_id]

            if disease.empty:
                return {
                    "success": False,
                    "error": f"Disease {disease_id} not found"
                }
            else:
                return {
                    "success": True,
                    "disease_id": disease_id,
                    "count": 0,
                    "phenotypes": [],
                    "message": "No HPO phenotypes associated with this disease"
                }

        # Limit results
        phenotypes_df = phenotypes_df.head(limit)

        # Convert to list of dictionaries
        phenotypes_list = phenotypes_df.to_dict('records')
        phenotypes_list = convert_to_native_types(phenotypes_list)

        return {
            "success": True,
            "disease_id": disease_id,
            "count": len(phenotypes_list),
            "limit": limit,
            "phenotypes": phenotypes_list
        }

    except Exception as e:
        logger.error(f"Error getting disease phenotypes: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def find_diseases_by_therapeutic_area(
    therapeutic_area: Optional[str] = None,
    list_therapeutic_areas: bool = False,
    limit: int = 100
) -> dict:
    """
    Find diseases in a specific therapeutic area or list all therapeutic areas

    Args:
        therapeutic_area: Therapeutic area ID (e.g., 'MONDO_0045024' for cancer) or name
        list_therapeutic_areas: If True, return list of all therapeutic areas instead
        limit: Maximum number of diseases to return (default: 100)

    Returns:
        If list_therapeutic_areas=True:
            Dictionary with list of all therapeutic areas

        If therapeutic_area specified:
            Dictionary with:
            - success: Boolean
            - therapeutic_area: Queried therapeutic area
            - count: Number of diseases found
            - diseases: List of diseases with id, name, description

    Example:
        >>> # List all therapeutic areas
        >>> areas = find_diseases_by_therapeutic_area(list_therapeutic_areas=True)
        >>>
        >>> # Find cancer diseases
        >>> cancers = find_diseases_by_therapeutic_area(therapeutic_area="MONDO_0045024")
    """
    try:
        diseases = _get_loader().get_dataset("disease")

        # List all therapeutic areas
        if list_therapeutic_areas:
            # Get diseases that are therapeutic areas
            def is_therapeutic_area(ontology):
                if pd.isna(ontology):
                    return False
                if isinstance(ontology, dict):
                    return ontology.get('isTherapeuticArea', False)
                return False

            ta_diseases = diseases[diseases['ontology'].apply(is_therapeutic_area)]
            ta_list = ta_diseases[['id', 'name', 'description']].to_dict('records')
            ta_list = convert_to_native_types(ta_list)

            return {
                "success": True,
                "count": len(ta_list),
                "therapeutic_areas": ta_list
            }

        # Find diseases in a therapeutic area
        if not therapeutic_area:
            return {
                "success": False,
                "error": "Please provide therapeutic_area or set list_therapeutic_areas=True"
            }

        # Check if we need to search by name or ID
        if not therapeutic_area.startswith('MONDO_') and not therapeutic_area.startswith('EFO_'):
            # Search by name
            query_lower = therapeutic_area.lower()
            ta_match = diseases[
                diseases['name'].str.lower().str.contains(query_lower, na=False)
            ]

            # Filter to only therapeutic areas
            def is_ta(ontology):
                if pd.isna(ontology):
                    return False
                if isinstance(ontology, dict):
                    return ontology.get('isTherapeuticArea', False)
                return False

            ta_match = ta_match[ta_match['ontology'].apply(is_ta)]

            if ta_match.empty:
                return {
                    "success": False,
                    "error": f"No therapeutic area found matching '{therapeutic_area}'",
                    "suggestion": "Use list_therapeutic_areas=True to see all available therapeutic areas"
                }

            # Use first match
            therapeutic_area = ta_match.iloc[0]['id']

        # Find all diseases with this therapeutic area
        def has_ta(areas):
            if pd.isna(areas):
                return False
            if isinstance(areas, (list, tuple)):
                return therapeutic_area in areas
            return False

        results_df = diseases[diseases['therapeuticAreas'].apply(has_ta)]

        # Limit results
        results_df = results_df.head(limit)

        # Select key fields
        key_fields = ['id', 'name', 'description']
        results_df = results_df[key_fields]

        # Convert to list of dictionaries
        results_list = results_df.to_dict('records')
        results_list = convert_to_native_types(results_list)

        return {
            "success": True,
            "therapeutic_area": therapeutic_area,
            "count": len(results_list),
            "limit": limit,
            "diseases": results_list
        }

    except Exception as e:
        logger.error(f"Error finding diseases by therapeutic area: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def find_diseases_by_phenotype(
    phenotype_id: str,
    min_evidence: Optional[int] = None,
    evidence_type: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    Find diseases associated with a specific phenotype (reverse search)

    Enables clinical differential diagnosis workflows by finding all diseases
    that present with a given phenotypic abnormality. Uses HPO (Human Phenotype
    Ontology) phenotype IDs to link clinical observations to diseases.

    Args:
        phenotype_id: HPO phenotype ID (e.g., 'HP_0001250' for Seizure)
        min_evidence: Minimum number of evidence records (optional)
        evidence_type: Filter by evidence type code (optional):
            - 'IEA': Inferred from Electronic Annotation
            - 'TAS': Traceable Author Statement
            - 'PCS': Published Clinical Study
        limit: Maximum number of diseases to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - phenotype_id: Input phenotype ID
        - phenotype_name: Human-readable phenotype name (if available)
        - count: Number of associated diseases
        - diseases: List of diseases with:
            - disease_id: Disease ID (MONDO_* format)
            - disease_name: Disease name (if available)
            - evidence_count: Number of evidence records
            - evidence: Array of evidence records with:
                - evidenceType: Type of evidence (IEA, TAS, etc.)
                - frequency: Phenotype frequency in disease (if available)
                - references: Supporting references
                - resource: Source database

    Example:
        >>> # Find diseases associated with seizures
        >>> result = find_diseases_by_phenotype("HP_0001250")
        >>> for disease in result['diseases'][:5]:
        ...     print(f"{disease['disease_name']}: {disease['evidence_count']} evidence records")
        >>>
        >>> # Find diseases with high-confidence evidence
        >>> result = find_diseases_by_phenotype("HP_0001250", min_evidence=3)
    """
    try:
        disease_phenotypes = _get_loader().get_dataset("disease_phenotype")

        # Filter by phenotype
        results_df = disease_phenotypes[disease_phenotypes['phenotype'] == phenotype_id]

        if results_df.empty:
            return {
                "success": True,
                "phenotype_id": phenotype_id,
                "count": 0,
                "diseases": [],
                "message": f"No diseases found for phenotype {phenotype_id}"
            }

        # Process evidence and apply filters
        diseases_list = []

        for _, row in results_df.iterrows():
            disease_id = row['disease']
            evidence_array = row['evidence']

            # Convert evidence to list if needed
            if not isinstance(evidence_array, (list, tuple, np.ndarray)):
                evidence_list = []
            else:
                evidence_list = list(evidence_array)

            # Filter by evidence type if specified
            if evidence_type:
                evidence_list = [
                    ev for ev in evidence_list
                    if isinstance(ev, dict) and ev.get('evidenceType') == evidence_type
                ]

            # Filter by minimum evidence count
            if min_evidence and len(evidence_list) < min_evidence:
                continue

            # Extract disease name from evidence (first available)
            disease_name = None
            for ev in evidence_list:
                if isinstance(ev, dict) and 'diseaseName' in ev:
                    disease_name = ev['diseaseName']
                    break

            diseases_list.append({
                'disease_id': disease_id,
                'disease_name': disease_name,
                'evidence_count': len(evidence_list),
                'evidence': convert_to_native_types(evidence_list)
            })

        # Sort by evidence count (most evidence first)
        diseases_list.sort(key=lambda x: x['evidence_count'], reverse=True)

        # Limit results
        diseases_list = diseases_list[:limit]

        # Try to get phenotype name from disease_hpo dataset
        phenotype_name = None
        try:
            disease_hpo = _get_loader().get_dataset("disease_hpo")
            hpo_term = disease_hpo[disease_hpo['id'] == phenotype_id]
            if not hpo_term.empty:
                phenotype_name = hpo_term.iloc[0].get('name')
        except:
            pass  # If disease_hpo not available or error, continue without name

        return {
            "success": True,
            "phenotype_id": phenotype_id,
            "phenotype_name": phenotype_name,
            "count": len(diseases_list),
            "limit": limit,
            "filters_applied": {
                "min_evidence": min_evidence,
                "evidence_type": evidence_type
            },
            "diseases": diseases_list
        }

    except Exception as e:
        logger.error(f"Error finding diseases by phenotype: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }
