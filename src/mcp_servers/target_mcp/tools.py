"""
Target MCP Tools
Tool implementations for target annotation queries
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


def is_empty_field(value):
    """
    Check if a field value is empty/null, handling various pandas/numpy types

    Args:
        value: Field value to check

    Returns:
        True if value is None, NaN, or empty array/list
    """
    if value is None:
        return True
    # For scalar values, check pd.isna
    if not isinstance(value, (list, np.ndarray)):
        try:
            return pd.isna(value)
        except:
            return False
    # For arrays/lists, check if empty
    if isinstance(value, (list, np.ndarray)):
        return len(value) == 0
    return False


def get_target_info(target_id: str) -> dict:
    """
    Get comprehensive target annotations from Open Targets

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203' for APOE)

    Returns:
        Dictionary with target information:
        - id: Ensembl gene ID
        - approvedSymbol: HGNC gene symbol
        - approvedName: Full gene name
        - biotype: Gene biotype (protein_coding, etc.)
        - chromosome: Chromosome location
        - genomicLocation: Detailed genomic coordinates
        - proteinAnnotations: Protein-related annotations
        - tractability: Druggability predictions (if available)
        - subcellularLocations: Protein localization
        - functionDescriptions: Gene function

    Example:
        >>> info = get_target_info("ENSG00000130203")
        >>> print(info['approvedSymbol'])  # APOE
    """
    try:
        targets = _get_loader().get_dataset("target")

        # Filter to specific target
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "error": f"Target {target_id} not found",
                "target_id": target_id,
                "found": False
            }

        # Convert to dict (first match)
        target_dict = target.iloc[0].to_dict()

        # Convert any NaN values to None and handle numpy types
        result = convert_to_native_types(target_dict)
        result['found'] = True
        return result

    except Exception as e:
        logger.error(f"Error in get_target_info: {e}")
        return {
            "error": str(e),
            "target_id": target_id,
            "found": False
        }


def search_targets_by_name(
    query: str,
    limit: int = 10
) -> dict:
    """
    Search for targets by gene symbol or name

    Useful for finding targets when you have a gene name but need the Ensembl ID

    Args:
        query: Gene symbol or name to search (e.g., 'APOE', 'TP53', 'breast cancer')
        limit: Maximum number of results to return (default: 10)

    Returns:
        Dictionary with:
        - query: Original search query
        - num_results: Number of results found
        - results: List of matching targets with:
          - id: Ensembl gene ID
          - approvedSymbol: HGNC gene symbol
          - approvedName: Full gene name
          - biotype: Gene biotype

    Example:
        >>> results = search_targets_by_name("APOE")
        >>> print(results['results'][0]['id'])  # ENSG00000130203
    """
    try:
        targets = _get_loader().get_dataset("target")

        query_lower = query.lower()

        # Search in symbol and name
        mask = (
            targets['approvedSymbol'].str.lower().str.contains(query_lower, na=False) |
            targets['approvedName'].str.lower().str.contains(query_lower, na=False)
        )

        matches = targets[mask].head(limit)

        if len(matches) == 0:
            return {
                "query": query,
                "num_results": 0,
                "results": [],
                "message": f"No targets found matching '{query}'"
            }

        # Convert to list of dicts with key fields
        results = []
        for _, row in matches.iterrows():
            results.append({
                'id': row['id'],
                'approvedSymbol': row.get('approvedSymbol'),
                'approvedName': row.get('approvedName'),
                'biotype': row.get('biotype')
            })

        return convert_to_native_types({
            "query": query,
            "num_results": len(results),
            "results": results
        })

    except Exception as e:
        logger.error(f"Error in search_targets_by_name: {e}")
        return {
            "query": query,
            "num_results": 0,
            "results": [],
            "error": str(e)
        }


def get_target_tractability(target_id: str) -> dict:
    """
    Get druggability/tractability predictions for a target

    Indicates whether target is druggable by different modalities

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - target_id: Gene ID
        - found: Whether target was found
        - tractability: Druggability predictions by modality
          - antibody: Antibody tractability
          - smallMolecule: Small molecule druggability
          - protac: PROTAC/degrader potential
          - other: Other modality predictions

    Example:
        >>> tract = get_target_tractability("ENSG00000130203")
        >>> if tract['tractability']:
        ...     print(tract['tractability']['smallMolecule'])
    """
    try:
        targets = _get_loader().get_dataset("target")

        # Filter to target
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "target_id": target_id,
                "found": False,
                "error": f"Target {target_id} not found"
            }

        # Extract tractability field
        tractability_raw = target.iloc[0].get('tractability', None)

        # Handle NaN - pandas may return nan which fails boolean check
        import pandas as pd
        import numpy as np

        # Check for None first, then handle pandas/numpy NaN
        if tractability_raw is None:
            tractability = None
        elif isinstance(tractability_raw, float) and np.isnan(tractability_raw):
            tractability = None
        else:
            tractability = tractability_raw

        if tractability is None:
            return {
                "target_id": target_id,
                "found": True,
                "tractability": None,
                "message": "No tractability data available for this target"
            }

        return {
            "target_id": target_id,
            "found": True,
            "tractability": convert_to_native_types(tractability)
        }

    except Exception as e:
        logger.error(f"Error in get_target_tractability: {e}")
        return {
            "target_id": target_id,
            "found": False,
            "error": str(e)
        }


def search_drugs(
    query: str = None,
    target_id: str = None,
    mechanism: str = None,
    limit: int = 20
) -> dict:
    """
    Search drugs by name, target, or mechanism of action

    Args:
        query: Drug name search term (case-insensitive, partial match)
        target_id: Filter by target Ensembl ID
        mechanism: Filter by mechanism of action
        limit: Maximum number of results (default: 20)

    Returns:
        Dictionary with:
        - success: Boolean indicating success
        - count: Number of drugs found
        - drugs: List of drug information dictionaries

    Example:
        >>> # Search by drug name
        >>> drugs = search_drugs(query="aspirin")
        >>> # Find drugs for a target
        >>> drugs = search_drugs(target_id="ENSG00000130203")
    """
    try:
        drugs = _get_loader().get_dataset("drug_molecule")

        # Start with all drugs
        mask = pd.Series([True] * len(drugs))

        # Apply filters
        if query:
            query_lower = query.lower()
            mask &= drugs['name'].str.lower().str.contains(query_lower, na=False)

        if target_id:
            # Check if drug has this target
            def has_target(targets):
                if targets is None or (isinstance(targets, float) and pd.isna(targets)):
                    return False
                if isinstance(targets, (list, tuple)):
                    return target_id in targets
                return False

            mask &= drugs['linkedTargets'].apply(has_target)

        if mechanism:
            mechanism_lower = mechanism.lower()
            mask &= drugs['mechanismOfAction'].str.lower().str.contains(mechanism_lower, na=False)

        result_df = drugs[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "drugs": [],
                "message": "No drugs found matching criteria"
            }

        # Select key fields
        key_fields = ['id', 'name', 'mechanismOfAction', 'maximumClinicalTrialPhase', 'linkedTargets']
        available_fields = [f for f in key_fields if f in result_df.columns]
        result_df = result_df[available_fields]

        drugs_list = result_df.to_dict('records')
        drugs_list = convert_to_native_types(drugs_list)

        return {
            "success": True,
            "count": len(drugs_list),
            "limit": limit,
            "drugs": drugs_list
        }

    except Exception as e:
        logger.error(f"Error searching drugs: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_drug_info(drug_id: str) -> dict:
    """
    Get comprehensive drug information including properties and clinical phase

    Args:
        drug_id: Drug identifier (ChEMBL ID)

    Returns:
        Dictionary with drug properties:
        - id: Drug identifier
        - name: Drug name
        - mechanismOfAction: How the drug works
        - maximumClinicalTrialPhase: Highest trial phase (0-4)
        - linkedTargets: Associated targets
        - drugType: Type of drug (small molecule, antibody, etc.)

    Example:
        >>> drug = get_drug_info("CHEMBL25")
    """
    try:
        drugs = _get_loader().get_dataset("drug_molecule")

        drug_df = drugs[drugs['id'] == drug_id]

        if drug_df.empty:
            return {
                "success": False,
                "error": f"Drug {drug_id} not found"
            }

        drug_dict = drug_df.iloc[0].to_dict()
        drug_dict = convert_to_native_types(drug_dict)
        drug_dict["success"] = True

        return drug_dict

    except Exception as e:
        logger.error(f"Error getting drug info: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_drug_indications(drug_id: str, limit: int = 20) -> dict:
    """
    Get disease indications and clinical trial phases for a drug

    Args:
        drug_id: Drug identifier (ChEMBL ID)
        limit: Maximum number of indications to return

    Returns:
        Dictionary with:
        - success: Boolean
        - drug_id: Drug identifier
        - count: Number of indications
        - indications: List of disease indications with trial phases

    Example:
        >>> indications = get_drug_indications("CHEMBL25")
    """
    try:
        indications = _get_loader().get_dataset("drug_indication")

        drug_indications = indications[indications['id'] == drug_id].head(limit)

        if drug_indications.empty:
            return {
                "success": True,
                "drug_id": drug_id,
                "count": 0,
                "indications": [],
                "message": "No indications found for this drug"
            }

        indications_list = drug_indications.to_dict('records')
        indications_list = convert_to_native_types(indications_list)

        return {
            "success": True,
            "drug_id": drug_id,
            "count": len(indications_list),
            "indications": indications_list
        }

    except Exception as e:
        logger.error(f"Error getting drug indications: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_drug_warnings(drug_id: str, limit: int = 20) -> dict:
    """
    Get safety warnings for a drug

    Args:
        drug_id: Drug identifier (ChEMBL ID)
        limit: Maximum number of warnings to return

    Returns:
        Dictionary with:
        - success: Boolean
        - drug_id: Drug identifier
        - count: Number of warnings
        - warnings: List of safety warnings

    Example:
        >>> warnings = get_drug_warnings("CHEMBL25")
    """
    try:
        warnings = _get_loader().get_dataset("drug_warning")

        # chemblIds is a list/array, check if drug_id is in it
        def has_drug_id(chembl_ids):
            if chembl_ids is None:
                return False
            try:
                if pd.isna(chembl_ids):
                    return False
            except (ValueError, TypeError):
                pass
            if isinstance(chembl_ids, (list, tuple, np.ndarray)):
                return drug_id in chembl_ids
            return chembl_ids == drug_id

        mask = warnings['chemblIds'].apply(has_drug_id).astype(bool)
        drug_warnings = warnings[mask].head(limit)

        if drug_warnings.empty:
            return {
                "success": True,
                "drug_id": drug_id,
                "count": 0,
                "warnings": [],
                "message": "No warnings found for this drug"
            }

        warnings_list = drug_warnings.to_dict('records')
        warnings_list = convert_to_native_types(warnings_list)

        return {
            "success": True,
            "drug_id": drug_id,
            "count": len(warnings_list),
            "warnings": warnings_list
        }

    except Exception as e:
        logger.error(f"Error getting drug warnings: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_target_prioritisation_scores(target_id: str) -> dict:
    """
    Get multi-factor target prioritisation scores

    This tool provides composite scoring across multiple evidence types
    to help prioritize drug targets for specific diseases.

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203')

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Target identifier
        - prioritisations: List of disease-target prioritisation scores

    Example:
        >>> scores = get_target_prioritisation_scores("ENSG00000130203")
    """
    try:
        prioritisations = _get_loader().get_dataset("target_prioritisation")

        target_scores = prioritisations[prioritisations['targetId'] == target_id]

        if target_scores.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "prioritisations": [],
                "message": "No prioritisation data for this target"
            }

        scores_list = target_scores.to_dict('records')
        scores_list = convert_to_native_types(scores_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(scores_list),
            "prioritisations": scores_list
        }

    except Exception as e:
        logger.error(f"Error getting target prioritisation: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def prioritize_targets(
    has_pocket: bool = None,
    has_small_molecule_binder: bool = None,
    no_safety_events: bool = False,
    min_genetic_constraint: float = None,
    min_clinical_phase: int = None,
    is_membrane: bool = None,
    is_secreted: bool = None,
    sort_by: str = None,
    ascending: bool = False,
    limit: int = 100
) -> dict:
    """
    Multi-factor target prioritization for drug discovery

    Filters and ranks targets based on druggability, safety, genetic evidence,
    clinical validation, and localization criteria. This enables discovery
    workflows like "find druggable, safe targets with genetic evidence".

    Dataset: target_prioritisation (78,726 genes)
    Fields available for filtering:
    - Druggability: hasPocket, hasLigand, hasSmallMoleculeBinder
    - Safety: hasSafetyEvent
    - Genetics: geneticConstraint (normalized score ~[-1, 1]; LOWER = more constrained)
    - Clinical: maxClinicalTrialPhase (0-4)
    - Localization: isInMembrane, isSecreted
    - Expression: tissueSpecificity, tissueDistribution
    - Mouse: mouseKOScore

    Args:
        has_pocket: Filter to targets with binding pockets (hasPocket == 1)
        has_small_molecule_binder: Filter to targets with small molecule binders
        no_safety_events: Exclude targets with safety liabilities
        min_genetic_constraint: Threshold on the normalized geneticConstraint score
            (range ~[-1, 1]). LOWER scores mean MORE constrained (LoF-intolerant).
            Targets are filtered to the most-constrained end (geneticConstraint <=
            this value) and ranked most-constrained first, so pass a low/negative
            value (e.g. -0.5) to select genuinely constrained genes.
        min_clinical_phase: Minimum clinical trial phase (0-4)
        is_membrane: Filter to membrane proteins (isInMembrane > 0.5)
        is_secreted: Filter to secreted proteins (isSecreted > 0.5)
        sort_by: Column to sort by (e.g., 'geneticConstraint', 'maxClinicalTrialPhase')
        ascending: Sort order (default False = highest scores first)
        limit: Maximum number of results to return (default 100)

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of targets matching criteria
        - filters_applied: Summary of filters used
        - targets: List of target records with all prioritization scores

    Examples:
        # Find druggable, highly-constrained targets with genetic evidence
        >>> prioritize_targets(has_pocket=True, min_genetic_constraint=-0.5, limit=20)

        # Find safe, clinically validated targets
        >>> prioritize_targets(no_safety_events=True, min_clinical_phase=2, sort_by='maxClinicalTrialPhase', limit=50)

        # Find secreted proteins for antibody therapeutics, most-constrained first
        >>> prioritize_targets(is_secreted=True, no_safety_events=True, sort_by='geneticConstraint', ascending=True, limit=30)

        # Find membrane targets (GPCR-like)
        >>> prioritize_targets(is_membrane=True, has_small_molecule_binder=True, sort_by='maxClinicalTrialPhase', limit=20)
    """
    try:
        prioritisations = _get_loader().get_dataset("target_prioritisation")

        # Start with all targets
        filtered = prioritisations.copy()
        filters_applied = {}

        # Apply druggability filters
        if has_pocket is not None:
            if has_pocket:
                filtered = filtered[filtered['hasPocket'] == 1.0]
                filters_applied['has_pocket'] = True
            else:
                filtered = filtered[(filtered['hasPocket'] != 1.0) | (filtered['hasPocket'].isna())]
                filters_applied['has_pocket'] = False

        if has_small_molecule_binder is not None:
            if has_small_molecule_binder:
                filtered = filtered[filtered['hasSmallMoleculeBinder'] == 1.0]
                filters_applied['has_small_molecule_binder'] = True
            else:
                filtered = filtered[(filtered['hasSmallMoleculeBinder'] != 1.0) | (filtered['hasSmallMoleculeBinder'].isna())]
                filters_applied['has_small_molecule_binder'] = False

        # Apply safety filter
        if no_safety_events:
            # Exclude targets with safety events
            filtered = filtered[(filtered['hasSafetyEvent'] != 1.0) | (filtered['hasSafetyEvent'].isna())]
            filters_applied['no_safety_events'] = True

        # Apply genetic constraint filter
        if min_genetic_constraint is not None:
            # geneticConstraint is a normalized score (~[-1, 1]) where LOWER =
            # MORE constrained (LoF-intolerant) and HIGHER = LESS constrained.
            # To keep the most-constrained genes, select the low end and rank
            # ascending so the most-constrained targets come first.
            filtered = filtered[filtered['geneticConstraint'] <= min_genetic_constraint]
            filtered = filtered.sort_values(
                by='geneticConstraint', ascending=True, na_position='last'
            )
            filters_applied['min_genetic_constraint'] = min_genetic_constraint

        # Apply clinical phase filter
        if min_clinical_phase is not None:
            filtered = filtered[filtered['maxClinicalTrialPhase'] >= min_clinical_phase]
            filters_applied['min_clinical_phase'] = min_clinical_phase

        # Apply membrane localization filter
        if is_membrane is not None:
            if is_membrane:
                filtered = filtered[filtered['isInMembrane'] > 0.5]
                filters_applied['is_membrane'] = True
            else:
                filtered = filtered[(filtered['isInMembrane'] <= 0.5) | (filtered['isInMembrane'].isna())]
                filters_applied['is_membrane'] = False

        # Apply secreted localization filter
        if is_secreted is not None:
            if is_secreted:
                filtered = filtered[filtered['isSecreted'] > 0.5]
                filters_applied['is_secreted'] = True
            else:
                filtered = filtered[(filtered['isSecreted'] <= 0.5) | (filtered['isSecreted'].isna())]
                filters_applied['is_secreted'] = False

        # Apply sorting if specified
        if sort_by is not None:
            if sort_by in filtered.columns:
                # Sort, handling NaN by placing them at the end
                filtered = filtered.sort_values(by=sort_by, ascending=ascending, na_position='last')
                filters_applied['sort_by'] = sort_by
                filters_applied['ascending'] = ascending
            else:
                logger.warning(f"Sort column '{sort_by}' not found in dataset")

        # Apply limit
        filtered = filtered.head(limit)

        # Convert to list of dicts
        targets_list = filtered.to_dict('records')
        targets_list = convert_to_native_types(targets_list)

        return {
            "success": True,
            "count": len(targets_list),
            "filters_applied": filters_applied,
            "targets": targets_list
        }

    except Exception as e:
        logger.error(f"Error prioritizing targets: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_target_safety_profile(target_id: str, limit: int = 20) -> dict:
    """
    Get adverse event profile for a target from OpenFDA data

    Args:
        target_id: Ensembl gene ID
        limit: Maximum number of adverse events to return

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Target identifier
        - count: Number of adverse events
        - adverse_events: List of significant adverse reactions

    Example:
        >>> safety = get_target_safety_profile("ENSG00000130203")
    """
    try:
        adverse = _get_loader().get_dataset("openfda_significant_adverse_target_reactions")

        target_adverse = adverse[adverse['targetId'] == target_id].head(limit)

        if target_adverse.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "adverse_events": [],
                "message": "No adverse event data for this target"
            }

        adverse_list = target_adverse.to_dict('records')
        adverse_list = convert_to_native_types(adverse_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(adverse_list),
            "adverse_events": adverse_list
        }

    except Exception as e:
        logger.error(f"Error getting target safety profile: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_mouse_phenotype(target_id: str, limit: int = 20) -> dict:
    """
    Get mouse knockout phenotypes for a target

    Provides information about what happens when the gene is knocked out
    in mice, which can inform target validation and predict side effects.

    Args:
        target_id: Ensembl gene ID
        limit: Maximum number of phenotypes to return

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Target identifier
        - count: Number of phenotypes
        - phenotypes: List of mouse phenotypes

    Example:
        >>> phenotypes = get_mouse_phenotype("ENSG00000130203")
    """
    try:
        mouse = _get_loader().get_dataset("mouse_phenotype")

        target_phenotypes = mouse[mouse['targetInModelEnsemblId'] == target_id].head(limit)

        if target_phenotypes.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "phenotypes": [],
                "message": "No mouse phenotype data for this target"
            }

        phenotypes_list = target_phenotypes.to_dict('records')
        phenotypes_list = convert_to_native_types(phenotypes_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(phenotypes_list),
            "phenotypes": phenotypes_list
        }

    except Exception as e:
        logger.error(f"Error getting mouse phenotypes: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_pharmacogenomics(target_id: str = None, drug_id: str = None, limit: int = 20) -> dict:
    """
    Get pharmacogenomics relationships between genes and drug responses

    Args:
        target_id: Ensembl gene ID (optional)
        drug_id: Drug identifier (optional)
        limit: Maximum number of PGx relationships to return

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of PGx relationships
        - pgx_relationships: List of gene-drug relationships

    Example:
        >>> pgx = get_pharmacogenomics(target_id="ENSG00000130203")
        >>> pgx = get_pharmacogenomics(drug_id="CHEMBL25")
    """
    try:
        pgx = _get_loader().get_dataset("pharmacogenomics")

        if target_id:
            result_df = pgx[pgx['targetFromSourceId'] == target_id].head(limit)
        elif drug_id:
            # drugs is a list, check if drug_id is in the list
            def has_drug(drugs):
                if drugs is None or (isinstance(drugs, float) and pd.isna(drugs)):
                    return False
                if isinstance(drugs, (list, tuple)):
                    # Check if any drug dict in list has matching id
                    for drug in drugs:
                        if isinstance(drug, dict) and drug.get('id') == drug_id:
                            return True
                return False

            mask = pgx['drugs'].apply(has_drug)
            result_df = pgx[mask].head(limit)
        else:
            return {
                "success": False,
                "error": "Must provide target_id or drug_id"
            }

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "pgx_relationships": [],
                "message": "No pharmacogenomics data found"
            }

        pgx_list = result_df.to_dict('records')
        pgx_list = convert_to_native_types(pgx_list)

        return {
            "success": True,
            "count": len(pgx_list),
            "pgx_relationships": pgx_list
        }

    except Exception as e:
        logger.error(f"Error getting pharmacogenomics: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_drug_adverse_events(drug_id: str, limit: int = 20) -> dict:
    """
    Get significant adverse drug reactions from OpenFDA data

    Uses log-likelihood ratio (LLR) analysis to identify adverse events
    significantly associated with a drug compared to background rates.

    Args:
        drug_id: Drug identifier (ChEMBL ID)
        limit: Maximum number of adverse events to return

    Returns:
        Dictionary with:
        - success: Boolean
        - drug_id: Drug identifier
        - count: Number of adverse events
        - adverse_events: List of significant adverse reactions with:
            - event: Adverse event description
            - count: Number of reports
            - llr: Log likelihood ratio (higher = stronger association)
            - critval: Critical value threshold
            - meddraCode: MedDRA code

    Example:
        >>> events = get_drug_adverse_events("CHEMBL25")
    """
    try:
        adverse = _get_loader().get_dataset("openfda_significant_adverse_drug_reactions")

        drug_adverse = adverse[adverse['chembl_id'] == drug_id].head(limit)

        if drug_adverse.empty:
            return {
                "success": True,
                "drug_id": drug_id,
                "count": 0,
                "adverse_events": [],
                "message": "No adverse event data for this drug"
            }

        adverse_list = drug_adverse.to_dict('records')
        adverse_list = convert_to_native_types(adverse_list)

        # Sort by LLR (log likelihood ratio) descending to show most significant first
        adverse_list = sorted(adverse_list, key=lambda x: x.get('llr', 0), reverse=True)

        return {
            "success": True,
            "drug_id": drug_id,
            "count": len(adverse_list),
            "adverse_events": adverse_list
        }

    except Exception as e:
        logger.error(f"Error getting drug adverse events: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_drug_mechanisms(drug_id: str = None, mechanism: str = None, limit: int = 20) -> dict:
    """
    Get detailed drug mechanism of action information

    Provides comprehensive mechanism data including action types, targets,
    and literature references. More detailed than the mechanismOfAction
    field in drug_molecule dataset.

    Args:
        drug_id: Drug identifier (ChEMBL ID, optional)
        mechanism: Search term for mechanism of action (optional)
        limit: Maximum number of results to return

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of mechanisms found
        - mechanisms: List of mechanism details with:
          - actionType: Type of action (inhibitor, agonist, etc.)
          - mechanismOfAction: Description of mechanism
          - chemblIds: Associated ChEMBL drug IDs
          - targetName: Target protein/gene name
          - targetType: Type of target (enzyme, receptor, etc.)
          - targets: List of Ensembl target IDs
          - references: Supporting literature

    Example:
        >>> # Get mechanisms for a specific drug
        >>> mechanisms = get_drug_mechanisms(drug_id="CHEMBL25")
        >>> # Search for specific mechanism types
        >>> mechanisms = get_drug_mechanisms(mechanism="kinase inhibitor")
    """
    try:
        moa_data = _get_loader().get_dataset("drug_mechanism_of_action")

        if drug_id:
            # Check if drug_id is in chemblIds list
            def has_chembl_id(chembl_ids):
                if chembl_ids is None:
                    return False
                try:
                    if pd.isna(chembl_ids):
                        return False
                except (ValueError, TypeError):
                    pass
                if isinstance(chembl_ids, (list, tuple, np.ndarray)):
                    return drug_id in chembl_ids
                return chembl_ids == drug_id

            mask = moa_data['chemblIds'].apply(has_chembl_id)
            result_df = moa_data[mask].head(limit)

        elif mechanism:
            mechanism_lower = mechanism.lower()
            mask = moa_data['mechanismOfAction'].str.lower().str.contains(mechanism_lower, na=False)
            result_df = moa_data[mask].head(limit)

        else:
            return {
                "success": False,
                "error": "Must provide drug_id or mechanism search term"
            }

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "mechanisms": [],
                "message": "No mechanism data found"
            }

        mechanisms_list = result_df.to_dict('records')
        mechanisms_list = convert_to_native_types(mechanisms_list)

        return {
            "success": True,
            "count": len(mechanisms_list),
            "mechanisms": mechanisms_list
        }

    except Exception as e:
        logger.error(f"Error getting drug mechanisms: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def search_known_drugs(
    target_id: str = None,
    disease_id: str = None,
    min_phase: int = 0,
    limit: int = 20
) -> dict:
    """
    Search for known clinical-stage drugs with target-disease relationships

    This is different from drug_molecule dataset - known_drug contains
    clinical validation data showing which drugs are in trials or approved
    for specific target-disease combinations.

    Args:
        target_id: Ensembl gene ID (optional)
        disease_id: EFO disease ID (optional)
        min_phase: Minimum clinical trial phase (0-4, where 4=approved)
        limit: Maximum number of results to return

    Returns:
        Dictionary with:
        - success: Boolean
        - count: Number of drugs found
        - drugs: List of known drugs with:
          - drugId: ChEMBL drug ID
          - targetId: Target Ensembl ID
          - diseaseId: Disease EFO ID
          - phase: Clinical trial phase (0-4)
          - status: Clinical status
          - label: Disease name
          - approvedSymbol: Target gene symbol
          - prefName: Drug name
          - drugType: Type of drug
          - mechanismOfAction: How drug works
          - targetClass: Target classification

    Example:
        >>> # Find drugs targeting a specific gene
        >>> drugs = search_known_drugs(target_id="ENSG00000130203", min_phase=2)
        >>> # Find approved drugs for a disease
        >>> drugs = search_known_drugs(disease_id="EFO_0000685", min_phase=4)
        >>> # Find all clinical drugs for target-disease pair
        >>> drugs = search_known_drugs(
        ...     target_id="ENSG00000130203",
        ...     disease_id="EFO_0000685"
        ... )
    """
    try:
        known_drugs = _get_loader().get_dataset("known_drug")

        # Start with all drugs
        mask = pd.Series([True] * len(known_drugs))

        # Apply filters
        if target_id:
            mask &= known_drugs['targetId'] == target_id

        if disease_id:
            mask &= known_drugs['diseaseId'] == disease_id

        # Filter by minimum phase
        # Handle NaN values in phase column
        def meets_phase_threshold(phase):
            if pd.isna(phase):
                return False
            return phase >= min_phase

        mask &= known_drugs['phase'].apply(meets_phase_threshold)

        result_df = known_drugs[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "drugs": [],
                "message": "No known drugs found matching criteria"
            }

        # Sort by phase (descending) to show most advanced drugs first
        result_df = result_df.sort_values('phase', ascending=False)

        drugs_list = result_df.to_dict('records')
        drugs_list = convert_to_native_types(drugs_list)

        return {
            "success": True,
            "count": len(drugs_list),
            "limit": limit,
            "filters": {
                "target_id": target_id,
                "disease_id": disease_id,
                "min_phase": min_phase
            },
            "drugs": drugs_list
        }

    except Exception as e:
        logger.error(f"Error searching known drugs: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_target_adverse_events(
    target_id: str,
    min_llr: float = None,
    min_count: int = 3,
    limit: int = 50
) -> dict:
    """
    Get significant adverse events associated with a target across all drugs

    Uses OpenFDA FAERS (FDA Adverse Event Reporting System) data aggregated
    at the target level. This shows adverse events reported for drugs that
    target this gene/protein, helping assess target-level safety liabilities.

    The log likelihood ratio (LLR) measures statistical significance of the
    adverse event association. Higher LLR = stronger signal.

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203' for APOE)
        min_llr: Optional additional floor on the log likelihood ratio. By default
                 (None) no extra floor is applied — significance is already judged
                 per row against the Open Targets Monte-Carlo critical value
                 (see Note). Set this only to further restrict results.
        min_count: Minimum number of adverse event reports (default: 3)
        limit: Maximum number of adverse events to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Input target ID
        - count: Number of significant adverse events
        - adverse_events: List of events with:
            - event: Adverse event term (MedDRA preferred term)
            - count: Number of reports
            - llr: Log likelihood ratio (statistical significance)
            - critval: Critical value threshold
            - meddraCode: MedDRA code for the event

    Example:
        >>> # Get significant adverse events for a target
        >>> events = get_target_adverse_events("ENSG00000196218", min_llr=5.0)
        >>> print(f"Found {events['count']} significant adverse events")
        >>>
        >>> # Get highly significant events only
        >>> events = get_target_adverse_events(
        ...     "ENSG00000196218",
        ...     min_llr=10.0,
        ...     min_count=10
        ... )

    Note:
        Significance is determined per row by the Monte-Carlo critical value
        (critval) supplied by Open Targets: a reaction is significant when its
        llr meets or exceeds its own critval. The dataset is already pre-filtered
        to significant rows, so no fixed LLR-to-p-value cutoff is imposed.

        This data represents adverse events reported for drugs targeting
        this gene, not necessarily caused by the target. Use in combination
        with other safety data for comprehensive target assessment.
    """
    try:
        # Load target-level adverse events dataset
        adverse_events = _get_loader().get_dataset("openfda_significant_adverse_target_reactions")

        # Filter by target
        target_events = adverse_events[adverse_events['targetId'] == target_id]

        if target_events.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "adverse_events": [],
                "message": "No adverse events found for this target"
            }

        # Apply significance filters. Significance is gated per row by the
        # Monte-Carlo critical value from Open Targets (llr >= critval) rather
        # than a fixed LLR cutoff. Fall back gracefully if critval is absent.
        if 'critval' in target_events.columns:
            mask = target_events['llr'] >= target_events['critval']
        else:
            mask = target_events['llr'].notna()
        # Optional user-supplied additional LLR floor (default None = no floor).
        if min_llr is not None:
            mask = mask & (target_events['llr'] >= min_llr)
        mask = mask & (target_events['count'] >= min_count)
        target_events = target_events[mask]

        if target_events.empty:
            floor_note = f", LLR >= {min_llr}" if min_llr is not None else ""
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "adverse_events": [],
                "message": f"No significant adverse events (llr >= critval, count >= {min_count}{floor_note})"
            }

        # Sort by LLR (most significant first)
        target_events = target_events.sort_values('llr', ascending=False)

        # Limit results
        target_events = target_events.head(limit)

        # Convert to list
        events_list = target_events.to_dict('records')
        events_list = convert_to_native_types(events_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(events_list),
            "filters": {
                "significance": "llr >= critval (per-row Monte-Carlo critical value)",
                "min_llr": min_llr,
                "min_count": min_count
            },
            "adverse_events": events_list,
            "interpretation": {
                "significance": "A reaction is significant when its llr meets or exceeds its own critval (provided by Open Targets)",
                "note": "These are adverse events reported for drugs targeting this gene"
            }
        }

    except Exception as e:
        logger.error(f"Error getting target adverse events: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_comprehensive_target_profile(
    target_id: str,
    include_diseases: bool = True,
    include_drugs: bool = True,
    include_pathways: bool = True,
    include_expression: bool = True,
    include_interactions: bool = True,
    include_safety: bool = True,
    top_n: int = 10
) -> dict:
    """
    Get comprehensive target profile integrating multiple data sources

    Creates a one-stop comprehensive profile by pulling together target
    annotations, associated diseases, known drugs, pathways, expression,
    interactions, and safety information. Essential for target deep-dives,
    due diligence, and presentations.

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203' for APOE)
        include_diseases: Include top associated diseases (default: True)
        include_drugs: Include known drugs targeting this gene (default: True)
        include_pathways: Include associated pathways (default: True)
        include_expression: Include tissue expression summary (default: True)
        include_interactions: Include key protein interactions (default: True)
        include_safety: Include safety liabilities (default: True)
        top_n: Number of top items to return for each category (default: 10)

    Returns:
        Dictionary with comprehensive profile:
        - success: Boolean
        - target_id: Input target ID
        - target_info: Basic target annotations (symbol, name, biotype, etc.)
        - tractability: Druggability predictions
        - top_diseases: Top associated diseases (if include_diseases=True)
        - known_drugs: Drugs targeting this gene (if include_drugs=True)
        - pathways: Associated pathways (if include_pathways=True)
        - expression_summary: Tissue expression overview (if include_expression=True)
        - key_interactors: Top interacting proteins (if include_interactions=True)
        - safety_profile: Safety concerns (if include_safety=True)
        - summary_stats: Quick overview statistics

    Example:
        >>> # Get full comprehensive profile
        >>> profile = get_comprehensive_target_profile("ENSG00000130203")
        >>> print(f"Target: {profile['target_info']['approvedSymbol']}")
        >>> print(f"Top disease: {profile['top_diseases'][0]['diseaseId']}")
        >>>
        >>> # Get focused profile (just target + diseases + drugs)
        >>> profile = get_comprehensive_target_profile(
        ...     "ENSG00000130203",
        ...     include_pathways=False,
        ...     include_expression=False,
        ...     include_interactions=False
        ... )
    """
    try:
        result = {
            "success": True,
            "target_id": target_id
        }

        # 1. Get basic target info and tractability
        target_result = get_target_info(target_id)
        if target_result.get('found', False):
            target_data = target_result
            result['target_info'] = {
                'approvedSymbol': target_data.get('approvedSymbol'),
                'approvedName': target_data.get('approvedName'),
                'biotype': target_data.get('biotype'),
                'chromosome': target_data.get('chromosome'),
                'description': target_data.get('functionDescriptions', [None])[0] if target_data.get('functionDescriptions') else None
            }

            # Get tractability (handle both dict and list structures)
            tractability = target_data.get('tractability', {})
            if tractability:
                if isinstance(tractability, dict):
                    result['tractability'] = {
                        'smallmolecule': tractability.get('smallmolecule', {}).get('topCategory') if isinstance(tractability.get('smallmolecule'), dict) else None,
                        'antibody': tractability.get('antibody', {}).get('topCategory') if isinstance(tractability.get('antibody'), dict) else None
                    }
                else:
                    # If it's not a dict, just include it as-is
                    result['tractability'] = tractability
            else:
                result['tractability'] = None
        else:
            result['target_info'] = None
            result['tractability'] = None

        # 2. Get top associated diseases
        if include_diseases:
            try:
                associations = _get_loader().get_dataset("association_overall_direct")
                target_assocs = associations[associations['targetId'] == target_id]
                target_assocs = target_assocs.sort_values('score', ascending=False).head(top_n)

                result['top_diseases'] = convert_to_native_types(
                    target_assocs[['diseaseId', 'score', 'evidenceCount']].to_dict('records')
                )
            except Exception as e:
                logger.warning(f"Could not load diseases: {e}")
                result['top_diseases'] = {'status': 'error', 'message': f'failed to load: {e}'}

        # 3. Get known drugs
        if include_drugs:
            drugs_result = search_known_drugs(target_id=target_id, limit=top_n)
            if drugs_result['success']:
                result['known_drugs'] = drugs_result['drugs']
            else:
                result['known_drugs'] = []

        # 4. Get pathways
        if include_pathways:
            try:
                targets = _get_loader().get_dataset("target")
                target_row = targets[targets['id'] == target_id]

                if not target_row.empty:
                    pathways = target_row.iloc[0].get('pathways', [])
                    if pathways is not None and not (isinstance(pathways, list) and len(pathways) == 0):
                        # Limit to top_n pathways
                        result['pathways'] = pathways[:top_n] if isinstance(pathways, list) else []
                    else:
                        result['pathways'] = []
                else:
                    result['pathways'] = []
            except Exception as e:
                logger.warning(f"Could not load pathways: {e}")
                result['pathways'] = {'status': 'error', 'message': f'failed to load: {e}'}

        # 5. Get expression summary
        if include_expression:
            try:
                expression = _get_loader().get_dataset("expression")
                target_expr = expression[expression['id'] == target_id]

                if not target_expr.empty:
                    tissues = target_expr.iloc[0].get('tissues', [])
                    if tissues is not None and isinstance(tissues, list) and len(tissues) > 0:
                        # Get highest expressing tissues
                        tissue_expr = []
                        for tissue in tissues:
                            if isinstance(tissue, dict) and 'rna' in tissue:
                                rna_data = tissue['rna']
                                if isinstance(rna_data, dict) and 'value' in rna_data:
                                    tissue_expr.append({
                                        'tissue': tissue.get('biosampleName', tissue.get('label', 'Unknown')),
                                        'expression': rna_data['value'],
                                        'level': rna_data.get('level')
                                    })

                        # Sort by expression and take top N
                        tissue_expr.sort(key=lambda x: x['expression'] if x['expression'] is not None else 0, reverse=True)
                        result['expression_summary'] = tissue_expr[:top_n]
                    else:
                        result['expression_summary'] = []
                else:
                    result['expression_summary'] = []
            except Exception as e:
                logger.warning(f"Could not load expression: {e}")
                result['expression_summary'] = {'status': 'error', 'message': f'failed to load: {e}'}

        # 6. Get key interactions
        if include_interactions:
            try:
                interactions = _get_loader().get_dataset("interaction")
                mask_a = interactions['targetA'] == target_id
                mask_b = interactions['targetB'] == target_id
                target_interactions = interactions[mask_a | mask_b]

                # Sort by count (number of supporting observations)
                if 'count' in target_interactions.columns:
                    target_interactions = target_interactions.sort_values('count', ascending=False)

                target_interactions = target_interactions.head(top_n)

                key_interactors = []
                for _, row in target_interactions.iterrows():
                    partner = row['targetB'] if row['targetA'] == target_id else row['targetA']
                    key_interactors.append({
                        'interactor_id': partner,
                        'count': row.get('count'),
                        'sourceDatabase': row.get('sourceDatabase')
                    })

                result['key_interactors'] = convert_to_native_types(key_interactors)
            except Exception as e:
                logger.warning(f"Could not load interactions: {e}")
                result['key_interactors'] = {'status': 'error', 'message': f'failed to load: {e}'}

        # 7. Get safety profile
        if include_safety:
            try:
                # Get adverse events
                adverse_result = get_target_adverse_events(target_id, min_llr=5.0, limit=5)
                if adverse_result['success']:
                    result['adverse_events'] = adverse_result['adverse_events']
                else:
                    result['adverse_events'] = []

                # Get mouse phenotypes
                mouse_result = get_mouse_phenotype(target_id, limit=5)
                if mouse_result['success']:
                    result['mouse_phenotypes'] = mouse_result.get('phenotypes', [])
                else:
                    result['mouse_phenotypes'] = []

                # Get safety liabilities from target data
                targets = _get_loader().get_dataset("target")
                target_row = targets[targets['id'] == target_id]
                if not target_row.empty:
                    safety_liabilities = target_row.iloc[0].get('safetyLiabilities', [])
                    result['safety_liabilities'] = safety_liabilities if (safety_liabilities is not None and isinstance(safety_liabilities, list) and len(safety_liabilities) > 0) else []
                else:
                    result['safety_liabilities'] = []

            except Exception as e:
                logger.warning(f"Could not load safety data: {e}")
                _err = {'status': 'error', 'message': f'failed to load: {e}'}
                result['adverse_events'] = _err
                result['mouse_phenotypes'] = _err
                result['safety_liabilities'] = _err

        # 8. Generate summary statistics
        tractability = result.get('tractability')
        has_tractability = False
        if tractability:
            if isinstance(tractability, dict):
                has_tractability = tractability.get('smallmolecule') is not None or tractability.get('antibody') is not None
            else:
                has_tractability = True  # Has some tractability info even if not dict format

        # Count only sections that loaded successfully. A section that failed to
        # load is a dict with status='error' (not a list) and is reported as None,
        # never 0 — so a data outage is never mistaken for a biological negative.
        def _count(v):
            return len(v) if isinstance(v, list) else None

        section_errors = {
            k: result[k]['message']
            for k in ('top_diseases', 'known_drugs', 'pathways', 'expression_summary',
                      'key_interactors', 'adverse_events', 'mouse_phenotypes', 'safety_liabilities')
            if isinstance(result.get(k), dict) and result[k].get('status') == 'error'
        }

        result['summary_stats'] = {
            'num_diseases': _count(result.get('top_diseases')),
            'num_drugs': _count(result.get('known_drugs')),
            'num_pathways': _count(result.get('pathways')),
            'num_tissues_expressed': _count(result.get('expression_summary')),
            'num_interactions': _count(result.get('key_interactors')),
            'num_adverse_events': _count(result.get('adverse_events')),
            'has_tractability': has_tractability
        }
        if section_errors:
            result['section_errors'] = section_errors

        return result

    except Exception as e:
        logger.error(f"Error getting comprehensive target profile: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_target_hallmarks(target_id: str) -> dict:
    """
    Get cancer hallmark annotations for a target

    Cancer hallmarks represent the biological capabilities acquired during tumor
    development. This function returns hallmark annotations showing which cancer
    hallmarks a target is implicated in based on curated evidence.

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000012048' for BRCA1)

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - hallmarks: Dict with 'attributes' and 'cancerHallmarks' arrays
        - num_hallmarks: Total number of hallmarks

    Example:
        >>> result = get_target_hallmarks("ENSG00000141510")  # TP53
        >>> for hallmark in result['cancer_hallmarks']:
        ...     print(f"{hallmark['label']}: {hallmark['description']}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        hallmarks = target_row.get('hallmarks')

        if is_empty_field(hallmarks):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "hallmarks": None,
                "num_hallmarks": 0,
                "message": "No cancer hallmark annotations available for this target"
            }

        # Convert to native types
        hallmarks_clean = convert_to_native_types(hallmarks)

        # Count total hallmarks
        num_hallmarks = 0
        if isinstance(hallmarks_clean, dict):
            cancer_hallmarks = hallmarks_clean.get('cancerHallmarks', [])
            if isinstance(cancer_hallmarks, list):
                num_hallmarks = len(cancer_hallmarks)

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "hallmarks": hallmarks_clean,
            "num_hallmarks": num_hallmarks
        }

    except Exception as e:
        logger.error(f"Error in get_target_hallmarks: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_target_tep(target_id: str) -> dict:
    """
    Get Target Enabling Package (TEP) information

    TEPs are comprehensive packages of reagents, data, and protocols to facilitate
    target validation research. This function returns TEP data including therapeutic
    area context and resource links.

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - tep: Dict with targetFromSourceId, description, therapeuticArea, url
        - has_tep: Boolean indicating if TEP is available

    Example:
        >>> result = get_target_tep("ENSG00000082397")  # EPB41L3
        >>> if result['has_tep']:
        ...     print(f"TEP available: {result['tep']['url']}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        tep = target_row.get('tep')

        if is_empty_field(tep):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "tep": None,
                "has_tep": False,
                "message": "No Target Enabling Package available for this target"
            }

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "tep": convert_to_native_types(tep),
            "has_tep": True
        }

    except Exception as e:
        logger.error(f"Error in get_target_tep: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_chemical_probes(target_id: str) -> dict:
    """
    Get available chemical probes for a target

    Chemical probes are high-quality small molecules useful for target validation.
    Returns probe information including quality metrics, mechanism of action, and
    scoring across different experimental contexts.

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - chemical_probes: List of probe dicts with drugId, id, isHighQuality,
          mechanismOfAction, scores (in cells/organisms), urls
        - num_probes: Total number of probes
        - num_high_quality: Number of high-quality probes

    Example:
        >>> result = get_chemical_probes("ENSG00000007866")  # TEAD3
        >>> for probe in result['chemical_probes']:
        ...     print(f"{probe['id']}: quality={probe['isHighQuality']}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        probes = target_row.get('chemicalProbes')

        if is_empty_field(probes):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "chemical_probes": [],
                "num_probes": 0,
                "num_high_quality": 0,
                "message": "No chemical probes available for this target"
            }

        probes_clean = convert_to_native_types(probes)
        # Ensure it's a list
        if not isinstance(probes_clean, list):
            probes_clean = [probes_clean]

        # Count high quality probes
        num_high_quality = sum(1 for p in probes_clean if p.get('isHighQuality', False))

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "chemical_probes": probes_clean,
            "num_probes": len(probes_clean),
            "num_high_quality": num_high_quality
        }

    except Exception as e:
        logger.error(f"Error in get_chemical_probes: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_genetic_constraint(target_id: str) -> dict:
    """
    Get gnomAD genetic constraint metrics for a target

    Genetic constraint measures selective pressure on genes by comparing expected
    vs observed variation. High constraint (low o/e ratio) suggests intolerance to
    loss-of-function and potential phenotypic impact.

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - constraint: List of constraint metrics by type (syn, mis, lof) with:
          - constraintType: Type of variant (synonymous, missense, loss-of-function)
          - score: Constraint score
          - exp: Expected number of variants
          - obs: Observed number of variants
          - oe: Observed/Expected ratio (lower = more constrained)
          - oeLower, oeUpper: Confidence interval bounds
          - upperRank, upperBin: Binned constraint categories
        - num_constraint_types: Number of constraint types available
        - lof_oe: Loss-of-function O/E ratio (key metric)

    Example:
        >>> result = get_genetic_constraint("ENSG00000012048")  # BRCA1
        >>> lof_constraint = [c for c in result['constraint'] if c['constraintType'] == 'lof'][0]
        >>> print(f"LoF O/E: {lof_constraint['oe']:.3f}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        constraint = target_row.get('constraint')

        if is_empty_field(constraint):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "constraint": [],
                "num_constraint_types": 0,
                "lof_oe": None,
                "message": "No genetic constraint data available for this target"
            }

        constraint_clean = convert_to_native_types(constraint)
        # Ensure it's a list
        if not isinstance(constraint_clean, list):
            constraint_clean = [constraint_clean]

        # Extract LoF O/E ratio (most important metric)
        lof_oe = None
        for c in constraint_clean:
            if c.get('constraintType') == 'lof':
                lof_oe = c.get('oe')
                break

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "constraint": constraint_clean,
            "num_constraint_types": len(constraint_clean),
            "lof_oe": lof_oe
        }

    except Exception as e:
        logger.error(f"Error in get_genetic_constraint: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_subcellular_locations(target_id: str) -> dict:
    """
    Get subcellular localization annotations for a target protein

    Returns information about where the protein is localized within the cell,
    which is important for understanding drug accessibility and delivery.

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - subcellular_locations: List of location dicts with:
          - location: Subcellular compartment name
          - source: Data source (uniprot, HPA_main, etc.)
          - termSL: Subcellular location term ID
          - labelSL: Cellular component label
        - num_locations: Number of distinct locations
        - primary_locations: List of primary location names

    Example:
        >>> result = get_subcellular_locations("ENSG00000012048")  # BRCA1
        >>> print("Locations:", ", ".join(result['primary_locations']))
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        locations = target_row.get('subcellularLocations')

        if is_empty_field(locations):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "subcellular_locations": [],
                "num_locations": 0,
                "primary_locations": [],
                "message": "No subcellular location data available for this target"
            }

        locations_clean = convert_to_native_types(locations)
        # Ensure it's a list
        if not isinstance(locations_clean, list):
            locations_clean = [locations_clean]

        # Extract primary location names
        primary_locations = []
        for loc in locations_clean:
            location_name = loc.get('location')
            if location_name and location_name not in primary_locations:
                # Clean up isoform-specific annotations
                if '[Isoform' not in location_name:
                    primary_locations.append(location_name)

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "subcellular_locations": locations_clean,
            "num_locations": len(locations_clean),
            "primary_locations": primary_locations
        }

    except Exception as e:
        logger.error(f"Error in get_subcellular_locations: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_target_class(target_id: str) -> dict:
    """
    Get ChEMBL target classification for a target

    Returns hierarchical target classification showing molecular family membership
    (e.g., Kinase, GPCR, Ion channel) which is useful for understanding target
    druggability and selecting appropriate compound libraries.

    Args:
        target_id: Ensembl gene ID

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - target_class: List of classification dicts with:
          - id: Class ID
          - label: Class name (e.g., 'Enzyme', 'Kinase')
          - level: Hierarchy level (l1, l2, l3)
        - num_classes: Number of classification entries
        - primary_class: Top-level class label

    Example:
        >>> result = get_target_class("ENSG00000012048")  # BRCA1
        >>> print(f"Target class: {result['primary_class']}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        target_class = target_row.get('targetClass')

        if is_empty_field(target_class):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "target_class": [],
                "num_classes": 0,
                "primary_class": None,
                "message": "No target class information available"
            }

        target_class_clean = convert_to_native_types(target_class)
        # Ensure it's a list
        if not isinstance(target_class_clean, list):
            target_class_clean = [target_class_clean]

        # Extract primary (top-level) class
        primary_class = None
        for tc in target_class_clean:
            if tc.get('level') == 'l1':
                primary_class = tc.get('label')
                break

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "target_class": target_class_clean,
            "num_classes": len(target_class_clean),
            "primary_class": primary_class
        }

    except Exception as e:
        logger.error(f"Error in get_target_class: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


def get_homologues(target_id: str, species_filter: str = None, min_identity: float = None) -> dict:
    """
    Get cross-species homologue information for a target

    Returns homologues across model organisms, useful for translational research
    and understanding evolutionary conservation. Includes sequence identity metrics
    and homology relationships.

    Args:
        target_id: Ensembl gene ID
        species_filter: Optional species name filter (e.g., 'Mouse', 'Chimpanzee')
        min_identity: Optional minimum query percentage identity threshold

    Returns:
        Dictionary with:
        - success: Boolean indicating if query was successful
        - target_id: Queried target ID
        - approvedSymbol: Gene symbol
        - homologues: List of homologue dicts with:
          - speciesId: NCBI taxonomy ID
          - speciesName: Species common name
          - homologyType: Type (ortholog_one2one, ortholog_one2many, etc.)
          - targetGeneId: Ensembl ID in target species
          - targetGeneSymbol: Gene symbol in target species
          - queryPercentageIdentity: Sequence identity from query perspective
          - targetPercentageIdentity: Sequence identity from target perspective
          - isHighConfidence: Quality flag
          - priority: Ranking for importance
        - num_homologues: Total number of homologues
        - model_organisms: Dict with common model organisms (mouse, rat, etc.)

    Example:
        >>> result = get_homologues("ENSG00000012048", species_filter="Mouse")
        >>> if result['model_organisms']['mouse']:
        ...     print(f"Mouse ortholog: {result['model_organisms']['mouse']['targetGeneSymbol']}")
    """
    try:
        targets = _get_loader().get_dataset("target")
        target = targets[targets['id'] == target_id]

        if len(target) == 0:
            return {
                "success": False,
                "error": f"Target {target_id} not found",
                "target_id": target_id
            }

        target_row = target.iloc[0]
        homologues = target_row.get('homologues')

        if is_empty_field(homologues):
            return {
                "success": True,
                "target_id": target_id,
                "approvedSymbol": target_row.get('approvedSymbol'),
                "homologues": [],
                "num_homologues": 0,
                "model_organisms": {},
                "message": "No homologue data available for this target"
            }

        homologues_clean = convert_to_native_types(homologues)
        # Ensure it's a list
        if not isinstance(homologues_clean, list):
            homologues_clean = [homologues_clean]

        # Apply filters
        filtered_homologues = homologues_clean
        if species_filter:
            filtered_homologues = [
                h for h in filtered_homologues
                if species_filter.lower() in h.get('speciesName', '').lower()
            ]

        if min_identity is not None:
            filtered_homologues = [
                h for h in filtered_homologues
                if h.get('queryPercentageIdentity', 0) >= min_identity
            ]

        # Extract common model organisms
        model_organisms = {}
        species_map = {
            'mouse': '10090',
            'rat': '10116',
            'zebrafish': '7955',
            'fly': '7227',
            'worm': '6239',
            'yeast': '4932'
        }

        for common_name, species_id in species_map.items():
            matching = [h for h in homologues_clean if h.get('speciesId') == species_id]
            if matching:
                model_organisms[common_name] = matching[0]
            else:
                model_organisms[common_name] = None

        return {
            "success": True,
            "target_id": target_id,
            "approvedSymbol": target_row.get('approvedSymbol'),
            "homologues": filtered_homologues,
            "num_homologues": len(filtered_homologues),
            "model_organisms": model_organisms,
            "filters_applied": {
                "species_filter": species_filter,
                "min_identity": min_identity
            }
        }

    except Exception as e:
        logger.error(f"Error in get_homologues: {e}")
        return {
            "success": False,
            "error": str(e),
            "target_id": target_id
        }


# Export target-related tools (drug tools moved to drug_mcp)
__all__ = [
    'get_target_info',
    'search_targets_by_name',
    'get_target_tractability',
    'get_target_prioritisation_scores',
    'prioritize_targets',
    'get_target_safety_profile',
    'get_mouse_phenotype',
    'get_pharmacogenomics',
    'get_comprehensive_target_profile',
    # Phase 1 - Additional target characterization tools
    'get_target_hallmarks',
    'get_target_tep',
    'get_chemical_probes',
    'get_genetic_constraint',
    'get_subcellular_locations',
    'get_target_class',
    'get_homologues'
]
