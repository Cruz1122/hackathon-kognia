from app.features.agent.tools import execute_tool, generate_lorem_ipsum, sum_numbers


def test_lorem_ipsum_matches_requested_length() -> None:
    text = generate_lorem_ipsum(200)
    assert len(text) == 200
    assert text.lower().startswith("lorem ipsum")


def test_sum_numbers_adds_user_values() -> None:
    assert sum_numbers([4, 7, 12]) == "23"
    assert execute_tool("sum_numbers", {"numbers": [1.5, 2.5]}) == "4"
