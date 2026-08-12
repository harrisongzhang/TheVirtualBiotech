"""
Output Manager for MCP Server File-Based Tools

Provides utilities for managing file outputs from MCP tools including:
- Standardized folder structure: data/YYYY-MM-DD/server_name/
- Timestamped filenames
- JSON index tracking
- No auto-deletion (user manages cleanup)

Usage:
    from src.utils.output_manager import OutputManager

    # In any MCP tool:
    om = OutputManager(server_name='single_cell', tool_name='get_expression_for_genes')
    output_path = om.get_output_path(
        user_path='tp53_tcells.h5ad',  # If user provides path
        auto_suffix='.h5ad'              # For auto-generated names
    )
    # Returns: data/2025-11-09/single_cell/tp53_tcells.h5ad
    # OR auto: data/2025-11-09/single_cell/get_expression_for_genes_17-30-45.h5ad

    # After saving data, register it:
    om.register_output(
        file_path=output_path,
        query_params={'gene_symbols': ['TP53'], 'value_filter': '...'},
        n_records=5000,
        size_mb=0.6
    )
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class OutputManager:
    """Manages file outputs for MCP server tools"""

    # Base directory for all outputs
    BASE_DIR = Path(os.getenv('MCP_OUTPUT_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data')))

    def __init__(self, server_name: str, tool_name: str):
        """
        Initialize output manager for a specific MCP server tool

        Args:
            server_name: Name of MCP server (e.g., 'single_cell', 'association')
            tool_name: Name of the tool (e.g., 'get_expression_for_genes')
        """
        self.server_name = server_name
        self.tool_name = tool_name
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.timestamp = datetime.now().strftime("%H-%M-%S")

        # Create folder structure: data/YYYY-MM-DD/server_name/
        self.output_dir = self.BASE_DIR / self.today / server_name
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Index file for this server/date
        self.index_path = self.output_dir / "index.json"

    def get_output_path(
        self,
        user_path: Optional[str] = None,
        auto_suffix: str = '.parquet'
    ) -> str:
        """
        Get output path for a file, either user-specified or auto-generated

        Args:
            user_path: User-provided path (absolute or relative)
            auto_suffix: File extension for auto-generated names (e.g., '.h5ad', '.parquet')

        Returns:
            Absolute path to output file

        Examples:
            # User provides absolute path - use as-is
            get_output_path('/tmp/my_data.h5ad') → '/tmp/my_data.h5ad'

            # User provides relative path - place in standard location
            get_output_path('my_data.h5ad') → 'data/2025-11-09/single_cell/my_data.h5ad'

            # Auto-generate with timestamp
            get_output_path(auto_suffix='.h5ad') → 'data/2025-11-09/single_cell/get_expression_for_genes_17-30-45.h5ad'
        """
        if user_path:
            # User provided a path
            if os.path.isabs(user_path):
                # Absolute path - use as-is
                # Create parent directory if needed
                parent_dir = os.path.dirname(user_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                return user_path
            else:
                # Relative path - place in standard location
                full_path = self.output_dir / user_path
                # Create parent directory for the file
                parent_dir = os.path.dirname(full_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                return str(full_path)
        else:
            # Auto-generate filename with timestamp
            filename = f"{self.tool_name}_{self.timestamp}{auto_suffix}"
            return str(self.output_dir / filename)

    def register_output(
        self,
        file_path: str,
        query_params: Dict[str, Any],
        n_records: int,
        size_mb: float,
        additional_metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Register an output file in the index

        Args:
            file_path: Path to the output file
            query_params: Parameters used to generate the file
            n_records: Number of records in the file
            size_mb: File size in MB
            additional_metadata: Any additional metadata to store
        """
        # Load existing index or create new
        if self.index_path.exists():
            with open(self.index_path, 'r') as f:
                index = json.load(f)
        else:
            index = {'outputs': []}

        # Add new entry
        entry = {
            'timestamp': datetime.now().isoformat(),
            'tool': self.tool_name,
            'file_path': os.path.basename(file_path),  # Store relative name
            'full_path': file_path,  # Store full path for easy access
            'query_params': query_params,
            'size_mb': size_mb,
            'n_records': n_records
        }

        if additional_metadata:
            entry['metadata'] = additional_metadata

        index['outputs'].append(entry)

        # Save updated index
        with open(self.index_path, 'w') as f:
            json.dump(index, f, indent=2)

    @staticmethod
    def list_outputs(date: Optional[str] = None, server: Optional[str] = None) -> Dict[str, Any]:
        """
        List all outputs, optionally filtered by date and/or server

        Args:
            date: Date string (YYYY-MM-DD) or None for all dates
            server: Server name or None for all servers

        Returns:
            Dictionary with outputs grouped by date and server
        """
        base = OutputManager.BASE_DIR

        if not base.exists():
            return {'outputs': []}

        all_outputs = []

        # Scan directory structure
        for date_dir in sorted(base.iterdir()):
            if not date_dir.is_dir():
                continue
            if date and date_dir.name != date:
                continue

            for server_dir in sorted(date_dir.iterdir()):
                if not server_dir.is_dir():
                    continue
                if server and server_dir.name != server:
                    continue

                # Load index if exists
                index_file = server_dir / "index.json"
                if index_file.exists():
                    with open(index_file, 'r') as f:
                        index_data = json.load(f)
                        for output in index_data.get('outputs', []):
                            output['date'] = date_dir.name
                            output['server'] = server_dir.name
                            all_outputs.append(output)

        return {
            'total_outputs': len(all_outputs),
            'outputs': all_outputs
        }
