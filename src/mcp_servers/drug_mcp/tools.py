"""
Drug MCP Tools
Tool implementations for drug information, mechanisms, and safety queries
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


# Export all tools
__all__ = [
    'search_drugs',
    'get_drug_info',
    'get_target_tractability',
    'get_drug_indications',
    'get_drug_warnings',
    'get_drug_adverse_events',
    'get_drug_mechanisms',
    'search_known_drugs',
    'get_pharmacogenomics'
]
