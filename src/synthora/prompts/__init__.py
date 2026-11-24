"""Every prompt Claude receives. Convention: one module per prompt, each defining PROMPT.

Add a prompt = add a module here. It is registered in MLflow automatically.
"""

import importlib
import pkgutil


def all_prompts() -> dict[str, str]:
    """Module name -> its PROMPT text, for every module in this package"""
    prompts: dict[str, str] = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        if not isinstance(getattr(module, "PROMPT", None), str):
            raise TypeError(f"{module.__name__} must define PROMPT as a string")
        prompts[info.name] = module.PROMPT
    return prompts
