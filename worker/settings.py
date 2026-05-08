from arq.connections import RedisSettings

from core.config import settings
from worker.tasks import task_index_document, task_index_user_document


class WorkerSettings:
    functions = [task_index_document, task_index_user_document]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    job_timeout = 1800
