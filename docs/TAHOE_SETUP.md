# Optional Tahoe data

Tahoe enables drug-perturbation tools. Leave `TAHOE_DATA_PATH` unset to use
the core installation, including DepMap essentiality, without Tahoe.

The source is the [official Tahoe-100M dataset](https://huggingface.co/datasets/tahoebio/Tahoe-100M).
Use its [pseudobulk differential-expression shards](https://huggingface.co/datasets/tahoebio/Tahoe-100M/tree/2dc57900b7981cfcf5e211527169a0b006546a95/metadata/pseudobulk_differential_expression)
and four metadata tables. The loader expects filtered files prepared from
those shards. The source download alone is not the layout used by the app.

## Download

Activate the application environment and run from the repository root.
The `hf` CLI is available through the environment's Hugging Face dependency.
This pins the source revision inspected for this recipe and downloads only the
DE shards and the required metadata, excluding the raw cell expression data:

```bash
conda activate vbt
hf download tahoebio/Tahoe-100M --repo-type dataset \
  --revision 2dc57900b7981cfcf5e211527169a0b006546a95 \
  --include "metadata/pseudobulk_differential_expression/*.parquet" \
  --include "metadata/gene_metadata.parquet" \
  --include "metadata/drug_metadata.parquet" \
  --include "metadata/cell_line_metadata.parquet" \
  --include "metadata/sample_metadata.parquet" \
  --local-dir /path/to/tahoe-source
```

Wait for the download to finish successfully before preparing the data. This
revision has 1,026 DE shards, approximately 83 GiB compressed. Budget additional
space for the filtered outputs; their size depends on the source. Rerun the
same download command if interrupted.

## Prepare the files the app reads

```bash
python tools/prepare_tahoe.py /path/to/tahoe-source /path/to/tahoe-prepared \
  --source-revision 2dc57900b7981cfcf5e211527169a0b006546a95
```

The command reads batches of at most 65,536 rows, validates the source schema,
and preserves source columns and metadata tables. It requires numeric `padj`,
`log2FoldChange`, `baseMean` and string `gene_name`, `drug`, `Cell_ID_DepMap`.
Rows with null/non-finite `padj` or `log2FoldChange`, or negative `padj`, are
excluded. The three outputs use these exact thresholds:

| Output | Filter |
|---|---|
| `tahoe_permissive_padj010.parquet` | `padj < 0.10` |
| `pseudobulk_de_significant/` | `padj < 0.05` |
| `pseudobulk_de_high_quality/` | `padj < 0.05` and `abs(log2FoldChange) > 0.5` |

The fold-change threshold is on **log2 fold change**. Values exactly at a
cutoff are excluded. The permissive output is one consolidated Parquet file;
the other outputs are sharded directories.

The destination must be new. Output is prepared in a temporary sibling directory
and published only when complete, so an interrupted preparation does not leave
a usable-looking partial destination. Rerun preparation from the start after an
interruption. The source download is retained.

```text
tahoe-prepared/
├── tahoe_permissive_padj010.parquet
├── pseudobulk_de_significant/
├── pseudobulk_de_high_quality/
├── metadata/
│   ├── gene_metadata.parquet
│   ├── drug_metadata.parquet
│   ├── cell_line_metadata.parquet
│   └── sample_metadata.parquet
└── preparation_manifest.json
```

`preparation_manifest.json` records the supplied source revision, input shard
names, filters, and row counts. This recipe generates compatible inputs from
the public DE results; historical privately prepared files may differ.

Set this entry in the project-root `.env`, using an absolute path:

```dotenv
TAHOE_DATA_PATH="/absolute/path/to/tahoe-prepared"
```

Check that the prepared files can be opened without making a model request:

```bash
python -c "from src.data.loader import get_data_loader; d = get_data_loader(preload_all=False); print(d.get_tahoe_dataset('tahoe_pseudobulk_permissive').schema)"
```

This reads the same configuration as the app, including the required
`OPEN_TARGETS_DATA_PATH`. Continue with the
[README's CLI instructions](../README.md#running-the-cli) to run a query.
