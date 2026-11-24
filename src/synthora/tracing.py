import os

import mlflow
from loguru import logger

from synthora.config import TracingConfig


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
    except Exception as e:
        mlflow.tracing.disable()
        logger.warning(
            f"MLflow unreachable at {config.tracking_uri}, tracing is off: {e}"
        )
        return

    mlflow.langchain.autolog()  # every LangGraph node and ChatAnthropic call becomes a span
    logger.info(f"tracing to {config.tracking_uri} (experiment {config.experiment!r})")
