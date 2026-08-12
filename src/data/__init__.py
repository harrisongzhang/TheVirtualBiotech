"""
Data management module
Handles loading and caching of Open Targets datasets
"""

from .loader import OpenTargetsDataLoader, get_data_loader

__all__ = ['OpenTargetsDataLoader', 'get_data_loader']
