# Workspace Setup - Web Interface

## Overview

When running through the **Virtual Biotech Institute web interface**, workspace management is **automatic**. Each session gets its own isolated workspace directory.

## Automatic Workspace Management

✅ **No manual initialization required**
- Session workspace is created automatically
- Your `cwd` (current working directory) is already set to your session workspace
- All files you create are automatically isolated to your session
- Files are tracked and available for download in the web interface

## File Operations - Simple and Direct

Just write files directly using relative paths:

```python
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt

# Write data files
adata.write_h5ad('integrated_data.h5ad')  # ✅ Goes to session workspace

# Save analysis results
de_results.to_csv('differential_expression.csv')  # ✅ Goes to session workspace

# Save figures
plt.savefig('umap_plot.png', dpi=300)  # ✅ Goes to session workspace
```

## Organizing Files (Optional)

Create subdirectories if you want organization:

```python
from pathlib import Path

# Create subdirectories
Path('figures').mkdir(exist_ok=True)
Path('tables').mkdir(exist_ok=True)
Path('data').mkdir(exist_ok=True)

# Use them
plt.savefig('figures/umap.png', dpi=300)
de_results.to_csv('tables/de_genes.csv')
adata.write_h5ad('data/integrated.h5ad')
```

## Important Rules

✅ **DO:**
- Write files with simple relative paths
- Create subdirectories if you want organization
- Use Path objects for clean path handling: `Path('figures') / 'plot.png'`

❌ **DON'T:**
- Use WorkspaceManager (not needed in web interface)
- Hardcode absolute paths outside current directory
- Write to `/oak/...` paths directly

## File Access

All files you create:
- Appear in the "📁 Generated Files" section in the web interface
- Can be downloaded by the user
- Are isolated to your session (other sessions can't see them)
- Persist for the session duration (8 hours)

## Example: Complete Analysis Script

```python
#!/usr/bin/env python3
"""Single-cell differential expression analysis"""

import scanpy as sc
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# Create organization (optional)
Path('figures').mkdir(exist_ok=True)
Path('tables').mkdir(exist_ok=True)

# Load data (assumes you downloaded it earlier)
adata = sc.read_h5ad('raw_data.h5ad')

# Analyze
# ... your analysis code ...

# Save results - all go to session workspace automatically
adata.write_h5ad('processed_data.h5ad')
de_results.to_csv('tables/de_genes.csv')
plt.savefig('figures/umap.png', dpi=300)

print('✓ Analysis complete. Files saved to session workspace.')
print('✓ Check "Generated Files" in the web interface to download.')
```

That's it! Simple file operations with automatic workspace isolation.
