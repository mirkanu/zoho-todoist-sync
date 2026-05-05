"""Entry point for the worker service. Run with: python -m app.worker"""
from arq import run_worker

from app.worker.settings import WorkerSettings

if __name__ == "__main__":
    run_worker(WorkerSettings)
