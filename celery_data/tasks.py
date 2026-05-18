import asyncio
import json
import logging
import redis
from celery_data.app import celery_app
from backend.nlivalidation import run_session_validation
import config

logger = logging.getLogger(__name__)
status_redis = redis.from_url(config.REDIS_URL, decode_responses=True)

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_nli_validation_task(self, user_id: int, examples: list):
    """
    Background NLI-validation task in CPU-process in service!
    """
    status_key = f"nli:status:{user_id}"
    result_key = f"nli:result:{user_id}"
    try:
        logger.info(f"[Celery] Запуск NLI-валидации | user={user_id} | examples={len(examples)}!")
        status_redis.set(status_key, "processing", ex=3600)  # Bridge-init. Async. pipeline in sync. cel. worker!
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            validation_result = loop.run_until_complete(
                run_session_validation(examples, user_id)
            )
        finally:
            loop.close()
        # Fixing data to Redis for FastApi-polling!
        final_status = validation_result.get("status", "failed")
        status_redis.set(status_key, final_status, ex=1800)
        status_redis.set(result_key, json.dumps(validation_result, ensure_ascii=False), ex=3600)
        logger.info(f"[Celery] Валидация завершена | user={user_id} | status={final_status}!")
        return {"status": final_status}

    except Exception as exc:
        logger.error(f"[Celery] Ошибка валидации | user={user_id} | {exc}!")
        status_redis.set(status_key, "error", ex=1800)
        raise self.retry(exc=exc)