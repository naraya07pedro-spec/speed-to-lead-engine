import pytest

from app.urgency import Urgency, classify, classify_rules


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I think I smell gas near the furnace", Urgency.SAFETY),
        ("CO alarm keeps beeping", Urgency.SAFETY),
        ("Pipe burst under the sink, water everywhere", Urgency.EMERGENCY),
        ("No AC since this morning", Urgency.EMERGENCY),
        ("AC is blowing warm air", Urgency.EMERGENCY),
        ("Need a quote to replace a 15 year old unit", Urgency.STANDARD),
        ("Want to schedule an annual tune-up", Urgency.LOW),
    ],
)
def test_rules(text, expected):
    assert classify_rules(text).urgency is expected


def test_highest_rank_wins():
    # Maintenance wording plus a gas smell must still be a safety case.
    assert classify_rules("annual maintenance, also smells like gas").urgency is Urgency.SAFETY


def test_llm_cannot_downgrade_safety():
    result = classify("smell of gas in kitchen", llm=lambda _: Urgency.LOW)
    assert result.urgency is Urgency.SAFETY
    assert result.source == "rules"


def test_llm_cannot_downgrade_rule_emergency():
    assert classify("no AC and it is 104F", llm=lambda _: Urgency.LOW).urgency is Urgency.EMERGENCY


def test_llm_can_upgrade_standard():
    result = classify("strange noise from the unit", llm=lambda _: Urgency.EMERGENCY)
    assert result.urgency is Urgency.EMERGENCY
    assert result.source == "llm"


def test_llm_failure_falls_back_to_rules():
    def broken(_: str) -> Urgency:
        raise TimeoutError

    assert classify("strange noise", llm=broken).urgency is Urgency.STANDARD
