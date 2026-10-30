"""Claim-check mapping: ClaimReview hit → EvidenceItem OR UnverifiedItem."""

import uuid
from datetime import date

from provcheck.core.interfaces import ClaimReviewHit
from provcheck.core.investigators.claimcheck import _hit_to_evidence


def _hit(**over):
    base = dict(
        claim_text="X did Y",
        claimant="X",
        claim_date=date(2026, 1, 1),
        review_url="https://pub.example/review/1",
        review_publisher="PubCheck",
        review_publisher_site="pub.example",
        review_title="X did not do Y",
        review_date=None,
        textual_rating="False",
        language_code="en",
    )
    base.update(over)
    return ClaimReviewHit(**base)


def test_false_rating_becomes_undermines_evidence():
    case_id, run_id, claim_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ev, unv = _hit_to_evidence(case_id, run_id, claim_id, _hit())
    assert ev is not None and unv is None
    assert ev.direction == "undermines"
    assert ev.signal == "factcheck.claimreview_false"
    assert ev.strength >= 0.9
    assert ev.source_url == "https://pub.example/review/1"


def test_true_rating_becomes_supports_evidence():
    ev, unv = _hit_to_evidence(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), _hit(textual_rating="True")
    )
    assert ev is not None and unv is None
    assert ev.direction == "supports"
    assert ev.signal == "factcheck.claimreview_true"


def test_mixed_rating_becomes_neutral_evidence():
    ev, unv = _hit_to_evidence(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), _hit(textual_rating="Half true")
    )
    assert ev is not None and unv is None
    assert ev.direction == "neutral"


def test_unmapped_rating_becomes_unverified_entry():
    ev, unv = _hit_to_evidence(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), _hit(textual_rating="Four Pinocchios")
    )
    assert ev is None and unv is not None
    assert unv.reason == "no_results"
    assert "Four Pinocchios" in (unv.detail or "")
