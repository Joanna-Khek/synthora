from synthora.prompts import all_prompts


def test_every_prompt_module_is_found():
    prompts = all_prompts()
    assert {"distill", "picker"} <= prompts.keys()
    assert all(text.strip() for text in prompts.values())
