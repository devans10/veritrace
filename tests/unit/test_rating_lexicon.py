"""Rating lexicon: known phrases map to (direction, strength); unknown → neutral/0."""

import pytest

from provcheck.core.investigators.rating_lexicon import map_rating


@pytest.mark.parametrize(
    "rating, direction, min_strength",
    [
        ("False", "undermines", 0.9),
        ("FALSE", "undermines", 0.9),
        ("Pants on Fire!", "undermines", 1.0),
        ("Pants-on-fire", "undermines", 1.0),
        ("Fabricated", "undermines", 1.0),
        ("Debunked claim", "undermines", 0.9),
        ("Misleading", "undermines", 0.7),
        ("Mostly False", "undermines", 0.6),
        ("Partly false", "undermines", 0.5),
        ("Half true", "neutral", 0.3),
        ("A mixture", "neutral", 0.3),
        ("Mostly True", "supports", 0.6),
        ("True", "supports", 0.9),
        ("Verified", "supports", 0.8),
        ("Satire", "neutral", 0.2),
    ],
)
def test_known_ratings(rating, direction, min_strength):
    m = map_rating(rating)
    assert m.direction == direction
    assert m.strength >= min_strength - 0.001


@pytest.mark.parametrize(
    "rating", ["Four Pinocchios", "No rating", "Opinion", "", "???", "scale 3/5", "fire"]
)
def test_unknown_rating_is_neutral_zero(rating):
    m = map_rating(rating)
    assert m.direction == "neutral"
    assert m.strength == 0.0


def test_longer_phrases_win_over_shorter():
    # "mostly false" must not collapse to "false"
    assert map_rating("Mostly False").strength < map_rating("False").strength
    # "half true" must not collapse to "true"
    assert map_rating("Half true").direction == "neutral"
    assert map_rating("True").direction == "supports"
