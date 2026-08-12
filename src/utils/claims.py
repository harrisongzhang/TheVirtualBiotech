"""
Claim–Evidence objects and their validator
The Virtual Biotech

Reviewer comment R2.3 asks for claim-evidence objects in the UI: click a claim,
see where it came from. This module defines that object and — more importantly —
the validator that keeps it honest.

A claim is a single assertion in the CSO's synthesis. Its ``evidence`` entries
point at things that must actually exist: an artifact in ``MANIFEST.json`` whose
hash still matches, or a ``tool_use_id`` present in the run's trace. Anything
else is rejected at the moment it is filed.

That rejection is the whole point. An unvalidated evidence link is worse than no
link at all: it looks like provenance while being a plausible-sounding guess.
Here, a claim citing a file that was never written, or a tool call that never
happened, cannot be recorded — ``record_claims`` returns the errors and the CSO
has to fix them.

Claim IDs are referenced from prose as ``[[claim:C3]]``; the Gradio layer rewrites
those anchors into clickable superscripts.

Usage::

    from src.utils.claims import ClaimSet, validate_claims

    result = validate_claims(claims, manifest, provenance)
    if not result.ok:
        ...                                  # returned to the CSO to correct
    cs = ClaimSet(result.claims)
    cs.write(run_dir / 'evidence' / 'claims.json')
    cs.link_into_manifest(manifest)          # populates each artifact's cited_by
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

#: Inline reference to a claim from CSO prose.
CLAIM_REF_RE = re.compile(r"\[\[claim:([A-Za-z0-9_.-]+)\]\]")

CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

#: Evidence kinds. `artifact`/`figure`/`table`/`code` resolve against the
#: manifest; `tool_call` against the trace; `citation` is external (PMID/DOI/URL)
#: and cannot be checked locally, so it is marked external rather than trusted.
EVIDENCE_KINDS = {"artifact", "figure", "table", "code", "tool_call", "citation"}

LOCAL_KINDS = {"artifact", "figure", "table", "code"}

CONFIDENCE_LEVELS = {"strong", "moderate", "weak"}

#: How an evidence entry's ``verified`` flag should be presented.
#:
#: ``verified=False`` is reached two entirely different ways, and collapsing them
#: into one "unverified" label was misleading: a PMID we deliberately did not
#: fetch was shown the same as a file path that does not exist. The first is a
#: property of the evidence kind, the second is a defect.
#:
#:   ``verified``    resolved against this run's manifest or trace
#:   ``external``    a citation — outside the run, so not checkable from here.
#:                   Not a problem, and not a claim about the source being wrong.
#:   ``unresolved``  cites a local artifact or tool call that is NOT on record.
#:                   This one IS a defect and must stay loud.
EVIDENCE_STATUS_LABELS = {
    "verified": "✓ verified",
    "external": "external ref",
    "unresolved": "not on record",
}


def evidence_status(ev: dict[str, Any]) -> str:
    """One of ``verified`` / ``external`` / ``unresolved`` for an evidence entry."""
    if ev.get("verified"):
        return "verified"
    return "external" if ev.get("kind") == "citation" else "unresolved"


@dataclass
class ValidationResult:
    """Outcome of validating a batch of claims."""
    claims: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "n_claims": len(self.claims),
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_claims(
    claims: Any,
    manifest=None,
    provenance=None,
    strict: bool = True,
) -> ValidationResult:
    """Validate claims against the run's manifest and trace.

    Args:
        claims: list of claim dicts (or a dict with a ``claims`` key).
        manifest: a ``RunManifest``; artifact pointers are resolved against it.
        provenance: a ``Provenance``; ``tool_call`` pointers are resolved against it.
        strict: when True, an unresolvable *local* evidence pointer is an error.
            When False it is downgraded to a warning — used by the retrofit tool,
            where historical runs never had a manifest to cite against.

    Returns a ValidationResult. Each accepted evidence entry gains a ``verified``
    flag; see ``evidence_status`` for how the UI presents it, which distinguishes
    an external citation from a link that failed to resolve rather than implying
    every link was checked.
    """
    res = ValidationResult()

    if isinstance(claims, dict):
        claims = claims.get("claims", [])
    if not isinstance(claims, list):
        res.errors.append("claims must be a list of claim objects")
        return res

    known_paths: dict[str, dict] = {}
    if manifest is not None:
        known_paths = dict(manifest.data.get("artifacts", {}))

    seen_ids: set[str] = set()

    for i, raw in enumerate(claims):
        where = f"claim[{i}]"
        if not isinstance(raw, dict):
            res.errors.append(f"{where}: not an object")
            continue

        cid = str(raw.get("id") or "").strip()
        if not cid:
            res.errors.append(f"{where}: missing 'id'")
            continue
        if not CLAIM_ID_RE.match(cid):
            res.errors.append(f"{where}: id {cid!r} must match {CLAIM_ID_RE.pattern}")
            continue
        if cid in seen_ids:
            res.errors.append(f"claim {cid}: duplicate id")
            continue
        seen_ids.add(cid)

        text = str(raw.get("text") or "").strip()
        if not text:
            res.errors.append(f"claim {cid}: missing 'text'")
            continue

        confidence = str(raw.get("confidence") or "moderate").lower()
        if confidence not in CONFIDENCE_LEVELS:
            res.warnings.append(
                f"claim {cid}: unknown confidence {confidence!r}, using 'moderate'"
            )
            confidence = "moderate"

        raw_evidence = raw.get("evidence") or []
        if not isinstance(raw_evidence, list) or not raw_evidence:
            # A claim with no evidence defeats the purpose of the mechanism.
            res.errors.append(f"claim {cid}: must cite at least one piece of evidence")
            continue

        evidence: list[dict[str, Any]] = []
        for j, ev in enumerate(raw_evidence):
            if not isinstance(ev, dict):
                res.errors.append(f"claim {cid}: evidence[{j}] is not an object")
                continue
            kind = str(ev.get("kind") or "artifact").lower()
            if kind not in EVIDENCE_KINDS:
                res.errors.append(
                    f"claim {cid}: evidence[{j}] unknown kind {kind!r} "
                    f"(expected one of {sorted(EVIDENCE_KINDS)})"
                )
                continue

            entry: dict[str, Any] = {"kind": kind, "verified": False}
            if ev.get("note"):
                entry["note"] = str(ev["note"])[:500]

            if kind in LOCAL_KINDS:
                path = str(ev.get("path") or "").strip()
                if not path:
                    res.errors.append(f"claim {cid}: evidence[{j}] ({kind}) missing 'path'")
                    continue
                entry["path"] = path
                rec = known_paths.get(path) or _match_by_suffix(known_paths, path)
                if rec is None:
                    msg = (f"claim {cid}: evidence[{j}] cites {path!r}, "
                           f"which is not a registered artifact of this run")
                    (res.errors if strict else res.warnings).append(msg)
                    if strict:
                        continue
                else:
                    entry["path"] = rec["path"]
                    entry["sha256"] = rec.get("sha256")
                    entry["produced_by"] = rec.get("produced_by")
                    entry["verified"] = True
                    if ev.get("line"):
                        entry["line"] = ev["line"]

            elif kind == "tool_call":
                tuid = str(ev.get("tool_use_id") or "").strip()
                if not tuid:
                    entry_err = f"claim {cid}: evidence[{j}] (tool_call) missing 'tool_use_id'"
                    res.errors.append(entry_err)
                    continue
                entry["tool_use_id"] = tuid
                if provenance is not None and provenance.has_tool_call(tuid):
                    call = provenance.calls[tuid]
                    entry["tool_name"] = call.get("tool_name")
                    entry["agent"] = call.get("agent")
                    entry["ts"] = call.get("started_at")
                    entry["verified"] = True
                else:
                    msg = (f"claim {cid}: evidence[{j}] cites tool call {tuid!r}, "
                           f"which does not appear in this run's trace")
                    (res.errors if strict else res.warnings).append(msg)
                    if strict:
                        continue

            else:  # citation — external, not checkable from here
                ref = ev.get("pmid") or ev.get("doi") or ev.get("url")
                if not ref:
                    res.errors.append(
                        f"claim {cid}: evidence[{j}] (citation) needs pmid, doi or url"
                    )
                    continue
                for k in ("pmid", "doi", "url", "title"):
                    if ev.get(k):
                        entry[k] = ev[k]
                # Deliberately left verified=False: we did not check it.
                res.warnings.append(
                    f"claim {cid}: external citation {ref} is not locally verifiable"
                )

            evidence.append(entry)

        if not evidence:
            res.errors.append(f"claim {cid}: no evidence survived validation")
            continue

        agent = raw.get("agent")
        if not agent:
            # Infer from the evidence rather than leaving it blank.
            producers = [e.get("produced_by") or e.get("agent") for e in evidence]
            producers = [p for p in producers if p]
            agent = producers[0] if producers else None

        res.claims.append({
            "id": cid,
            "text": text,
            "agent": agent,
            "confidence": confidence,
            "turn": raw.get("turn"),
            "evidence": evidence,
            "n_verified": sum(1 for e in evidence if e["verified"]),
        })

    return res


def _match_by_suffix(known: dict[str, dict], path: str) -> Optional[dict]:
    """Resolve a claim's path against the manifest tolerantly.

    Agents cite a mix of absolute paths, run-relative paths and bare basenames.
    Rejecting a real artifact over a path-prefix mismatch would train the CSO to
    stop citing evidence, so match on suffix and then on basename — but only when
    it is unambiguous, since a wrong match is a false provenance link.
    """
    if path in known:
        return known[path]

    norm = path.lstrip("./")
    hits = [rec for key, rec in known.items() if key == norm or key.endswith("/" + norm)]
    if len(hits) == 1:
        return hits[0]

    base = Path(path).name
    hits = [rec for key, rec in known.items() if Path(key).name == base]
    return hits[0] if len(hits) == 1 else None


class ClaimSet:
    """A validated set of claims for one run."""

    def __init__(self, claims: Optional[list[dict[str, Any]]] = None):
        self.claims: list[dict[str, Any]] = list(claims or [])

    @classmethod
    def load(cls, path) -> "ClaimSet":
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p) as f:
            data = json.load(f)
        return cls(data.get("claims", data if isinstance(data, list) else []))

    def by_id(self, cid: str) -> Optional[dict[str, Any]]:
        return next((c for c in self.claims if c["id"] == cid), None)

    def add(self, claims: list[dict[str, Any]]) -> None:
        """Merge in new claims; a re-filed id replaces the earlier version."""
        index = {c["id"]: i for i, c in enumerate(self.claims)}
        for c in claims:
            if c["id"] in index:
                self.claims[index[c["id"]]] = c
            else:
                index[c["id"]] = len(self.claims)
                self.claims.append(c)

    def link_into_manifest(self, manifest) -> None:
        """Populate each artifact's ``cited_by`` so the manifest reads both ways:
        claim → artifact, and artifact → the claims that depend on it."""
        for e in manifest.data.get("artifacts", {}).values():
            e["cited_by"] = []
        for c in self.claims:
            for ev in c["evidence"]:
                p = ev.get("path")
                if p and p in manifest.data.get("artifacts", {}):
                    cited = manifest.data["artifacts"][p]["cited_by"]
                    if c["id"] not in cited:
                        cited.append(c["id"])

    def stats(self) -> dict[str, Any]:
        n_ev = sum(len(c["evidence"]) for c in self.claims)
        return {
            "n_claims": len(self.claims),
            "n_evidence": n_ev,
            "n_verified_evidence": sum(c["n_verified"] for c in self.claims),
            "claims_without_verified_evidence": [
                c["id"] for c in self.claims if c["n_verified"] == 0
            ],
            "by_agent": _count(c.get("agent") or "unattributed" for c in self.claims),
            "by_confidence": _count(c["confidence"] for c in self.claims),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"stats": self.stats(), "claims": self.claims}

    def write(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return path


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def find_claim_refs(text: str) -> list[str]:
    """Claim ids referenced inline in a block of CSO prose, in order."""
    return CLAIM_REF_RE.findall(text or "")


def check_refs_resolve(text: str, claim_set: ClaimSet) -> list[str]:
    """Claim ids cited in prose that were never filed — a dangling superscript.

    Surfaced in the run README so a missing ``record_claims`` call is visible
    rather than silently rendering an anchor that opens an empty panel.
    """
    return sorted({r for r in find_claim_refs(text) if claim_set.by_id(r) is None})
