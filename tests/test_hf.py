"""Offline tests for ko.hf — pure logic only, no network."""

import pytest

from ko import hf


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("2602.08025", "2602.08025"),
        ("2602.08025v1", "2602.08025v1"),
        ("https://huggingface.co/papers/2602.08025", "2602.08025"),
        ("https://huggingface.co/papers/2602.08025.md", "2602.08025"),
        ("https://arxiv.org/abs/2602.08025", "2602.08025"),
        ("https://arxiv.org/pdf/2602.08025v2", "2602.08025v2"),
    ],
)
def test_paper_id(ref, expected):
    assert hf.paper_id(ref) == expected


def test_paper_id_rejects_garbage():
    with pytest.raises(ValueError):
        hf.paper_id("not a paper")


def test_paper_from_dict_minimal():
    p = hf._paper(
        {
            "id": "2412.20138",
            "title": "Trading\nAgents",
            "publishedAt": "2024-12-28T12:54:06.000Z",
            "upvotes": None,
        }
    )
    assert p.title == "Trading Agents"  # newlines flattened
    assert p.upvotes == 0
    assert p.linked_models == []
    assert p.hf_url == "https://huggingface.co/papers/2412.20138"


def test_paper_from_dict_full():
    p = hf._paper(
        {
            "id": "1",
            "title": "T",
            "publishedAt": "2026-01-01T00:00:00.000Z",
            "upvotes": 91,
            "githubRepo": "https://github.com/x/y",
            "githubStars": 85117,
            "linkedModels": [{"id": "org/model"}],
            "linkedSpaces": [{"id": "org/space", "emoji": "📊"}],
        }
    )
    assert p.upvotes == 91
    assert p.github_stars == 85117
    assert p.linked_models == ["org/model"]
    assert p.linked_spaces == ["org/space"]
