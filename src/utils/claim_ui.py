"""
Claim–evidence rendering for the web interface
The Virtual Biotech

Reviewer comment R2.3 asks for claim-evidence objects in the UI: click a claim,
see where it came from. This module turns a validated ClaimSet into the three
surfaces that provide it:

  * ``render_claim_refs``     — ``[[claim:C3]]`` in CSO prose → a clickable marker
  * ``render_evidence_panel`` — the sidebar the click handler writes into
  * ``render_evidence_static``— the same content rendered server-side

The third one matters. An audit trail that only exists once JavaScript runs is
not an audit trail: the static list is always present, is what the chat export
carries, and is what a reader sees if the script never executes.

Kept out of gradio_cso_app so it can be tested without a Gradio install.
"""

import html
import json

from src.utils.claims import (
    CLAIM_REF_RE, EVIDENCE_STATUS_LABELS, ClaimSet, evidence_status,
)


def esc(s) -> str:
    """HTML-escape. These strings are model-authored, so nothing is trusted."""
    return html.escape("" if s is None else str(s), quote=True)

def _strip_claim_refs(text: str) -> str:
    """Hide claim anchors while a response is still streaming.

    The CSO files its claims at the end of a turn, so mid-stream the ids do not
    resolve yet. Showing raw ``[[claim:C1]]`` or a broken marker to the user for
    those seconds would be noise; the real anchors go in once the turn completes.
    """
    return CLAIM_REF_RE.sub("", text or "")


def render_claim_refs(text: str, claim_set: ClaimSet) -> str:
    """Turn ``[[claim:C3]]`` anchors in CSO prose into clickable superscripts.

    A claim id that was never filed is rendered as a visibly broken marker rather
    than silently dropped — a dangling citation is a defect the reader should see,
    not something to paper over.
    """
    if not text or "[[claim:" not in text:
        return text

    order: dict[str, int] = {}

    def repl(m):
        cid = m.group(1)
        claim = claim_set.by_id(cid)
        if claim is None:
            return (f'<sup class="claim-ref missing" title="No evidence was filed '
                    f'for {cid}">{cid}?</sup>')
        n = order.setdefault(cid, len(order) + 1)
        ver = claim.get("n_verified", 0)
        title = claim["text"].replace('"', "&quot;")[:180]
        return (f'<sup class="claim-ref{"" if ver else " unverified"}" '
                f'data-claim="{cid}" title="{title}">{n}</sup>')

    return CLAIM_REF_RE.sub(repl, text)


def render_evidence_panel(claim_set: ClaimSet) -> str:
    """The Evidence sidebar: a JSON payload plus the panel the JS writes into.

    Claims are embedded as JSON so clicking an anchor resolves instantly on the
    client with no server round-trip.
    """
    payload = json.dumps({c["id"]: c for c in claim_set.claims}, default=str)
    if not claim_set.claims:
        body = ('<div class="ev-empty">Evidence for the CSO\'s claims appears here. '
                'Click any numbered marker in the conversation.</div>')
    else:
        n = len(claim_set.claims)
        ver = sum(1 for c in claim_set.claims if c["n_verified"])
        body = (f'<div class="ev-empty">{n} claim{"s" if n != 1 else ""} on record '
                f'({ver} with verified evidence). Click a numbered marker in the '
                f'conversation to see what backs it.</div>')
    return (
        f'<script type="application/json" id="vbt-claims">{payload}</script>'
        f'<div class="sidebar-header">Evidence</div>'
        f'<div id="vbt-evidence">{body}</div>'
    )


def _evidence_row_html(ev: dict) -> str:
    """One evidence line, shaped like the click-panel's rows so the two agree."""
    status = evidence_status(ev)
    badge = (f'<span class="ev-badge {status}">'
             f'{esc(EVIDENCE_STATUS_LABELS[status])}</span>')

    if ev["kind"] == "tool_call":
        what = f'<code>{esc(ev.get("tool_name") or ev.get("tool_use_id"))}</code>'
        if ev.get("agent"):
            what += f' <span class="ev-dim">by {esc(ev["agent"])}</span>'
    elif ev["kind"] == "citation":
        what = esc(ev.get("pmid") or ev.get("doi") or ev.get("url"))
        if ev.get("title"):
            what += f' <span class="ev-dim">{esc(ev["title"])}</span>'
    else:
        what = f'<code>{esc(ev.get("path"))}</code>'
        if ev.get("line"):
            what += f' <span class="ev-dim">line {esc(ev["line"])}</span>'

    note = f'<span class="ev-note">{esc(ev["note"])}</span>' if ev.get("note") else ""
    return (f'<li><span class="ev-kind">{esc(ev["kind"])}</span>{what}'
            f'{note}{badge}</li>')


def render_evidence_static(claim_set: ClaimSet) -> str:
    """Server-rendered card per claim, for the always-present accordion.

    The audit path must not depend on JavaScript: this is what a reader sees if
    the click handler never runs, and it must not disagree with the panel that
    handler writes. Same card markup, same badges — only the trigger differs.
    """
    if not claim_set.claims:
        return '<div class="ev-empty">No claims filed yet this session.</div>'

    cards = []
    for i, c in enumerate(claim_set.claims, 1):
        rows = "".join(_evidence_row_html(ev) for ev in c["evidence"])
        if not rows:
            rows = ('<li class="ev-none">No evidence was attached to this '
                    'claim.</li>')
        # A claim with nothing resolvable behind it is the one case worth
        # flagging at card level. Everything else stays quiet — a card that
        # shouts on every row teaches the reader to ignore it.
        weak = "" if c["n_verified"] else " ev-card-weak"
        flag = ("" if c["n_verified"] else
                '<span class="ev-flag">no verified evidence</span>')
        cards.append(
            f'<div class="ev-card ev-card-static{weak}">'
            f'<div class="ev-id">'
            f'<span class="ev-num">{i}</span>{esc(c["id"])}{flag}'
            f'</div>'
            f'<div class="ev-text">{esc(c["text"])}</div>'
            f'<div class="ev-meta">{esc(c.get("agent") or "unattributed")}'
            f' · confidence {esc(c["confidence"])}</div>'
            f'<ul class="ev-list">{rows}</ul>'
            f'</div>'
        )
    return f'<div class="ev-cards">{"".join(cards)}</div>'


