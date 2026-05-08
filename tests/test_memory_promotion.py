from src.memory_promotion import extract_profile_facts


def test_extracts_morning_medication_fact():
    facts = extract_profile_facts("User: I take medication in the morning")

    assert ("personal", "Travis takes medication in the morning") in facts


def test_extracts_project_and_medical_facts():
    facts = extract_profile_facts(
        "Add finance tracking, a prediction engine, blood pressure, and mental health questions."
    )

    assert ("work", "Travis wants finance tracking added to Andrew's project work") in facts
    assert ("work", "Travis wants a prediction engine for routines and daily patterns") in facts
    assert ("personal", "Travis wants blood pressure included in medical check-ins") in facts
    assert ("personal", "Travis wants mental health questions included in medical check-ins") in facts


def test_extracts_chance_routine_facts():
    facts = extract_profile_facts("I need to let Chance outside and give Chance water.")

    assert ("personal", "Chance needs fresh water as part of Travis's routine") in facts
    assert ("personal", "Chance needs outside/chain time as part of Travis's routine") in facts
