import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import selectinload

from app.core.constants import SYSTEM_TENANT_ID
from app.core.sse_router import event_router
from app.models.source import Source, SourceHealth
from app.schemas.tenant import REFRESH_OVERRIDE_MAX_SECONDS, REFRESH_OVERRIDE_MIN_SECONDS

logger = logging.getLogger(__name__)

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

# Load-aware collection throttling (finance-tab.md §3.8.3). Thresholds:
# more than SSE_LOAD_THRESHOLD active SSE connections, or Redis memory above
# REDIS_MEM_LOAD_THRESHOLD of maxmemory, each slow collection down by
# LOAD_MULTIPLIER. The signals never stack — the applied load multiplier is
# capped at LOAD_MULTIPLIER (max, not product). It combines multiplicatively
# with the health-based multiplier from adaptive_reschedule:
#   effective interval = original × health multiplier × load multiplier
SSE_LOAD_THRESHOLD = 500
REDIS_MEM_LOAD_THRESHOLD = 0.8
LOAD_MULTIPLIER = 2.0

# Periodic market refresh job ids (finance-tab.md §3.8.2): warm the market
# indices / commodities Redis caches and push SSE on the finance channel,
# gated on at least one major market being open.
MARKET_INDICES_REFRESH_JOB_ID = "market_indices_refresh"
COMMODITIES_REFRESH_JOB_ID = "commodities_refresh"

_source_category_cache: dict[str, str] = {}


async def evaluate_load_multiplier() -> float:
    """Load-driven collection throttling multiplier: ×1.0 or ×LOAD_MULTIPLIER.

    Signals (cross-process: the api process exposes its SSE connection count as
    the Redis gauge `sse:active_connections`, the worker reads it back; Redis
    memory pressure is read straight from INFO memory):
      - active SSE connections > SSE_LOAD_THRESHOLD        → ×LOAD_MULTIPLIER
      - Redis used_memory/maxmemory > REDIS_MEM_LOAD_THRESHOLD → ×LOAD_MULTIPLIER
    Both signals together still cap at ×LOAD_MULTIPLIER (they never stack).
    A maxmemory of 0 means "no limit configured" — the memory signal is skipped.
    Any signal read failure degrades to ×1.0: collect at the normal frequency
    rather than throttle on missing or corrupt data (fail-open).
    """
    from app.core.redis import RedisKeys, get_redis_client

    try:
        client = await get_redis_client()
    except Exception as e:
        logger.debug(f"Load multiplier: Redis client unavailable ({e}), keeping frequency")
        return 1.0

    multiplier = 1.0

    try:
        raw = await client.get(RedisKeys.sse_active_connections_key())
        if raw is not None and int(raw) > SSE_LOAD_THRESHOLD:
            multiplier = LOAD_MULTIPLIER
    except (TypeError, ValueError):
        logger.debug("Load multiplier: unreadable SSE connection gauge value, ignoring signal")
    except Exception as e:
        logger.debug(f"Load multiplier: SSE connection gauge read failed ({e}), ignoring signal")

    try:
        info = await client.info("memory")
        used_memory = info.get("used_memory") or 0
        maxmemory = info.get("maxmemory") or 0
        if maxmemory > 0 and used_memory / maxmemory > REDIS_MEM_LOAD_THRESHOLD:
            multiplier = max(multiplier, LOAD_MULTIPLIER)
    except Exception as e:
        logger.debug(f"Load multiplier: Redis INFO memory failed ({e}), ignoring signal")

    return multiplier


