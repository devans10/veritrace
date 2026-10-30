"""Builds provider implementations from settings. The only place that names concrete impls."""

from __future__ import annotations

from functools import lru_cache

from provcheck.config import Settings, get_settings
from provcheck.core.interfaces import (
    ArchiveProvider,
    BrowserCapture,
    C2PAVerifier,
    Detector,
    DomainInfoProvider,
    ELAProvider,
    EmbeddingProvider,
    ExifReader,
    FactCheckProvider,
    LLMClient,
    ObjectStore,
    Queue,
    ReverseImageProvider,
    SearchProvider,
)
from provcheck.core.providers.anthropic_llm import AnthropicLLM
from provcheck.core.providers.archive import NullArchive, WaybackArchive
from provcheck.core.providers.browser import HTTPBrowserCapture
from provcheck.core.providers.c2pa import C2PAPythonVerifier, NullC2PAVerifier
from provcheck.core.providers.detector import NullDetector
from provcheck.core.providers.ela import PillowELA
from provcheck.core.providers.exif import PillowExifReader
from provcheck.core.providers.fake import FakeEmbed, FakeLLM
from provcheck.core.providers.google_factcheck import GoogleFactCheck, NullFactCheck
from provcheck.core.providers.local_phash import LocalPHashReverseImage, NullReverseImage
from provcheck.core.providers.objectstore import MemoryObjectStore, S3ObjectStore
from provcheck.core.providers.openai_compat_llm import OpenAICompatLLM
from provcheck.core.providers.queue import InlineQueue, RQJobQueue
from provcheck.core.providers.rdap import NullDomainInfo, RDAPDomainInfo
from provcheck.core.providers.searxng import NullSearch, SearXNGSearch
from provcheck.core.providers.sources import SourceLists, load_source_lists
from provcheck.core.providers.voyage_embed import VoyageEmbed
from provcheck.core.security.ssrf import SafeFetcher, URLPolicy


def build_object_store(s: Settings) -> ObjectStore:
    if s.object_store == "memory":
        return MemoryObjectStore()
    return S3ObjectStore(
        endpoint=s.s3_endpoint,
        access_key=s.s3_access_key.get_secret_value(),
        secret_key=s.s3_secret_key.get_secret_value(),
        bucket=s.s3_bucket,
        secure=s.s3_secure,
        region=s.s3_region,
    )


def build_queue(s: Settings) -> Queue:
    if s.queue == "inline":
        from provcheck.services.worker.tasks import run_task

        return InlineQueue(runner=run_task)
    return RQJobQueue(s.redis_url)


def build_browser(s: Settings) -> BrowserCapture:
    return HTTPBrowserCapture(s.browser_url, timeout_s=s.browser_timeout_s)


def build_archive(s: Settings) -> ArchiveProvider:
    if s.archive_provider == "none":
        return NullArchive()
    return WaybackArchive(
        base_url=s.wayback_base_url,
        access_key=s.ia_s3_access_key.get_secret_value(),
        secret_key=s.ia_s3_secret_key.get_secret_value(),
    )


def build_llm(s: Settings) -> LLMClient:
    if s.llm_provider == "fake":
        return FakeLLM()
    if s.llm_provider == "openai_compat":
        return OpenAICompatLLM(
            base_url=s.llm_base_url,
            model=s.llm_model,
            api_key=s.llm_api_key.get_secret_value(),
            default_max_tokens=s.llm_max_tokens,
        )
    return AnthropicLLM(
        api_key=s.anthropic_api_key.get_secret_value(),
        model=s.anthropic_model,
        base_url=s.anthropic_base_url,
        cache_system=s.llm_prompt_cache,
        default_max_tokens=s.llm_max_tokens,
    )


def build_embedder(s: Settings) -> EmbeddingProvider:
    if s.embed_provider == "fake":
        return FakeEmbed(dim=s.embed_dim)
    if s.embed_provider == "local":
        from provcheck.core.providers.local_embed import LocalEmbed

        return LocalEmbed(model=s.embed_model, dim=s.embed_dim)
    if s.embed_provider == "openai_compat":
        from provcheck.core.providers.openai_compat_embed import OpenAICompatEmbed

        base = s.embed_base_url or s.llm_base_url
        key = s.embed_api_key.get_secret_value() or s.llm_api_key.get_secret_value()
        return OpenAICompatEmbed(base_url=base, model=s.embed_model, dim=s.embed_dim, api_key=key)
    return VoyageEmbed(
        api_key=s.voyage_api_key.get_secret_value(),
        model=s.embed_model,
        dim=s.embed_dim,
    )


