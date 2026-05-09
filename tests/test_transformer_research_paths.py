from scripts import transformer_research


def test_topic_slug_avoids_research_research_duplication():
    assert transformer_research._topic_slug("research sesame ai") == "sesame_ai"


def test_topic_slug_is_stable_for_transformer_topic():
    slug = transformer_research._topic_slug("transformer architectures for fine-tuning")
    assert slug.startswith("transformer")
    assert "research_research" not in slug
