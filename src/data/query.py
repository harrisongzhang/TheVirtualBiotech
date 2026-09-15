"""Bounded scans of nested Parquet references, retaining global result order."""

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds


def scan_top_rows(
    dataset: ds.Dataset,
    *,
    filter: ds.Expression,
    sort_keys: list[tuple[str, str]],
    limit: int,
    batch_size: int = 1024,
) -> pa.Table:
    """Filter batches and retain only the globally best ``limit`` full rows.

    Sorting a head of the scan would silently miss significant hits in later
    files. Instead each batch competes with the current best rows. Nested
    columns remain Arrow arrays until the caller materializes the final result.
    Serial decoding and disabled prefetch bound concurrent Parquet buffers.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    keys = [(name, direction) for name, direction in sort_keys if name in dataset.schema.names]
    best = pa.Table.from_batches([], schema=dataset.schema)
    scanner = dataset.scanner(
        filter=filter, batch_size=batch_size, batch_readahead=0,
        fragment_readahead=0, use_threads=False,
        fragment_scan_options=ds.ParquetFragmentScanOptions(
            pre_buffer=False, use_buffered_stream=True,
        ),
    )
    for batch in scanner.to_batches():
        if not batch.num_rows:
            continue
        candidates = pa.concat_tables([best, pa.Table.from_batches([batch])])
        if keys:
            order = pc.sort_indices(
                candidates.select([name for name, _ in keys]),
                sort_keys=keys, null_placement="at_end",
            ).slice(0, limit)
        else:
            order = pa.array(range(min(limit, len(candidates))), type=pa.int64())
        # take copies selected nested values; a slice could retain large buffers.
        best = candidates.take(order)
        if not keys and len(best) == limit:
            break
    return best