def build_search(s: Settings) -> SearchProvider:
    if s.search_provider == "none":
        return NullSearch()
    return SearXNGSearch(base_url=s.searxng_url)


def build_factcheck(s: Settings) -> FactCheckProvider:
    if s.factcheck_provider == "none":
        return NullFactCheck()
    return GoogleFactCheck(
        api_key=s.google_api_key.get_secret_value(), base_url=s.factcheck_base_url
    )


def build_domaininfo(s: Settings) -> DomainInfoProvider:
    if s.domaininfo_provider == "none":
        return NullDomainInfo()
    return RDAPDomainInfo(bootstrap_url=s.rdap_bootstrap_url)


def build_reverse_image(s: Settings) -> ReverseImageProvider:
    if s.reverse_image_provider == "none":
        return NullReverseImage()
    return LocalPHashReverseImage(similarity_threshold=s.phash_similarity_threshold)


def build_sources(s: Settings) -> SourceLists:
    return load_source_lists(s.sources_dir)


def build_exif(_s: Settings) -> ExifReader:
    return PillowExifReader()


def build_c2pa(_s: Settings) -> C2PAVerifier:
    try:
        return C2PAPythonVerifier()
    except Exception:
        return NullC2PAVerifier()


def build_ela(s: Settings) -> ELAProvider:
    return PillowELA(jpeg_quality=s.ela_jpeg_quality)


def build_detector(_s: Settings) -> Detector:
    return NullDetector()


def build_fetcher(s: Settings) -> SafeFetcher:
    return SafeFetcher(
        URLPolicy.from_settings(s),
        proxy_url=s.egress_proxy_url,
        timeout_s=s.fetch_timeout_s,
        max_bytes=s.fetch_max_bytes,
        max_redirects=s.fetch_max_redirects,
    )


class Providers:
    """Lazily-built provider set; tests replace attributes with fakes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, object] = {}

    def _get(self, key: str, factory: object) -> object:
        if key not in self._cache:
            self._cache[key] = factory(self.settings)  # type: ignore[operator]
        return self._cache[key]

    @property
    def object_store(self) -> ObjectStore:
        return self._get("object_store", build_object_store)  # type: ignore[return-value]

    @property
    def queue(self) -> Queue:
        return self._get("queue", build_queue)  # type: ignore[return-value]

    @property
    def browser(self) -> BrowserCapture:
        return self._get("browser", build_browser)  # type: ignore[return-value]

    @property
    def archive(self) -> ArchiveProvider:
        return self._get("archive", build_archive)  # type: ignore[return-value]

    @property
    def llm(self) -> LLMClient:
        return self._get("llm", build_llm)  # type: ignore[return-value]

    @property
    def embedder(self) -> EmbeddingProvider:
        return self._get("embedder", build_embedder)  # type: ignore[return-value]

    @property
    def search(self) -> SearchProvider:
        return self._get("search", build_search)  # type: ignore[return-value]

    @property
    def factcheck(self) -> FactCheckProvider:
        return self._get("factcheck", build_factcheck)  # type: ignore[return-value]

    @property
    def domaininfo(self) -> DomainInfoProvider:
        return self._get("domaininfo", build_domaininfo)  # type: ignore[return-value]

    @property
    def reverse_image(self) -> ReverseImageProvider:
        return self._get("reverse_image", build_reverse_image)  # type: ignore[return-value]

    @property
    def sources(self) -> SourceLists:
        return self._get("sources", build_sources)  # type: ignore[return-value]

    @property
    def exif(self) -> ExifReader:
        return self._get("exif", build_exif)  # type: ignore[return-value]

    @property
    def c2pa(self) -> C2PAVerifier:
        return self._get("c2pa", build_c2pa)  # type: ignore[return-value]

    @property
    def ela(self) -> ELAProvider:
        return self._get("ela", build_ela)  # type: ignore[return-value]

    @property
    def detector(self) -> Detector:
        return self._get("detector", build_detector)  # type: ignore[return-value]

    @property
    def fetcher(self) -> SafeFetcher:
        return self._get("fetcher", build_fetcher)  # type: ignore[return-value]

    def override(self, **impls: object) -> None:
        self._cache.update(impls)


@lru_cache(maxsize=1)
def get_providers() -> Providers:
    return Providers(get_settings())
