"""Genomic queries keep global ranking and nested data without full-table loads."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HAS_DATA = all(importlib.util.find_spec(name) is not None for name in ('pandas', 'pyarrow', 'dotenv'))
if HAS_DATA:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
    from src.data.loader import OpenTargetsDataLoader
    from src.data.query import scan_top_rows
    from src.mcp_servers.genetics_mcp import tools


@unittest.skipUnless(HAS_DATA, 'requires the application data dependencies')
class TestBoundedGeneticsQueries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        rows = []
        for i in range(32):
            rows.append({
                'studyLocusId': str(i), 'studyId': 'study' if i % 2 else 'other',
                'variantId': f'9_{6000000+i}_G_A', 'chromosome': '9' if i < 30 else '2',
                'position': 6000000+i, 'beta': i/10, 'pValueExponent': -i,
                'pValueMantissa': 1.0, 'studyType': 'gwas',
                'credibleSetlog10BF': float(i) if i != 29 else None,
                'locus': [{'variantId': f'9_{i}_G_A', 'posteriorProbability': .8}] if i != 28 else None,
            })
        rows[27]['pValueExponent'] = None
        # Exercise the mantissa tie-breaker across batches as well as exponents.
        rows[26]['pValueExponent'] = -29
        rows[26]['pValueMantissa'] = 2.0
        table = pa.Table.from_pylist(rows)
        path = self.root / 'credible_set'
        path.mkdir()
        # The best hits are in later files and batches, not the first scan head.
        for i in range(0, 32, 8):
            pq.write_table(table.slice(i, 8), path / f'{i:03}.parquet', row_group_size=4)
        self.frame = table.to_pandas()
        self.loader = OpenTargetsDataLoader.__new__(OpenTargetsDataLoader)
        self.loader._base_path = self.root
        self.loader._cache = {}
        patches = (
            patch.object(tools, '_get_loader', return_value=self.loader),
            patch.object(tools.OutputManager, 'BASE_DIR', self.root / 'outputs'),
            # Calling the former eager path is a regression even on a small fixture.
            patch.object(self.loader, 'get_dataset', side_effect=AssertionError('eager full-table load')),
        )
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def assert_output_matches(self, path, expected):
        output = pd.read_parquet(path)
        self.assertEqual(output.columns.tolist(), expected.columns.tolist())
        # Compare every field, including nested values and missing data.
        self.assertEqual(pa.Table.from_pandas(output, preserve_index=False).to_pylist(),
                         pa.Table.from_pandas(expected, preserve_index=False).to_pylist())
        self.assertEqual(self.loader._cache, {})

    def test_top_rows_across_files_batches_nulls_and_nested_values(self):
        result = scan_top_rows(self.loader.get_arrow_dataset('credible_set'),
                              filter=ds.field('chromosome') == '9',
                              sort_keys=[('pValueExponent', 'ascending'), ('pValueMantissa', 'ascending')],
                              limit=5, batch_size=3).to_pandas()
        expected = self.frame[self.frame.chromosome == '9'].sort_values(
            ['pValueExponent', 'pValueMantissa'], na_position='last').head(5)
        self.assertEqual(result.studyLocusId.tolist(), expected.studyLocusId.tolist())
        self.assertEqual(result.iloc[0].locus[0]['variantId'], expected.iloc[0].locus[0]['variantId'])
        self.assertEqual(self.loader._cache, {})

    def test_region_intersection_significance_and_full_parquet_output(self):
        path = self.root / 'gwas.parquet'
        result = tools.query_gwas_associations(str(path), chromosome='9',
                    region='chr9:6000004-6000029', study_id='study', limit=4)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['num_results'], 4)
        expected = self.frame[(self.frame.chromosome == '9') & (self.frame.studyId == 'study') &
                             self.frame.position.between(6000004, 6000029)].sort_values(
                                 ['pValueExponent', 'pValueMantissa'], na_position='last').head(4)
        self.assert_output_matches(path, expected)

    def test_variant_filter_intersects_with_study(self):
        path = self.root / 'variant.parquet'
        variant = '9_6000011_G_A'
        result = tools.query_gwas_associations(str(path), variant_id=variant, study_id='study')
        self.assertTrue(result['success'], result)
        self.assert_output_matches(path, self.frame[self.frame.variantId == variant])
        empty = tools.query_gwas_associations(str(self.root / 'no-variant.parquet'),
                                              variant_id=variant, study_id='other')
        self.assertEqual(empty['num_results'], 0)

    def test_credible_sets_global_confidence_excludes_missing_locus(self):
        path = self.root / 'credible.parquet'
        result = tools.get_credible_sets(str(path), study_type='gwas', limit=5)
        self.assertTrue(result['success'], result)
        expected = self.frame[self.frame.locus.notna()].sort_values(
            'credibleSetlog10BF', ascending=False, na_position='last').head(5)
        self.assert_output_matches(path, expected)

    def test_credible_set_identifier_study_type_and_confidence_filters(self):
        path = self.root / 'confidence.parquet'
        result = tools.get_credible_sets(str(path), study_type='gwas', study_id='study',
                                         min_confidence=20, limit=3)
        self.assertTrue(result['success'], result)
        expected = self.frame[(self.frame.studyId == 'study') & self.frame.locus.notna() &
                             (self.frame.credibleSetlog10BF >= 20)].sort_values(
                                 'credibleSetlog10BF', ascending=False).head(3)
        self.assert_output_matches(path, expected)
        identifier = expected.iloc[0].studyLocusId
        one = tools.get_credible_sets(str(self.root / 'one.parquet'), study_locus_id=identifier)
        self.assertTrue(one['success'], one)
        self.assert_output_matches(one['output_path'], self.frame[self.frame.studyLocusId == identifier])
        empty = tools.get_credible_sets(str(self.root / 'wrong-type.parquet'), study_type='eqtl')
        self.assertEqual(empty['num_results'], 0)

    def test_invalid_region_returns_before_opening_reference(self):
        for region in ['bad', 'chr9:7-3', 'chr9:x-9', ':1-2']:
            with self.subTest(region=region), patch.object(self.loader, 'get_arrow_dataset',
                                                         side_effect=AssertionError('opened reference')):
                result = tools.query_gwas_associations(str(self.root / 'invalid.parquet'), region=region)
                self.assertFalse(result['success'])
                self.assertIn('Invalid region format', result['error'])

    def test_required_filter_and_empty_matches(self):
        with patch.object(self.loader, 'get_arrow_dataset', side_effect=AssertionError('opened reference')):
            self.assertFalse(tools.query_gwas_associations(str(self.root / 'none.parquet'))['success'])
            self.assertFalse(tools.get_credible_sets(str(self.root / 'none.parquet'))['success'])
        result = tools.query_gwas_associations(str(self.root / 'empty.parquet'), chromosome='X')
        self.assertTrue(result['success'], result)
        self.assertEqual(result['num_results'], 0)
        self.assertFalse((self.root / 'empty.parquet').exists())

    def test_invalid_limits_are_rejected(self):
        for limit in [-1, 0, True, 1.5]:
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'positive integer'):
                scan_top_rows(self.loader.get_arrow_dataset('credible_set'), filter=ds.scalar(True),
                              sort_keys=[], limit=limit)

    def test_missing_sort_columns_and_ties_keep_first_rows(self):
        dataset = self.loader.get_arrow_dataset('credible_set')
        for keys in [[('not_in_schema', 'ascending')], [('studyType', 'ascending')]]:
            with self.subTest(keys=keys):
                result = scan_top_rows(dataset, filter=ds.scalar(True), sort_keys=keys, limit=5,
                                       batch_size=2)
                self.assertEqual(result['studyLocusId'].to_pylist(), ['0', '1', '2', '3', '4'])

    def test_empty_scan_preserves_schema_and_large_limit_returns_all_matches(self):
        dataset = self.loader.get_arrow_dataset('credible_set')
        empty = scan_top_rows(dataset, filter=ds.field('chromosome') == 'X',
                              sort_keys=[('position', 'ascending')], limit=5)
        self.assertEqual(empty.schema, dataset.schema)
        self.assertEqual(empty.num_rows, 0)
        result = scan_top_rows(dataset, filter=ds.scalar(True),
                               sort_keys=[('position', 'ascending')], limit=100, batch_size=3)
        self.assertEqual(result['studyLocusId'].to_pylist(), self.frame.studyLocusId.tolist())


if __name__ == '__main__':
    unittest.main()
