from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    log.info(
        "startup",
        zoho_region=settings.zoho_region,
        todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET",
        log_level=settings.log_level,
    )
    yield
    log.info("shutdown")


app = FastAPI(lifespan=lifespan)
