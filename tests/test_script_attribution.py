"""Preserve script attribution without assigning ambiguous outputs to a writer."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.provenance import build_provenance
from src.utils.run_manifest import CSO_DIR, RunManifest
from src.utils.session_audit import attribute_script_outputs
from src.utils.trace_logger import TraceLogger


class ScriptAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = RunManifest.create(Path(self.temp.name) / 'runs')
        self.trace = TraceLogger(self.run.run_dir / 'logs' / 'trace.jsonl')
        self.output = self.run.run_dir / 'observations.csv'
        self.output.write_text('target,count\nPCSK9,3\n')

    def script(self, agent, name, source):
        path = self.run.agent_dir(agent) / 'code' / 'scripts' / name
        path.write_text(source)
        arguments = {'file_path': str(path), 'content': source}
        self.trace.tool_start(name, 'Write', arguments, agent=agent)
        self.trace.tool_end(name, 'Write', arguments, {'success': True}, agent=agent)
        return path

    def attribute(self):
        self.run.scan()
        provenance = build_provenance(self.run.run_dir / 'logs' / 'trace.jsonl')
        attribute_script_outputs(self.run, provenance)
        return self.run.data['artifacts']['observations.csv']

    def test_unique_observed_script_writer_is_recovered(self):
        self.script('genomics-analyst', 'analyze.py',
                    f"frame.to_csv({str(self.output)!r}, index=False)\n")
        artifact = self.attribute()
        self.assertEqual(artifact['produced_by'], 'genomics-analyst')
        self.assertEqual(artifact['created_by'], 'analyze.py:1')

    def test_reading_an_existing_output_does_not_establish_authorship(self):
        self.script('genomics-analyst', 'read.py',
                    f"frame = pd.read_csv({str(self.output)!r})\n")
        self.assertEqual(self.attribute()['produced_by'], CSO_DIR)

    def test_two_possible_writers_remain_unattributed(self):
        for agent, name in [('genomics-analyst', 'genetics.py'),
                            ('target-biologist', 'biology.py')]:
            self.script(agent, name, f"frame.to_csv({str(self.output)!r})\n")
        self.assertEqual(self.attribute()['produced_by'], CSO_DIR)

    def test_existing_specialist_ownership_is_preserved(self):
        self.script('genomics-analyst', 'analyze.py',
                    f"frame.to_csv({str(self.output)!r})\n")
        self.run.add_artifact(self.output, produced_by='target-biologist')
        self.assertEqual(self.attribute()['produced_by'], 'target-biologist')


if __name__ == '__main__':
    unittest.main()
