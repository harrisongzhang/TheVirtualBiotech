#!/usr/bin/env python3
"""Prepare the loader's filtered Tahoe files from the official pseudobulk DE shards.

Run in the application environment. Reads one batch at a time; does not load
the full source into RAM. The destination must not already exist.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile

REQUIRED_COLUMNS = {"gene_name", "drug", "Cell_ID_DepMap", "padj", "log2FoldChange", "baseMean"}
METADATA = ("gene", "drug", "cell_line", "sample")


def prepare(source: Path, destination: Path, source_revision: str, batch_size: int = 65536) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}; choose a new directory")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    shards = sorted((source / "metadata/pseudobulk_differential_expression").glob("*.parquet"))
    if not shards:
        raise ValueError("No source DE shards in metadata/pseudobulk_differential_expression")
    schema = pq.read_schema(shards[0])
    missing = REQUIRED_COLUMNS - set(schema.names)
    if missing:
        raise ValueError(f"Source DE schema is missing columns: {', '.join(sorted(missing))}")
    for name in ("padj", "log2FoldChange", "baseMean"):
        if not (pa.types.is_floating(schema.field(name).type) or pa.types.is_integer(schema.field(name).type)):
            raise ValueError(f"Source DE column {name} must be numeric")
    for name in ("gene_name", "drug", "Cell_ID_DepMap"):
        if not (pa.types.is_string(schema.field(name).type) or pa.types.is_large_string(schema.field(name).type)):
            raise ValueError(f"Source DE column {name} must contain strings")
    for path in shards:
        if not pq.read_schema(path).equals(schema, check_metadata=False):
            raise ValueError(f"Inconsistent DE schema in {path.name}")
    for name in METADATA:
        pq.read_metadata(source / "metadata" / f"{name}_metadata.parquet")

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.preparing-", dir=destination.parent))
    rows = dict(source=0, permissive=0, significant=0, high_quality=0)
    try:
        for directory in ("metadata", "pseudobulk_de_significant", "pseudobulk_de_high_quality"):
            (stage / directory).mkdir()
        for name in METADATA:
            filename = f"{name}_metadata.parquet"
            shutil.copy2(source / "metadata" / filename, stage / "metadata" / filename)
        with pq.ParquetWriter(stage / "tahoe_permissive_padj010.parquet", schema, compression="zstd") as permissive:
            for index, path in enumerate(shards):
                with ExitStack() as stack:
                    raw = stack.enter_context(pq.ParquetFile(path))
                    significant = stack.enter_context(pq.ParquetWriter(
                        stage / "pseudobulk_de_significant" / f"part-{index:05d}.parquet", schema, compression="zstd"))
                    high_quality = stack.enter_context(pq.ParquetWriter(
                        stage / "pseudobulk_de_high_quality" / f"part-{index:05d}.parquet", schema, compression="zstd"))
                    for batch in raw.iter_batches(batch_size=batch_size):
                        rows["source"] += batch.num_rows
                        table = pa.Table.from_batches([batch])
                        padj, fold_change = table["padj"], table["log2FoldChange"]
                        valid = pc.and_(pc.is_finite(padj), pc.is_finite(fold_change))
                        valid = pc.and_(valid, pc.greater_equal(padj, 0))
                        selected = table.filter(pc.and_(valid, pc.less(padj, 0.10)))
                        strong = selected.filter(pc.less(selected["padj"], 0.05))
                        high = strong.filter(pc.greater(pc.abs(strong["log2FoldChange"]), 0.5))
                        for name, writer, output in (("permissive", permissive, selected),
                                                     ("significant", significant, strong),
                                                     ("high_quality", high_quality, high)):
                            rows[name] += output.num_rows
                            if output.num_rows:
                                writer.write_table(output)
                print(f"Prepared shard {index + 1}/{len(shards)}; {rows['permissive']:,} permissive rows", flush=True)
        manifest = {
            "source_dataset": "tahoebio/Tahoe-100M", "source_revision": source_revision,
            "source_files": [p.relative_to(source).as_posix() for p in shards],
            "filters": {"permissive": "padj < 0.10", "significant": "padj < 0.05",
                        "high_quality": "padj < 0.05 and abs(log2FoldChange) > 0.5",
                        "validity": "finite padj and log2FoldChange; padj >= 0"},
            "rows": rows, "complete": True,
        }
        (stage / "preparation_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if destination.exists():
            raise ValueError(f"Destination was created during preparation: {destination}")
        stage.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="downloaded Tahoe repository root")
    parser.add_argument("destination", type=Path, help="new directory to use as TAHOE_DATA_PATH")
    parser.add_argument("--source-revision", required=True, help="Hugging Face commit hash used for the source download")
    args = parser.parse_args()
    try:
        prepare(args.source, args.destination, args.source_revision)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Complete. Set TAHOE_DATA_PATH to {args.destination.resolve()}")


if __name__ == "__main__":
    main()
