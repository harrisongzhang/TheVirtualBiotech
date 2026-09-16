"""
Data management module
Handles loading and caching of Open Targets datasets
"""

__all__ = ['OpenTargetsDataLoader', 'get_data_loader']


def __getattr__(name):
    # Inventory checks must work without importing pandas or opening datasets.
    if name in __all__:
        from . import loader
        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
