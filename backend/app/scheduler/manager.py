import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import selectinload

from app.core.sse_router import event_router
from app.models.source import Source, SourceHealth

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULES = {
    "finance_stock_quote": 30,
    "finance_cn_stock": 30,
    "finance_market_indices": 30,
    "finance_commodities": 60,
    "finance_nav": 120,
    "tech_rss": 300,
    "tech_hackernews": 120,
    "tech_arxiv": 1800,
    "tech_web_scrape": 1800,
}

SOURCE_TYPE_DEFAULT_INTERVALS = {
    "finance_quote": 30,
    "finance_cn_stock": 30,
    "finance_market_indices": 30,
    "finance_commodities": 60,
    "finance_nav": 120,
    "rss": 300,
    "hackernews": 120,
    "arxiv": 1800,
    "web_scrape": 1800,
    "api": 60,
    "social": 600,
}

_source_category_cache: dict[str, str] = {}


class AsyncSchedulerManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "max_instances": 1,
                "misfire_grace_time": 60,
                "coalesce": True,
            },
        )
        self._running = False
        self._original_intervals: dict[str, int] = {}
        self._adaptive_multipliers: dict[str, float] = {}
        self._last_run_times: dict[str, datetime] = {}
        self._last_run_results: dict[str, dict] = {}
        # Adaptive pause switch: auto-pause jobs when there are no SSE subscribers.
        # Only meaningful for the api-embedded scheduler (the SSE connection registry lives
        # in the api process). The worker process has no SSE connections, so it must disable
        # this, otherwise jobs get paused permanently after the first collection round.
        self.adaptive_pause_enabled = True

    def disable_adaptive_pause(self) -> None:
        # Called by the worker process before starting the scheduler: the SSE connection
        # dict lives in the api process (core/sse_router.py), so get_connections_count() is
        # always 0 in the worker. Without disabling, every source would be paused
        # permanently after its first collection round.
        self.adaptive_pause_enabled = False

    async def start(self) -> None:
        if not self._running:
            self.scheduler.start()
            self._running = True
            logger.info("APScheduler started")

    async def shutdown(self, wait: bool = True) -> None:
        if self._running:
            self.scheduler.shutdown(wait=wait)
            self._running = False
            logger.info("APScheduler shutdown")

    async def add_job(
        self,
        job_id: str,
        func: Any,
        interval_seconds: int,
        kwargs: dict | None = None,
    ) -> None:
        if self.scheduler.get_job(job_id):
            logger.warning(f"Job {job_id} already exists, rescheduling")
            await self.reschedule_job(job_id, interval_seconds)
            return

        self._original_intervals[job_id] = interval_seconds
        self._adaptive_multipliers[job_id] = 1.0

        self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            # Fix: IntervalTrigger waits a full interval before its first fire by default.
            # Passing next_run_time makes the first collection run immediately.
            # datetime.now(UTC) is timezone-aware; APScheduler converts it to the scheduler
            # timezone automatically, which is compatible.
            next_run_time=datetime.now(UTC),
        )
        logger.info(f"Job {job_id} added with interval {interval_seconds}s")

    async def add_collection_job(self, source_id: str, interval_seconds: int, source_type: str = "") -> None:
        job_id = f"collect_{source_id}"

        default_interval = SOURCE_TYPE_DEFAULT_INTERVALS.get(source_type, 300)
        if not interval_seconds or interval_seconds < 10:
            interval_seconds = default_interval

        await self.add_job(
            job_id=job_id,
            func=self._run_collection,
            interval_seconds=interval_seconds,
            kwargs={"source_id": source_id},
        )

    async def remove_job(self, job_id: str) -> None:
        job = self.scheduler.get_job(job_id)
        if job:
            self.scheduler.remove_job(job_id)
            self._original_intervals.pop(job_id, None)
            self._adaptive_multipliers.pop(job_id, None)
            _source_category_cache.pop(job_id.replace("collect_", ""), None)
            logger.info(f"Job {job_id} removed")

    async def pause_job(self, job_id: str) -> None:
        job = self.scheduler.get_job(job_id)
        if job:
            job.pause()
            logger.info(f"Job {job_id} paused")

    async def resume_job(self, job_id: str) -> None:
        job = self.scheduler.get_job(job_id)
        if job:
            job.resume()
            logger.info(f"Job {job_id} resumed")

    async def reschedule_job(self, job_id: str, interval_seconds: int) -> None:
        job = self.scheduler.get_job(job_id)
        if job:
            job.reschedule(trigger=IntervalTrigger(seconds=interval_seconds))
            logger.info(f"Job {job_id} rescheduled to {interval_seconds}s")

    async def get_jobs_status(self) -> list[dict]:
        jobs = self.scheduler.get_jobs()
        result = []
        for job in jobs:
            job_id = job.id
            source_id = job_id.replace("collect_", "") if job_id.startswith("collect_") else None

            last_run = self._last_run_times.get(job_id)
            last_run_result = self._last_run_results.get(job_id, {})

            result.append(
                {
                    "job_id": job_id,
                    "name": job.name or job_id,
                    "source_id": source_id,
                    "next_run": str(job.next_run_time) if job.next_run_time else None,
                    "trigger": str(job.trigger),
                    "pending": job.pending,
                    "original_interval": self._original_intervals.get(job_id, 0),
                    "adaptive_multiplier": self._adaptive_multipliers.get(job_id, 1.0),
                    "last_run": str(last_run) if last_run else None,
                    "last_run_success": last_run_result.get("success"),
                    "last_run_items_count": last_run_result.get("items_count", 0),
                    "last_run_error": last_run_result.get("error"),
                }
            )
        return result

    async def schedule_all_active_sources(self, sources: list[Any]) -> None:
        for source in sources:
            if getattr(source, "is_active", False):
                interval = getattr(source, "refresh_interval_seconds", 300) or 300
                category = getattr(source, "category", None)
                if category:
                    category_slug = getattr(category, "slug", "")
                    _source_category_cache[str(source.id)] = category_slug

                job_id = f"collect_{source.id}"
                await self.add_job(
                    job_id=job_id,
                    func=self._run_collection,
                    interval_seconds=interval,
                    kwargs={"source_id": str(source.id)},
                )

    async def setup_default_jobs(self) -> None:
        logger.info("Setting up default collection jobs")

    async def adaptive_reschedule(self, source_id: str, health_status: str) -> None:
        job_id = f"collect_{source_id}"
        original = self._original_intervals.get(job_id)
        if original is None:
            logger.warning(f"No original interval for job {job_id}")
            return

        current_multiplier = self._adaptive_multipliers.get(job_id, 1.0)

        category = self._get_category_for_source(source_id)
        source_connections = event_router.get_connections_by_category(category)
        active_connections = event_router.get_connections_count()

        # Only pause based on the SSE subscriber count when adaptive pause is enabled
        # (api-embedded scheduler scenario). In the worker process the SSE connection
        # registry is always empty, and this branch is turned off via
        # disable_adaptive_pause(); otherwise jobs would be paused permanently after the
        # first collection round.
        if self.adaptive_pause_enabled and (active_connections == 0 or len(source_connections) == 0):
            job = self.scheduler.get_job(job_id)
            if job and not job.pending:
                await self.pause_job(job_id)
                logger.info(f"Job {job_id} paused: no SSE subscribers")
            return

        if health_status == "healthy":
            target_multiplier = 1.0
        elif health_status == "degraded":
            target_multiplier = 2.0
        elif health_status == "down":
            target_multiplier = 10.0
        else:
            target_multiplier = current_multiplier

        if current_multiplier > target_multiplier:
            step_down = max(target_multiplier, current_multiplier / 2)
            target_multiplier = step_down

        new_interval = int(original * target_multiplier)
        self._adaptive_multipliers[job_id] = target_multiplier

        if self.scheduler.get_job(job_id):
            job = self.scheduler.get_job(job_id)
            if job.pending:
                await self.resume_job(job_id)
            await self.reschedule_job(job_id, new_interval)
            logger.info(
                f"Job {job_id} adaptively rescheduled: "
                f"status={health_status}, multiplier={target_multiplier}, "
                f"interval={new_interval}s"
            )

    def _get_category_for_source(self, source_id: str) -> str:
        cached = _source_category_cache.get(source_id)
        if cached:
            return cached

        try:
            from sqlalchemy import select

            from app.db.session import async_session_factory
            from app.models.category import Category
            from app.models.source import Source

            async def _lookup():
                async with async_session_factory() as session:
                    stmt = (
                        select(Source, Category)
                        .join(Category, Source.category_id == Category.id)
                        .where(Source.id == source_id)
                    )
                    result = await session.execute(stmt)
                    row = result.one_or_none()
                    if row:
                        _, category = row
                        slug = category.slug
                        _source_category_cache[source_id] = slug
                        return slug
                return "finance"

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return "finance"
            else:
                result = loop.run_until_complete(_lookup())
                return result
        except Exception:
            return "finance"

    async def _run_collection(self, source_id: str) -> None:
        logger.info(f"Running collection for source {source_id}")
        job_id = f"collect_{source_id}"
        self._last_run_times[job_id] = datetime.now(UTC)

        try:
            from sqlalchemy import select

            from app.collectors import resolve_collector
            from app.db.session import async_session_factory
            from app.processors import create_default_processor_chain
            from app.services.sse import SSEService

            async with async_session_factory() as session:
                result = await session.execute(
                    select(Source)
                    .where(Source.id == source_id)
                    .options(
                        selectinload(Source.category),
                    )
                )
                source = result.scalar_one_or_none()

                if source is None:
                    logger.warning(f"Source {source_id} not found in DB")
                    self._last_run_results[job_id] = {"success": False, "error": "Source not found", "items_count": 0}
                    return

                if not source.is_active:
                    logger.info(f"Source {source_id} is inactive, skipping collection")
                    self._last_run_results[job_id] = {"success": False, "error": "Source inactive", "items_count": 0}
                    return

                if source.category:
                    _source_category_cache[str(source.id)] = source.category.slug

                # Collector selection: source_type match first, then the config.library
                # fallback (resolve_collector in app.collectors). Template sources like
                # source_type=api + library=yfinance / web_scrape + library=eastmoney
                # resolve to their real collectors without per-library special cases.
                collector_cls = resolve_collector(source.source_type, source.config)
                if collector_cls is None:
                    logger.warning(f"No collector for source_type={source.source_type}")
                    self._last_run_results[job_id] = {
                        "success": False,
                        "error": f"No collector for {source.source_type}",
                        "items_count": 0,
                    }
                    return

                collector = collector_cls()
                collection_result = await collector.collect(source)

                if not collection_result.success:
                    logger.warning(f"Collection failed for source {source.name}: {collection_result.error}")
                    self._last_run_results[job_id] = {
                        "success": False,
                        "error": collection_result.error,
                        "items_count": 0,
                    }
                    await self._update_health_after_collection(source, collection_result)
                    return

                if not collection_result.items:
                    logger.info(f"No new items from source {source.name}")
                    self._last_run_results[job_id] = {"success": True, "items_count": 0}
                    await self._update_health_after_collection(source, collection_result)
                    return

                chain = create_default_processor_chain()
                results = await chain.execute(collection_result.items, source)

                sse_service = SSEService()
                tenant_id = str(source.tenant_id) if source.tenant_id else "default"
                category_slug = _source_category_cache.get(str(source.id), "")
                if not category_slug and source.category:
                    category_slug = source.category.slug

                stored_count = 0
                for process_result in results:
                    if process_result.item is None:
                        continue
                    if process_result.errors:
                        logger.warning(
                            f"Processing errors for item {process_result.item.get('url', 'unknown')}: "
                            f"{process_result.errors}"
                        )

                    try:
                        from app.models.item import Item

                        item_obj = Item(
                            tenant_id=source.tenant_id,
                            category_id=source.category_id,
                            source_id=source.id,
                            title=process_result.item.get("title", ""),
                            summary=process_result.item.get("summary", ""),
                            url=process_result.item.get("url", ""),
                            image_url=process_result.item.get("image_url", ""),
                            topic_tags=process_result.item.get("topic_tags", []),
                            extra_data=process_result.item.get("extra_data", {}),
                            priority=process_result.item.get("priority", 5),
                            is_processed=True,
                        )

                        published_at_str = process_result.item.get("published_at", "")
                        if published_at_str:
                            try:
                                published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                                item_obj.published_at = published_at
                            except (ValueError, TypeError):
                                item_obj.published_at = datetime.now(UTC)
                        else:
                            item_obj.published_at = datetime.now(UTC)

                        session.add(item_obj)
                        stored_count += 1

                        await sse_service.publish_item_update(
                            category=category_slug,
                            item_data={
                                "id": str(item_obj.id),
                                "title": item_obj.title,
                                "summary": item_obj.summary,
                                "url": item_obj.url,
                                "source_name": source.name,
                                "topic_tags": item_obj.topic_tags,
                                "published_at": item_obj.published_at.isoformat() if item_obj.published_at else "",
                                "priority": item_obj.priority,
                            },
                            tenant_id=tenant_id,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to store/push item: {e}")

                await session.commit()
                logger.info(
                    f"Collection complete for source {source.name}: "
                    f"collected={len(collection_result.items)}, stored={stored_count}"
                )

                self._last_run_results[job_id] = {
                    "success": True,
                    "items_count": stored_count,
                }

                await self._update_health_after_collection(source, collection_result)

        except Exception as e:
            logger.error(f"Collection pipeline error for source {source_id}: {e}")
            self._last_run_results[job_id] = {"success": False, "error": str(e), "items_count": 0}

    async def _update_health_after_collection(self, source: Any, result: Any) -> None:
        try:
            from sqlalchemy import select

            from app.db.session import async_session_factory

            async with async_session_factory() as session:
                health_result = await session.execute(select(SourceHealth).where(SourceHealth.source_id == source.id))
                health = health_result.scalar_one_or_none()

                previous_status = "healthy"

                if health:
                    previous_status = health.status
                    if result.success:
                        health.consecutive_failures = 0
                        health.last_success_at = datetime.now(UTC)
                        health.total_fetches_24h += 1
                        health.success_count_24h += 1

                        prev_avg = health.avg_response_time_ms
                        total = health.total_fetches_24h
                        if total > 0 and result.response_time_ms > 0:
                            health.avg_response_time_ms = int(
                                (prev_avg * (total - 1) + result.response_time_ms) / total
                            )

                        if health.status == "degraded":
                            health.status = "healthy"
                        elif health.status == "down":
                            health.status = "degraded"
                    else:
                        health.consecutive_failures += 1
                        health.last_failure_at = datetime.now(UTC)
                        health.last_error_message = result.error
                        health.total_fetches_24h += 1

                        if health.consecutive_failures >= 10:
                            health.status = "down"
                        elif health.consecutive_failures >= 3:
                            health.status = "degraded"

                    await session.commit()
                else:
                    health = SourceHealth(
                        source_id=source.id,
                        status="healthy" if result.success else "degraded",
                        consecutive_failures=0 if result.success else 1,
                        total_fetches_24h=1,
                        success_count_24h=1 if result.success else 0,
                        avg_response_time_ms=result.response_time_ms,
                        last_success_at=datetime.now(UTC) if result.success else None,
                        last_failure_at=datetime.now(UTC) if not result.success else None,
                        last_error_message=result.error if not result.success else None,
                    )
                    session.add(health)
                    await session.commit()
                    previous_status = "healthy"

                if health.status != previous_status:
                    from app.services.sse import SSEService

                    sse_service = SSEService()
                    await sse_service.publish_source_health_update(
                        source_id=str(source.id),
                        status=health.status,
                        last_error=health.last_error_message,
                        tenant_id=str(source.tenant_id) if source.tenant_id else "default",
                    )

                await self.adaptive_reschedule(str(source.id), health.status)

        except Exception as e:
            logger.warning(f"Failed to update health for source {source.id}: {e}")


scheduler_manager = AsyncSchedulerManager()
