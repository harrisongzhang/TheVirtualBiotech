"""
Interaction MCP Tools
Provides tools for querying protein-protein interaction data

Data Structure:
- interaction: Aggregated PPI data (targetA/B, species, source databases)
- interaction_evidence: Detailed evidence with methods and scores
"""

import sys
import logging
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.data.loader import get_data_loader

logger = logging.getLogger(__name__)

# Lazy loading pattern for MCP servers
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
    """Convert numpy/pandas types to native Python types for JSON serialization"""
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


def get_interactions(
    target_id: str,
    species: str = None,
    source_database: str = None,
    limit: int = 50
) -> dict:
    """
    Get protein-protein interactions for a target gene

    Args:
        target_id: Ensembl gene ID (e.g., 'ENSG00000130203')
        species: Filter by species (e.g., 'homo sapiens')
        source_database: Filter by source database (e.g., 'intact', 'string')
        limit: Maximum number of interactions to return

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Query target
        - count: Number of interactions
        - interactions: List of interaction records with:
            - targetA/targetB: Interacting targets
            - intA/intB: Interactor identifiers
            - sourceDatabase: Data source
            - speciesA/speciesB: Species information
            - count: Number of supporting observations

    Example:
        >>> interactions = get_interactions("ENSG00000130203")
        >>> interactions = get_interactions("ENSG00000130203", species="homo sapiens")
    """
    try:
        interactions = _get_loader().get_dataset("interaction")

        # Find interactions where target is either A or B
        mask_a = interactions['targetA'] == target_id
        mask_b = interactions['targetB'] == target_id
        result_df = interactions[mask_a | mask_b]

        # Apply filters
        if species:
            species_lower = species.lower()
            species_mask_a = result_df['speciesA'].apply(
                lambda x: species_lower in str(x).lower() if pd.notna(x) else False
            )
            species_mask_b = result_df['speciesB'].apply(
                lambda x: species_lower in str(x).lower() if pd.notna(x) else False
            )
            result_df = result_df[species_mask_a | species_mask_b]

        if source_database:
            db_lower = source_database.lower()
            result_df = result_df[
                result_df['sourceDatabase'].str.lower().str.contains(db_lower, na=False)
            ]

        result_df = result_df.head(limit)

        if result_df.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "interactions": [],
                "message": "No interactions found"
            }

        interactions_list = result_df.to_dict('records')
        interactions_list = convert_to_native_types(interactions_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(interactions_list),
            "limit": limit,
            "interactions": interactions_list
        }

    except Exception as e:
        logger.error(f"Error getting interactions: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def search_interactions(
    target_a: str = None,
    target_b: str = None,
    source_database: str = None,
    min_count: int = None,
    limit: int = 50
) -> dict:
    """
    Search protein-protein interactions with flexible filters

    Args:
        target_a: Filter by first target
        target_b: Filter by second target
        source_database: Filter by source database
        min_count: Minimum number of supporting observations
        limit: Maximum number of results

    Returns:
        Dictionary with interaction records

    Example:
        >>> # Find high-confidence interactions from STRING
        >>> results = search_interactions(source_database="string", min_count=5)
    """
    try:
        interactions = _get_loader().get_dataset("interaction")

        # Start with all interactions
        mask = pd.Series([True] * len(interactions))

        # Apply filters
        if target_a:
            mask &= interactions['targetA'] == target_a

        if target_b:
            mask &= interactions['targetB'] == target_b

        if source_database:
            db_lower = source_database.lower()
            mask &= interactions['sourceDatabase'].str.lower().str.contains(db_lower, na=False)

        if min_count is not None:
            mask &= interactions['count'] >= min_count

        result_df = interactions[mask].head(limit)

        if result_df.empty:
            return {
                "success": True,
                "count": 0,
                "interactions": [],
                "message": "No interactions found matching criteria"
            }

        interactions_list = result_df.to_dict('records')
        interactions_list = convert_to_native_types(interactions_list)

        return {
            "success": True,
            "count": len(interactions_list),
            "limit": limit,
            "interactions": interactions_list
        }

    except Exception as e:
        logger.error(f"Error searching interactions: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_interaction_evidence(
    target_id: str,
    limit: int = 50
) -> dict:
    """
    Get detailed interaction evidence for a target

    Provides experimental evidence including detection methods,
    evidence scores, and host organisms.

    Args:
        target_id: Ensembl gene ID
        limit: Maximum number of evidence records

    Returns:
        Dictionary with:
        - success: Boolean
        - target_id: Query target
        - count: Number of evidence records
        - evidence: List of detailed evidence with:
            - targetB: Interacting partner
            - evidenceScore: Confidence score
            - interactionDetectionMethodShortName: How interaction was detected
            - interactionResources: Data sources
            - hostOrganismScientificName: Experimental organism
            - participantDetectionMethodA/B: How participants were detected

    Example:
        >>> evidence = get_interaction_evidence("ENSG00000130203")
    """
    try:
        evidence = _get_loader().get_dataset("interaction_evidence")

        # Note: interaction_evidence may use different column names
        # Check if targetA or targetId exists
        if 'targetA' in evidence.columns:
            result_df = evidence[evidence['targetA'] == target_id]
        elif 'targetId' in evidence.columns:
            result_df = evidence[evidence['targetId'] == target_id]
        else:
            # If neither, try to find target in targetB
            result_df = evidence[evidence['targetB'] == target_id]

        result_df = result_df.head(limit)

        if result_df.empty:
            return {
                "success": True,
                "target_id": target_id,
                "count": 0,
                "evidence": [],
                "message": "No interaction evidence found"
            }

        evidence_list = result_df.to_dict('records')
        evidence_list = convert_to_native_types(evidence_list)

        return {
            "success": True,
            "target_id": target_id,
            "count": len(evidence_list),
            "evidence": evidence_list
        }

    except Exception as e:
        logger.error(f"Error getting interaction evidence: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def get_interaction_network(
    seed_targets: list,
    max_hops: int = 2,
    min_confidence: float = 0.0,
    max_network_size: int = 500
) -> dict:
    """
    Build multi-hop protein interaction network from seed targets

    Performs breadth-first search from seed targets to construct an
    interaction network. This is essential for discovering pathway components,
    protein complexes, and functional modules.

    Args:
        seed_targets: List of Ensembl gene IDs to start from (e.g., ['ENSG00000130203', 'ENSG00000196218'])
        max_hops: Maximum number of hops from seed targets (default: 2)
                  1 = direct interactors only
                  2 = direct + second-degree neighbors
        min_confidence: Minimum interaction confidence score (default: 0.0)
        max_network_size: Maximum number of proteins in network (default: 500)
                          Prevents excessive computation for highly connected hubs

    Returns:
        Dictionary with:
        - success: Boolean
        - seed_targets: Input seed targets
        - network_size: Total number of proteins in network
        - edge_count: Total number of interactions
        - nodes: List of all proteins in network with:
            - target_id: Protein Ensembl ID
            - hop_distance: Hops from nearest seed (0 = seed)
            - interaction_count: Number of connections
        - edges: List of all interactions with:
            - targetA: First protein
            - targetB: Second protein
            - count: Supporting observations
            - sourceDatabase: Data source
        - statistics:
            - seeds_found: Number of seed targets with interactions
            - avg_degree: Average connections per protein
            - max_degree: Maximum connections for any protein

    Example:
        >>> # Build network around APOE
        >>> network = get_interaction_network(["ENSG00000130203"], max_hops=2)
        >>> print(f"Network has {network['network_size']} proteins and {network['edge_count']} edges")
        >>>
        >>> # Find pathway around multiple targets
        >>> network = get_interaction_network(
        ...     ["ENSG00000130203", "ENSG00000196218"],
        ...     max_hops=1,
        ...     min_confidence=0.5
        ... )
    """
    try:
        interactions = _get_loader().get_dataset("interaction")

        # Apply minimum confidence filter if available
        if 'scoring' in interactions.columns and min_confidence > 0:
            interactions = interactions[interactions['scoring'] >= min_confidence]

        # Initialize BFS
        current_layer = set(seed_targets)
        all_nodes = {target: {'hop_distance': 0, 'is_seed': True} for target in seed_targets}
        all_edges = []
        visited = set(seed_targets)
        # Track undirected pairs already added so each interaction is counted once.
        # Edges are consumed as undirected protein-protein links (targetA/targetB are
        # interchangeable in downstream degree/stat computation), so collapse to one
        # edge per unordered protein pair regardless of which endpoint is processed first.
        seen_pairs = set()

        # Breadth-first search for specified hops
        for hop in range(max_hops):
            if len(all_nodes) >= max_network_size:
                break

            next_layer = set()

            # For each protein in current layer, find its neighbors
            for target in current_layer:
                # Find interactions where target is either A or B
                mask_a = interactions['targetA'] == target
                mask_b = interactions['targetB'] == target
                target_interactions = interactions[mask_a | mask_b]

                for _, row in target_interactions.iterrows():
                    if len(all_nodes) >= max_network_size:
                        break

                    # Get the interaction partner
                    partner = row['targetB'] if row['targetA'] == target else row['targetA']

                    # Skip if this undirected pair was already counted from either endpoint
                    pair_key = frozenset((row['targetA'], row['targetB']))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    # Add edge
                    edge = {
                        'targetA': row['targetA'],
                        'targetB': row['targetB'],
                        'count': row.get('count', None),
                        'sourceDatabase': row.get('sourceDatabase', None)
                    }
                    all_edges.append(convert_to_native_types(edge))

                    # Add partner to network if new
                    if partner not in visited:
                        all_nodes[partner] = {
                            'hop_distance': hop + 1,
                            'is_seed': False
                        }
                        next_layer.add(partner)
                        visited.add(partner)

            current_layer = next_layer

            if not current_layer:  # No more neighbors
                break

        # Calculate node statistics (degree centrality)
        node_degrees = {}
        for edge in all_edges:
            for target in [edge['targetA'], edge['targetB']]:
                node_degrees[target] = node_degrees.get(target, 0) + 1

        # Build nodes list with statistics
        nodes_list = []
        for target_id, metadata in all_nodes.items():
            nodes_list.append({
                'target_id': target_id,
                'hop_distance': metadata['hop_distance'],
                'is_seed': metadata['is_seed'],
                'interaction_count': node_degrees.get(target_id, 0)
            })

        # Calculate statistics
        seeds_with_interactions = sum(1 for node in nodes_list if node['is_seed'] and node['interaction_count'] > 0)
        avg_degree = sum(node['interaction_count'] for node in nodes_list) / len(nodes_list) if nodes_list else 0
        max_degree = max((node['interaction_count'] for node in nodes_list), default=0)

        return {
            "success": True,
            "seed_targets": seed_targets,
            "max_hops": max_hops,
            "network_size": len(nodes_list),
            "edge_count": len(all_edges),
            "nodes": nodes_list,
            "edges": all_edges,
            "statistics": {
                "seeds_found": seeds_with_interactions,
                "avg_degree": round(avg_degree, 2),
                "max_degree": max_degree,
                "network_truncated": len(all_nodes) >= max_network_size
            }
        }

    except Exception as e:
        logger.error(f"Error building interaction network: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def find_common_interactors(
    target_ids: list,
    min_targets: int = 2,
    min_confidence: float = 0.0,
    limit: int = 50
) -> dict:
    """
    Find proteins that interact with multiple targets from input list

    Identifies "hub" proteins or common pathway components by finding
    proteins that interact with multiple targets of interest. This is
    valuable for discovering:
    - Shared pathway components
    - Potential drug targets affecting multiple genes
    - Protein complex members
    - Regulatory hubs

    Args:
        target_ids: List of Ensembl gene IDs to analyze (e.g., ['ENSG00000130203', 'ENSG00000196218'])
        min_targets: Minimum number of input targets the interactor must connect to (default: 2)
        min_confidence: Minimum interaction confidence score (default: 0.0)
        limit: Maximum number of common interactors to return (default: 50)

    Returns:
        Dictionary with:
        - success: Boolean
        - input_targets: Input target list
        - count: Number of common interactors found
        - common_interactors: List of hub proteins with:
            - interactor_id: Ensembl gene ID of common interactor
            - connects_to: List of input targets it interacts with
            - connection_count: Number of input targets connected to
            - total_interactions: Total interactions with input targets
            - avg_confidence: Average interaction confidence

    Example:
        >>> # Find proteins that interact with multiple Alzheimer's genes
        >>> targets = ["ENSG00000130203", "ENSG00000196218", "ENSG00000142192"]
        >>> hubs = find_common_interactors(targets, min_targets=2)
        >>> print(f"Found {hubs['count']} proteins connecting multiple targets")
        >>>
        >>> # Find high-confidence hubs
        >>> hubs = find_common_interactors(targets, min_targets=3, min_confidence=0.7)
    """
    try:
        interactions = _get_loader().get_dataset("interaction")

        # Apply minimum confidence filter if available
        if 'scoring' in interactions.columns and min_confidence > 0:
            interactions = interactions[interactions['scoring'] >= min_confidence]

        # Find all interactions involving input targets
        target_set = set(target_ids)
        interactor_connections = {}  # interactor_id -> {target_ids it connects to, interaction details}

        for target_id in target_ids:
            # Find interactions where target is either A or B
            mask_a = interactions['targetA'] == target_id
            mask_b = interactions['targetB'] == target_id
            target_interactions = interactions[mask_a | mask_b]

            for _, row in target_interactions.iterrows():
                # Get the interaction partner
                partner = row['targetB'] if row['targetA'] == target_id else row['targetA']

                # Skip if partner is also in input targets (we want external hubs)
                if partner in target_set:
                    continue

                # Track this connection
                if partner not in interactor_connections:
                    interactor_connections[partner] = {
                        'connects_to': set(),
                        'interactions': []
                    }

                interactor_connections[partner]['connects_to'].add(target_id)
                interactor_connections[partner]['interactions'].append({
                    'target': target_id,
                    'count': row.get('count', None),
                    'scoring': row.get('scoring', None),
                    'sourceDatabase': row.get('sourceDatabase', None)
                })

        # Filter to interactors meeting min_targets threshold
        common_interactors = []
        for interactor_id, data in interactor_connections.items():
            connection_count = len(data['connects_to'])

            if connection_count >= min_targets:
                # Calculate average confidence if available
                confidences = [i['scoring'] for i in data['interactions'] if i['scoring'] is not None]
                avg_confidence = sum(confidences) / len(confidences) if confidences else None

                common_interactors.append({
                    'interactor_id': interactor_id,
                    'connects_to': sorted(list(data['connects_to'])),
                    'connection_count': connection_count,
                    'total_interactions': len(data['interactions']),
                    'avg_confidence': round(avg_confidence, 3) if avg_confidence is not None else None,
                    'interaction_details': convert_to_native_types(data['interactions'][:10])  # Limit details
                })

        # Sort by connection count (descending)
        common_interactors.sort(key=lambda x: x['connection_count'], reverse=True)

        # Limit results
        common_interactors = common_interactors[:limit]

        return {
            "success": True,
            "input_targets": target_ids,
            "target_count": len(target_ids),
            "count": len(common_interactors),
            "filters": {
                "min_targets": min_targets,
                "min_confidence": min_confidence
            },
            "common_interactors": common_interactors
        }

    except Exception as e:
        logger.error(f"Error finding common interactors: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# Export all tools
__all__ = [
    'get_interactions',
    'search_interactions',
    'get_interaction_evidence',
    'get_interaction_network',  # NEW: Multi-hop network analysis
    'find_common_interactors'   # NEW: Find hub proteins connecting multiple targets
]
