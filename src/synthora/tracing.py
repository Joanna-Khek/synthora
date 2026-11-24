import os

import mlflow
import mlflow.genai
from loguru import logger

from synthora.config import TracingConfig
from synthora.prompts import all_prompts

REGISTRY_PREFIX = "synthora-"

_prompt_uris: dict[str, str] = {}


def register_prompts() -> None:
    """Register each prompt, creating a new version only when its text changed."""
    for name, text in all_prompts().items():
        registry_name = REGISTRY_PREFIX + name
        latest = mlflow.genai.load_prompt(
            f"prompts:/{registry_name}@latest", allow_missing=True
        )
        if latest is None or latest.template != text:
            latest = mlflow.genai.register_prompt(
                registry_name, text, commit_message="changed in code"
            )
            logger.info(f"registered prompt {latest.uri}")
        _prompt_uris[name] = latest.uri


def link_prompt(name: str) -> None:
    """Record on the current span and trace which prompt version is used. No-op when tracing is off."""
    if not _prompt_uris:
        return  # tracing off, or MLflow is unreachable
    uri = _prompt_uris.get(name)
    if uri is None:
        logger.warning(
            f"link_prompt({name!r}): no such prompt; known: {sorted(_prompt_uris)}"
        )
        return
    try:
        mlflow.genai.load_prompt(uri)  # cached
    except Exception as e:
        logger.warning(f"couldn't link prompt {uri}: {e}")


def setup_tracing(config: TracingConfig) -> None:
    if not config.enabled:
        mlflow.tracing.disable()
        logger.info("Tracing disabled in config")
        return

    # MLflow retries HTTP calls with backoff by default. With the server down,
    # startup would hang for minutes, so fail fast instead.
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "5")

    try:
        mlflow.set_tracking_uri(uri=config.tracking_uri)
        mlflow.set_experiment(config.experiment)
        register_prompts()
    except Exception as e:
        mlflow.tracing.disable()
        logger.warning(
            f"MLflow unreachable at {config.tracking_uri}, tracing is off: {e}"
        )
        return

    # every LangGraph node and ChatAnthropic call becomes a span
    # run_tracer_inline: run the callback in our async task, so @mlflow.trace spans
    # (Tools.read/resolve) nest under the graph node that called them.
    mlflow.langchain.autolog(run_tracer_inline=True)
    logger.info(f"tracing to {config.tracking_uri} (experiment {config.experiment!r})")
