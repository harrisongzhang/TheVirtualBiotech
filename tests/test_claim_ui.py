"""
Tests for the claim-evidence UI surfaces (reviewer comment R2.3).

The behaviours pinned here are the ones that decide whether the feature is
honest: a verified claim, an unverified one, and a citation that was never filed
must all look different, and the static fallback must carry the same information
as the interactive panel.

Run:  python tests/test_claim_ui.py
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claim_ui import (  # noqa: E402
    _strip_claim_refs, render_claim_refs, render_evidence_panel,
    render_evidence_static,
)
from src.utils.claims import ClaimSet  # noqa: E402


def _claims():
    return ClaimSet([
        {"id": "C1", "text": "IL1RL1 is highly expressed in lung mast cells",
         "agent": "single-cell-analyst", "confidence": "strong", "n_verified": 2,
         "evidence": [
             {"kind": "table", "path": "work/single-cell-analyst/results/tables/e.csv",
              "note": "row: mast cell", "verified": True,
              "produced_by": "single-cell-analyst"},
             {"kind": "tool_call", "tool_use_id": "toolu_01Ab",
              "tool_name": "mcp__single_cell__get_expr",
              "agent": "single-cell-analyst", "verified": True},
         ]},
        {"id": "C2", "text": "No black-box warning exists", "agent": "fda-safety-officer",
         "confidence": "moderate", "n_verified": 0,
         "evidence": [{"kind": "citation", "pmid": "12345678", "verified": False}]},
    ])


class TestClaimRefs(unittest.TestCase):
    def setUp(self):
        self.cs = _claims()

    def test_verified_claim_renders_a_numbered_marker(self):
        out = render_claim_refs("Expressed in mast cells[[claim:C1]].", self.cs)
        self.assertIn('data-claim="C1"', out)
        self.assertIn('class="claim-ref"', out)
        self.assertIn(">1</sup>", out)
        self.assertNotIn("[[claim:", out)

    def test_unverified_claim_is_visually_distinct(self):
        out = render_claim_refs("No warning[[claim:C2]].", self.cs)
        self.assertIn("claim-ref unverified", out)

    def test_unfiled_claim_renders_as_visibly_broken(self):
        """A dangling citation must not look like a working one."""
        out = render_claim_refs("Asserted[[claim:C9]].", self.cs)
        self.assertIn("claim-ref missing", out)
        self.assertIn("C9?", out)
        self.assertNotIn('data-claim="C9"', out)

    def test_numbering_follows_order_of_appearance(self):
        out = render_claim_refs("b[[claim:C2]] then a[[claim:C1]]", self.cs)
        self.assertLess(out.index(">1</sup>"), out.index(">2</sup>"))
        self.assertIn('data-claim="C2"', out.split(">1</sup>")[0])

    def test_repeated_reference_keeps_its_number(self):
        out = render_claim_refs("a[[claim:C1]] b[[claim:C2]] c[[claim:C1]]", self.cs)
        self.assertEqual(out.count(">1</sup>"), 2)
        self.assertEqual(out.count(">2</sup>"), 1)

    def test_claim_text_is_escaped_into_the_tooltip(self):
        cs = ClaimSet([{"id": "C1", "text": 'He said "high" expression',
                        "confidence": "strong", "n_verified": 1, "evidence": []}])
        out = render_claim_refs("x[[claim:C1]]", cs)
        self.assertIn("&quot;high&quot;", out)
        self.assertNotIn('title="He said "high"', out)

    def test_text_without_refs_is_untouched(self):
        self.assertEqual(render_claim_refs("plain prose", self.cs), "plain prose")
        self.assertEqual(render_claim_refs("", self.cs), "")

    def test_strip_hides_anchors_while_streaming(self):
        self.assertEqual(_strip_claim_refs("a[[claim:C1]] b[[claim:C2]]"), "a b")
        self.assertEqual(_strip_claim_refs(None), "")


class TestEvidencePanel(unittest.TestCase):
    def setUp(self):
        self.cs = _claims()

    def test_panel_embeds_claims_as_json(self):
        panel = render_evidence_panel(self.cs)
        m = re.search(r'id="vbt-claims">(.*?)</script>', panel, re.S)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1))
        self.assertEqual(sorted(data), ["C1", "C2"])
        self.assertEqual(data["C1"]["agent"], "single-cell-analyst")

    def test_panel_has_the_target_container(self):
        self.assertIn('id="vbt-evidence"', render_evidence_panel(self.cs))

    def test_empty_panel_is_valid_and_explains_itself(self):
        panel = render_evidence_panel(ClaimSet())
        self.assertEqual(json.loads(
            re.search(r'id="vbt-claims">(.*?)</script>', panel, re.S).group(1)), {})
        self.assertIn("Click any numbered marker", panel)

    def test_panel_reports_verified_counts(self):
        self.assertIn("(1 with verified evidence)", render_evidence_panel(self.cs))


class TestStaticFallback(unittest.TestCase):
    """The audit path must survive with JavaScript disabled."""

    def setUp(self):
        self.cs = _claims()

    def test_static_lists_every_claim_and_its_evidence(self):
        out = render_evidence_static(self.cs)
        self.assertIn("IL1RL1 is highly expressed in lung mast cells", out)
        self.assertIn("work/single-cell-analyst/results/tables/e.csv", out)
        self.assertIn("mcp__single_cell__get_expr", out)
        self.assertIn("12345678", out)

    def test_static_distinguishes_verified_from_not(self):
        out = render_evidence_static(self.cs)
        self.assertIn("✓ verified", out)
        self.assertIn("external ref", out)

    def test_an_external_citation_is_not_labelled_a_failure(self):
        """A PMID we chose not to fetch is not the same as a missing file.

        Both used to render as "unverified", which read as a defect against a
        claim whose local evidence had in fact verified.
        """
        out = render_evidence_static(self.cs)
        self.assertNotIn("unverified", out)
        self.assertNotIn("not on record", out)

    def test_evidence_that_does_not_resolve_stays_loud(self):
        cs = ClaimSet([{
            "id": "C9", "text": "unsupported", "agent": "a",
            "confidence": "weak", "n_verified": 0,
            "evidence": [{"kind": "table", "path": "work/a/nope.csv",
                          "verified": False}],
        }])
        out = render_evidence_static(cs)
        self.assertIn("not on record", out)
        self.assertIn("ev-badge unresolved", out)
        self.assertIn("no verified evidence", out)

    def test_static_renders_one_card_per_claim(self):
        out = render_evidence_static(self.cs)
        self.assertEqual(out.count('class="ev-card ev-card-static'),
                         len(self.cs.claims))

    def test_static_escapes_model_authored_text(self):
        cs = ClaimSet([{
            "id": "C1", "text": "<img src=x onerror=alert(1)>", "agent": "a",
            "confidence": "strong", "n_verified": 1,
            "evidence": [{"kind": "table", "path": "<script>", "verified": True}],
        }])
        out = render_evidence_static(cs)
        self.assertNotIn("<img", out)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;img", out)

    def test_static_carries_agent_and_confidence(self):
        out = render_evidence_static(self.cs)
        # Same wording as the click-panel's .ev-meta line, deliberately: the
        # two surfaces render the same card and must not read differently.
        self.assertIn("single-cell-analyst · confidence strong", out)
        self.assertIn("fda-safety-officer · confidence moderate", out)

    def test_static_includes_evidence_notes(self):
        self.assertIn("row: mast cell", render_evidence_static(self.cs))

    def test_empty_static_is_explicit(self):
        self.assertIn("No claims filed", render_evidence_static(ClaimSet()))

    def test_every_claim_in_the_panel_is_also_in_the_static_list(self):
        """The two surfaces must not disagree about what is on record."""
        panel = render_evidence_panel(self.cs)
        data = json.loads(
            re.search(r'id="vbt-claims">(.*?)</script>', panel, re.S).group(1))
        static = render_evidence_static(self.cs)
        for c in data.values():
            self.assertIn(c["text"], static)


if __name__ == "__main__":
    unittest.main(verbosity=2)
