from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from time import monotonic
from typing import TYPE_CHECKING

from atnos.providers.base import ProviderHealthCheck

if TYPE_CHECKING:
    from atnos.core.config import Settings
    from atnos.db.models.provider import Provider
    from atnos.providers.base import ProviderDescriptor
    from atnos.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CachedProviderHealth:
    provider_key: str
    health: ProviderHealthCheck
    checked_at_monotonic: float

    def is_fresh(self, *, ttl_s: int) -> bool:
        return monotonic() - self.checked_at_monotonic <= ttl_s


class ProviderHealthCacheService:
    _cache: dict[str, CachedProviderHealth] = {}
    _lock = threading.Lock()

    def __init__(self, *, ttl_s: int = 30) -> None:
        self.ttl_s = max(1, int(ttl_s))

    def get_health(
        self,
        *,
        provider: "Provider",
        adapter: "ProviderDescriptor",
        timeout_s: float | None = None,
        force_refresh: bool = False,
    ) -> ProviderHealthCheck:
        if not force_refresh:
            with self._lock:
                cached = self._cache.get(provider.key)
            if cached is not None and cached.is_fresh(ttl_s=self.ttl_s):
                return cached.health

        health = self._check_health(provider=provider, adapter=adapter, timeout_s=timeout_s)
        with self._lock:
            self._cache[provider.key] = CachedProviderHealth(
                provider_key=provider.key,
                health=health,
                checked_at_monotonic=monotonic(),
            )
        return health

    def is_healthy(
        self,
        *,
        provider: "Provider",
        adapter: "ProviderDescriptor",
        timeout_s: float | None = None,
    ) -> bool:
        readiness = adapter.configuration_status(provider=provider)
        if not readiness.ready:
            return False

        health = self.get_health(provider=provider, adapter=adapter, timeout_s=timeout_s)
        if health.status == "healthy":
            return True
        if health.status == "not_supported":
            return True
        if health.status == "unknown":
            refreshed = self.get_health(
                provider=provider,
                adapter=adapter,
                timeout_s=timeout_s,
                force_refresh=True,
            )
            return refreshed.status in {"healthy", "not_supported"}
        return False

    def refresh_many(
        self,
        *,
        providers: list["Provider"],
        provider_registry: "ProviderRegistry",
        timeout_s: float | None = None,
    ) -> None:
        for provider in providers:
            adapter = provider_registry.get(provider.key)
            if adapter is None:
                continue
            self.get_health(
                provider=provider,
                adapter=adapter,
                timeout_s=timeout_s,
                force_refresh=True,
            )

    @staticmethod
    def _check_health(
        *,
        provider: "Provider",
        adapter: "ProviderDescriptor",
        timeout_s: float | None,
    ) -> ProviderHealthCheck:
        timeout_attr = "health_timeout_s"
        previous_timeout = getattr(adapter, timeout_attr, None)
        timeout_overridden = timeout_s is not None and previous_timeout is not None
        if timeout_overridden:
            setattr(adapter, timeout_attr, float(timeout_s))
        try:
            return adapter.check_health(provider=provider)
        except Exception as exc:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=f"Provider health check failed: {exc}",
            )
        finally:
            if timeout_overridden:
                setattr(adapter, timeout_attr, previous_timeout)


class ProviderHealthMonitorService:
    def __init__(
        self,
        database,
        *,
        settings: "Settings",
        provider_registry: "ProviderRegistry",
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider_registry = provider_registry
        self.cache = ProviderHealthCacheService(ttl_s=settings.provider_health_cache_ttl_s)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="atnos-provider-health-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        initial_delay_s = min(5, max(1, self.settings.provider_health_cache_ttl_s))
        if self._stop_event.wait(initial_delay_s):
            return
        while not self._stop_event.is_set():
            try:
                from atnos.repositories.provider_repository import ProviderRepository

                with self.database.session_scope() as session:
                    providers = ProviderRepository(session).list_enabled()
                    self.cache.refresh_many(
                        providers=providers,
                        provider_registry=self.provider_registry,
                    )
            except Exception as exc:
                logger.debug("Provider health monitor refresh failed: %s", exc)
            self._stop_event.wait(max(1, self.settings.provider_health_cache_ttl_s))