def resolve_effective_interval(
    source: Any,
    category_slug: str | None,
    tenant_settings: dict | None,
) -> int:
    """Effective collection interval for a source.

    Priority (design: content-categories.md §3.4.4):
      tenants.settings.refresh_overrides[category.slug]
        → source.refresh_interval_seconds
        → SOURCE_TYPE_DEFAULT_INTERVALS[source.source_type] (fallback 300).

    `source` may be an ORM Source row (startup rebuild) or the event payload
    dict (runtime source events). Malformed/out-of-range override values are
    ignored rather than raised: scheduling must keep working on bad settings
    data; the PUT endpoint is the validation gate.
    """
    if isinstance(source, dict):
        source_interval = source.get("refresh_interval_seconds")
        source_type = source.get("source_type") or ""
    else:
        source_interval = getattr(source, "refresh_interval_seconds", None)
        source_type = getattr(source, "source_type", "") or ""

    if category_slug and isinstance(tenant_settings, dict):
        overrides = tenant_settings.get("refresh_overrides")
        override = overrides.get(category_slug) if isinstance(overrides, dict) else None
        if (
            isinstance(override, int)
            and not isinstance(override, bool)
            and REFRESH_OVERRIDE_MIN_SECONDS <= override <= REFRESH_OVERRIDE_MAX_SECONDS
        ):
            return override

    if source_interval and source_interval >= REFRESH_OVERRIDE_MIN_SECONDS:
        return source_interval

    return SOURCE_TYPE_DEFAULT_INTERVALS.get(source_type, 300)


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
        # Last applied load multiplier per job (system-wide signal from
        # evaluate_load_multiplier, stored per job so interval recomputation
        # stays local). Combined with _adaptive_multipliers (health) — the two
        # multiply, see evaluate_load_multiplier.
        self._load_multipliers: dict[str, float] = {}
        self._last_run_times: dict[str, datetime] = {}
        self._last_run_results: dict[str, dict] = {}
        # Adaptive pause switch: auto-pause jobs when there are no SSE subscribers.
        # Only meaningful for the api-embedded scheduler (the SSE connection registry lives
        # in the api process). The worker process has no SSE connections, so it must disable
        # this, otherwise jobs get paused permanently after the first collection round.
        self.adaptive_pause_enabled = True
        # Periodic market refresh switch (finance-tab.md §3.8.2): when off,
        # add_market_refresh_jobs() registers nothing. Only governs registration;
        # unrelated to the adaptive-pause / load-throttling switches above.
        self.market_refresh_jobs_enabled = True

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
        self._load_multipliers[job_id] = 1.0

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

        # Defense in depth only: the callers (add_source_job /
        # schedule_all_active_sources) already pass the tenant-aware
        # effective interval from resolve_effective_interval.
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
            self._load_multipliers.pop(job_id, None)
            _source_category_cache.pop(job_id.replace("collect_", ""), None)
            logger.info(f"Job {job_id} removed")

    async def add_source_job(self, source: dict, tenant_settings: dict | None = None) -> None:
        # Runtime hook for the worker's source-status listener (source_enabled event):
        # schedule collection for a single source without restarting the worker. The
        # dict is the full source payload published by SourceService, so no DB read
        # is needed to build the job. tenant_settings (if the worker could load them)
        # feeds the refresh-overrides branch of resolve_effective_interval.
        source_id = str(source.get("id") or "")
        if not source_id:
            logger.warning("add_source_job called with a payload missing source id")
            return

        category_slug = source.get("category_slug") or ""
        if category_slug:
            _source_category_cache[source_id] = category_slug

        await self.add_collection_job(
            source_id=source_id,
            interval_seconds=resolve_effective_interval(source, category_slug, tenant_settings),
            source_type=source.get("source_type") or "",
        )

    async def remove_source_job(self, source_id: str) -> None:
        # Runtime hook for the worker's source-status listener (source_disabled /
        # source_deleted events): stop collecting the source immediately.
        source_id = str(source_id or "")
        if not source_id:
            return
        await self.remove_job(f"collect_{source_id}")

    async def add_market_refresh_jobs(self) -> None:
        """Register the periodic market indices / commodities refresh jobs
        (finance-tab.md §3.8.2).

        Two interval jobs that warm the Redis display caches and push SSE on the
        finance channel — the display-chain counterpart of the source collection
        jobs (which only feed the generic items pipeline):
          - market_indices_refresh: MARKET_INDICES_REFRESH_INTERVAL (default 30s)
          - commodities_refresh: COMMODITIES_REFRESH_INTERVAL (default 60s)
        The job bodies gate on market hours (skip silently while every major
        market is closed), which replaces the originally designed
        market_indices_off_hours low-frequency job. The originally designed
        watchlist_quotes_realtime and nav_estimates jobs stay unimplemented —
        watchlist quotes and NAV estimates remain on-demand (see finance-tab.md
        §3.8.1). job_defaults apply from the scheduler-level config, same as
        the source collection jobs.
        """
        if not self.market_refresh_jobs_enabled:
            logger.info("Market refresh jobs disabled, not registering")
            return

        from app.config import settings

        await self.add_job(
            job_id=MARKET_INDICES_REFRESH_JOB_ID,
            func=self._run_market_indices_refresh,
            interval_seconds=settings.market_indices_refresh_interval,
        )
        await self.add_job(
            job_id=COMMODITIES_REFRESH_JOB_ID,
            func=self._run_commodities_refresh,
            interval_seconds=settings.commodities_refresh_interval,
        )

    async def _run_market_indices_refresh(self) -> None:
        await self._run_market_refresh(MARKET_INDICES_REFRESH_JOB_ID, "refresh_market_indices")

    async def _run_commodities_refresh(self) -> None:
        await self._run_market_refresh(COMMODITIES_REFRESH_JOB_ID, "refresh_commodities")

    async def _run_market_refresh(self, job_id: str, refresh_method: str) -> None:
        """Shared body for the market indices / commodities refresh jobs
        (finance-tab.md §3.8.2).

        Market-hours gate: run only while at least one major market is open —
        off hours the refresh is skipped silently to save external API calls
        (this gate replaces the originally designed market_indices_off_hours
        low-frequency job). The refresh itself goes through
        FinanceService.refresh_* (fetch with failover + cache write + SSE push
        of the full array payload), scoped to the system tenant like the
        dashboard metrics collection. Failures are logged only — a periodic job
        must never be killed by one bad round; the next interval retries.
        """
        self._last_run_times[job_id] = datetime.now(UTC)
        try:
            from app.db.session import async_session_factory
            from app.services.finance import FinanceService

            async with async_session_factory() as session:
                service = FinanceService(db=session, redis=None)
                if not service.is_any_market_open():
                    logger.debug(f"{job_id}: all major markets closed, skipping refresh")
                    self._last_run_results[job_id] = {"success": True, "items_count": 0}
                    return
                refreshed = await getattr(service, refresh_method)(str(SYSTEM_TENANT_ID))

            if refreshed:
                logger.info(f"{job_id}: market data cache refreshed and pushed")
                self._last_run_results[job_id] = {"success": True, "items_count": 0}
            else:
                logger.warning(f"{job_id}: refresh failed (failover chain returned no data)")
                self._last_run_results[job_id] = {
                    "success": False,
                    "error": "all failover sources returned no data",
                    "items_count": 0,
                }
        except Exception as e:
            logger.error(f"{job_id} failed: {e}")
            self._last_run_results[job_id] = {"success": False, "error": str(e), "items_count": 0}

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

    async def resume_paused_jobs(self) -> int:
        """Resume every collection job paused by the no-subscriber adaptive pause.

        Triggered by the first-subscriber hook (core/sse_router.py
        _signal_first_subscriber_resume) when the api SSE registry goes 0 → 1:
        the embedded scheduler (dev) is called directly, the worker (prod)
        receives a scheduler_resume event on channel:dashboard.

        The paused set is NOT tracked as separate state: APScheduler's
        job.pending is the same signal the no-subscriber pause relies on (a
        paused job has next_run_time == None) and that get_jobs_status already
        reports as "paused", so a second bookkeeping structure could only
        drift. Only jobs this manager owns (_original_intervals) are touched.

        Each resumed job is rescheduled on its current effective interval
        (original × health multiplier × load multiplier) so a job paused while
        throttled does not come back at a stale rate. Returns the number of
        jobs resumed; a no-op (returns 0) when nothing is paused — including
        the worker process, where adaptive pause is disabled and nothing ever
        gets paused this way.
        """
        resumed = 0
        for job in self.scheduler.get_jobs():
            job_id = job.id
            if not job.pending or job_id not in self._original_intervals:
                continue
            original = self._original_intervals[job_id]
            health_multiplier = self._adaptive_multipliers.get(job_id, 1.0)
            load_multiplier = self._load_multipliers.get(job_id, 1.0)
            new_interval = int(original * health_multiplier * load_multiplier)
            await self.resume_job(job_id)
            await self.reschedule_job(job_id, new_interval)
            resumed += 1

        if resumed:
            logger.info(f"Resumed {resumed} paused job(s): SSE subscribers returned")
        return resumed

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
                    "load_multiplier": self._load_multipliers.get(job_id, 1.0),
                    "last_run": str(last_run) if last_run else None,
                    "last_run_success": last_run_result.get("success"),
                    "last_run_items_count": last_run_result.get("items_count", 0),
                    "last_run_error": last_run_result.get("error"),
                }
            )
        return result

    async def schedule_all_active_sources(
        self,
        sources: list[Any],
        tenant_settings_map: dict[str, dict] | None = None,
    ) -> None:
        # tenant_settings_map ({str(tenant_id): settings}) is loaded once by the
        # caller (worker/api startup) so N sources cost a single settings query;
        # None keeps the legacy behavior (source interval → type default).
        for source in sources:
            if getattr(source, "is_active", False):
                category = getattr(source, "category", None)
                category_slug = getattr(category, "slug", "") if category else ""
                if category_slug:
                    _source_category_cache[str(source.id)] = category_slug

                tenant_settings = (tenant_settings_map or {}).get(str(getattr(source, "tenant_id", "")))
                interval = resolve_effective_interval(source, category_slug, tenant_settings)

                job_id = f"collect_{source.id}"
                await self.add_job(
                    job_id=job_id,
                    func=self._run_collection,
                    interval_seconds=interval,
                    kwargs={"source_id": str(source.id)},
                )

    async def setup_default_jobs(self) -> None:
        logger.info("Setting up default collection jobs")

    async def _apply_load_multiplier(self, source_id: str) -> None:
        """Re-evaluate the system load multiplier once per collection round.

        Called at the start of every _run_collection round; when the freshly
        evaluated multiplier (evaluate_load_multiplier: SSE connection gauge +
        Redis memory pressure) differs from the one last applied to this job,
        the job is rescheduled immediately on
            original × health multiplier × NEW load multiplier.
        Ordering with the health path: the health-driven adaptive_reschedule
        runs at the END of the same round and reads the _load_multipliers
        entry updated here, so the two reschedule paths compose instead of
        overwriting each other's multiplier state.
        """
        job_id = f"collect_{source_id}"
        original = self._original_intervals.get(job_id)
        if original is None:
            return

        load_multiplier = await evaluate_load_multiplier()
        if load_multiplier == self._load_multipliers.get(job_id, 1.0):
            return

        self._load_multipliers[job_id] = load_multiplier
        health_multiplier = self._adaptive_multipliers.get(job_id, 1.0)
        new_interval = int(original * health_multiplier * load_multiplier)
        job = self.scheduler.get_job(job_id)
        if job:
            await self.reschedule_job(job_id, new_interval)
        logger.info(
            f"Job {job_id} load-rescheduled: load_multiplier={load_multiplier}, "
            f"health_multiplier={health_multiplier}, interval={new_interval}s"
        )

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

        # Combined multiplier: the health multiplier (this path) and the load
        # multiplier (_apply_load_multiplier, evaluated per collection round) are
        # stored separately and multiply at reschedule time —
        #   effective interval = original × health multiplier × load multiplier
        # Neither path overwrites the other's state.
        load_multiplier = self._load_multipliers.get(job_id, 1.0)
        new_interval = int(original * target_multiplier * load_multiplier)
        self._adaptive_multipliers[job_id] = target_multiplier

        if self.scheduler.get_job(job_id):
            job = self.scheduler.get_job(job_id)
            if job.pending:
                await self.resume_job(job_id)
            await self.reschedule_job(job_id, new_interval)
            logger.info(
                f"Job {job_id} adaptively rescheduled: "
                f"status={health_status}, health_multiplier={target_multiplier}, "
                f"load_multiplier={load_multiplier}, interval={new_interval}s"
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

        # Load-aware throttling (finance-tab.md §3.8.3): re-evaluate the system
        # load multiplier once per round, BEFORE collecting, so a multiplier
        # change takes effect for this source immediately and the health-driven
        # adaptive_reschedule at the end of the round (which reads the same
        # state) composes with it rather than racing it.
        await self._apply_load_multiplier(source_id)

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
                # Fallback must be str(SYSTEM_TENANT_ID), never a literal like "default":
                # SSE routing forwards events only on exact tenant_id string match, so a
                # non-UUID literal could never equal a registered connection's tenant_id.
                # Unreachable in practice (Source.tenant_id is NOT NULL); contract:
                # docs/dev-guide/design/data-flow.md §3.5.4.
                tenant_id = str(source.tenant_id) if source.tenant_id else str(SYSTEM_TENANT_ID)
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

                # topic_stats_update push (tech-tab.md §3.8): topic stats can only
                # change when tech items were stored, so trigger after a successful
                # tech-source collection that stored at least one item. The publish
                # self-throttles to one push per tenant per 900s window (Redis SET NX)
                # and skips silently when Redis is down, so the collection path never
                # pays more than a cheap throttle check per run.
                if category_slug == "tech" and stored_count > 0:
                    await sse_service.publish_topic_stats_update(tenant_id)

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
                    from app.services.sse import SSEService, build_source_health_update_payload

                    sse_service = SSEService()
                    # Contract: docs/dev-guide/design/data-flow.md §3.5.4 — publish the full
                    # source_health row state so the dashboard health table can match the
                    # row by source_id and refresh status/times/latency in place.
                    # Tenant scoping: str(source.tenant_id) equals the admin JWT tenant
                    # claim for system-tenant sources, so admin sessions receive it.
                    # Fallback must be str(SYSTEM_TENANT_ID), never a literal like
                    # "default": exact-string tenant routing would drop the event.
                    # Unreachable in practice (Source.tenant_id is NOT NULL); contract:
                    # docs/dev-guide/design/data-flow.md §3.5.4.
                    await sse_service.publish_source_health_update(
                        build_source_health_update_payload(source, health, previous_status),
                        tenant_id=str(source.tenant_id) if source.tenant_id else str(SYSTEM_TENANT_ID),
                    )

                await self.adaptive_reschedule(str(source.id), health.status)

        except Exception as e:
            logger.warning(f"Failed to update health for source {source.id}: {e}")


scheduler_manager = AsyncSchedulerManager()
