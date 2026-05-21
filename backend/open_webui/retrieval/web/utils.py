import asyncio
import ipaddress
import logging
import socket
import ssl
import urllib.parse
import urllib.request

from datetime import datetime, time, timedelta
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Union,
)

from fastapi.concurrency import run_in_threadpool
import aiohttp
import certifi
import validators
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.document_loaders.base import BaseLoader
from langchain_core.documents import Document

try:
    from langchain_community.document_loaders import PlaywrightURLLoader
except ImportError:
    PlaywrightURLLoader = None

try:
    from open_webui.retrieval.loaders.tavily import TavilyLoader
except ImportError:
    TavilyLoader = None

try:
    from open_webui.retrieval.loaders.external_web import ExternalWebLoader
except ImportError:
    ExternalWebLoader = None

try:
    from open_webui.retrieval.web.firecrawl import scrape_firecrawl_url
except ImportError:
    scrape_firecrawl_url = None

from open_webui.constants import ERROR_MESSAGES
from open_webui.config import (
    ENABLE_RAG_LOCAL_WEB_FETCH,
    WEB_LOADER_ENGINE,
    WEB_LOADER_TIMEOUT,
    FIRECRAWL_API_BASE_URL,
    FIRECRAWL_API_KEY,
    FIRECRAWL_TIMEOUT,
    TAVILY_API_KEY,
    TAVILY_EXTRACT_DEPTH,
    EXTERNAL_WEB_LOADER_URL,
    EXTERNAL_WEB_LOADER_API_KEY,
    WEB_FETCH_FILTER_LIST,
)
from open_webui.utils.misc import is_string_allowed
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_ALLOW_REDIRECTS

log = logging.getLogger(__name__)


def resolve_hostname(hostname):
    # Get address information
    addr_info = socket.getaddrinfo(hostname, None)

    # Extract IP addresses from address information
    ipv4_addresses = [info[4][0] for info in addr_info if info[0] == socket.AF_INET]
    ipv6_addresses = [info[4][0] for info in addr_info if info[0] == socket.AF_INET6]

    return ipv4_addresses, ipv6_addresses


def validate_url(url: Union[str, Sequence[str]]):
    if isinstance(url, str):
        if isinstance(validators.url(url), validators.ValidationError):
            raise ValueError(ERROR_MESSAGES.INVALID_URL)

        # Reject parser-confusing chars: urlparse and requests/aiohttp split
        # on these differently, e.g. http://127.0.0.1\@1.1.1.1 → urlparse
        # extracts 1.1.1.1 (public, passes filter) while requests connects
        # to 127.0.0.1 (internal). Same shape with tab/CR/LF.
        if any(ch in url for ch in ('\\', '\t', '\n', '\r')):
            log.warning(f'Blocked URL with parser-confusing char: {url!r}')
            raise ValueError(ERROR_MESSAGES.INVALID_URL)

        parsed_url = urllib.parse.urlparse(url)

        # Protocol validation - only allow http/https
        if parsed_url.scheme not in ['http', 'https']:
            log.warning(f'Blocked non-HTTP(S) protocol: {parsed_url.scheme} in URL: {url}')
            raise ValueError(ERROR_MESSAGES.INVALID_URL)

        # Blocklist check using unified filtering logic
        if WEB_FETCH_FILTER_LIST:
            if not is_string_allowed(url, WEB_FETCH_FILTER_LIST):
                log.warning(f'URL blocked by filter list: {url}')
                raise ValueError(ERROR_MESSAGES.INVALID_URL)

        if not ENABLE_RAG_LOCAL_WEB_FETCH:
            # Local web fetch is disabled, filter out any URLs that resolve to private IP addresses
            parsed_url = urllib.parse.urlparse(url)
            # Get IPv4 and IPv6 addresses
            ipv4_addresses, ipv6_addresses = resolve_hostname(parsed_url.hostname)
            # Check if any of the resolved addresses are private
            # This is technically still vulnerable to DNS rebinding attacks, as we don't control WebBaseLoader
            for ip in ipv4_addresses + ipv6_addresses:
                addr = ipaddress.ip_address(ip)
                if not addr.is_global:
                    raise ValueError(ERROR_MESSAGES.INVALID_URL)
        return True
    elif isinstance(url, Sequence):
        return all(validate_url(u) for u in url)
    else:
        return False


def safe_validate_urls(url: Sequence[str]) -> Sequence[str]:
    valid_urls = []
    for u in url:
        try:
            if validate_url(u):
                valid_urls.append(u)
        except Exception as e:
            log.debug(f'Invalid URL {u}: {str(e)}')
            continue
    return valid_urls


def extract_metadata(soup, url):
    metadata = {'source': url}
    if title := soup.find('title'):
        metadata['title'] = title.get_text()
    if description := soup.find('meta', attrs={'name': 'description'}):
        metadata['description'] = description.get('content', 'No description found.')
    if html := soup.find('html'):
        metadata['language'] = html.get('lang', 'No language found.')
    return metadata


def verify_ssl_cert(url: str) -> bool:
    """Verify SSL certificate for the given URL."""
    if not url.startswith('https://'):
        return True

    try:
        hostname = url.split('://')[-1].split('/')[0]
        context = ssl.create_default_context(cafile=certifi.where())
        with context.wrap_socket(ssl.socket(), server_hostname=hostname) as s:
            s.connect((hostname, 443))
        return True
    except ssl.SSLError:
        return False
    except Exception as e:
        log.warning(f'SSL verification failed for {url}: {str(e)}')
        return False


class RateLimitMixin:
    async def _wait_for_rate_limit(self):
        """Wait to respect the rate limit if specified."""
        if self.requests_per_second and self.last_request_time:
            min_interval = timedelta(seconds=1.0 / self.requests_per_second)
            time_since_last = datetime.now() - self.last_request_time
            if time_since_last < min_interval:
                await asyncio.sleep((min_interval - time_since_last).total_seconds())
        self.last_request_time = datetime.now()

    def _sync_wait_for_rate_limit(self):
        """Synchronous version of rate limit wait."""
        if self.requests_per_second and self.last_request_time:
            min_interval = timedelta(seconds=1.0 / self.requests_per_second)
            time_since_last = datetime.now() - self.last_request_time
            if time_since_last < min_interval:
                time.sleep((min_interval - time_since_last).total_seconds())
        self.last_request_time = datetime.now()


class URLProcessingMixin:
    async def _verify_ssl_cert(self, url: str) -> bool:
        """Verify SSL certificate for a URL."""
        return await run_in_threadpool(verify_ssl_cert, url)

    async def _safe_process_url(self, url: str) -> bool:
        """Perform safety checks before processing a URL."""
        if self.verify_ssl and not await self._verify_ssl_cert(url):
            raise ValueError(f'SSL certificate verification failed for {url}')
        await self._wait_for_rate_limit()
        return True

    def _safe_process_url_sync(self, url: str) -> bool:
        """Synchronous version of safety checks."""
        if self.verify_ssl and not verify_ssl_cert(url):
            raise ValueError(f'SSL certificate verification failed for {url}')
        self._sync_wait_for_rate_limit()
        return True


class SafeWebBaseLoader(WebBaseLoader):
    """WebBaseLoader with enhanced error handling for URLs."""

    def __init__(self, trust_env: bool = False, *args, **kwargs):
        """Initialize SafeWebBaseLoader
        Args:
            trust_env (bool, optional): set to True if using proxy to make web requests, for example
                using http(s)_proxy environment variables. Defaults to False.
        """
        super().__init__(*args, **kwargs)
        self.trust_env = trust_env
        # Prevent redirect-based SSRF on the synchronous _scrape() path.
        # validate_url() is called once on the originally-submitted URL, but the
        # parent WebBaseLoader's _scrape() invokes self.session.get(url, **self.requests_kwargs)
        # which by default follows redirects. Without the override below, an attacker
        # can submit a public URL that 302-redirects to an internal address (RFC1918,
        # 127.0.0.1, 169.254.169.254, etc.) and the redirected target is fetched without
        # re-validation. Matches the policy enforced on the async _fetch() path below.
        self.requests_kwargs = {
            **(self.requests_kwargs or {}),
            'allow_redirects': AIOHTTP_CLIENT_ALLOW_REDIRECTS,
        }

    async def _fetch(self, url: str, retries: int = 3, cooldown: int = 2, backoff: float = 1.5) -> str:
        async with aiohttp.ClientSession(trust_env=self.trust_env) as session:
            for i in range(retries):
                try:
                    kwargs: Dict = dict(
                        headers=self.session.headers,
                        cookies=self.session.cookies.get_dict(),
                    )
                    if not self.session.verify:
                        kwargs['ssl'] = False
                    else:
                        kwargs['ssl'] = AIOHTTP_CLIENT_SESSION_SSL

                    async with session.get(
                        url,
                        **(self.requests_kwargs | kwargs),
                        allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS,
                    ) as response:
                        if self.raise_for_status:
                            response.raise_for_status()
                        return await response.text()
                except aiohttp.ClientConnectionError as e:
                    if i == retries - 1:
                        raise
                    else:
                        log.warning(f'Error fetching {url} with attempt {i + 1}/{retries}: {e}. Retrying...')
                        await asyncio.sleep(cooldown * backoff**i)
        raise ValueError('retry count exceeded')

    def _unpack_fetch_results(self, results: Any, urls: List[str], parser: Union[str, None] = None) -> List[Any]:
        """Unpack fetch results into BeautifulSoup objects."""
        from bs4 import BeautifulSoup

        final_results = []
        for i, result in enumerate(results):
            url = urls[i]
            if parser is None:
                if url.endswith('.xml'):
                    parser = 'xml'
                else:
                    parser = self.default_parser
                self._check_parser(parser)
            final_results.append(BeautifulSoup(result, parser, **self.bs_kwargs))
        return final_results

    async def ascrape_all(self, urls: List[str], parser: Union[str, None] = None) -> List[Any]:
        """Async fetch all urls, then return soups for all results."""
        results = await self.fetch_all(urls)
        return self._unpack_fetch_results(results, urls, parser=parser)

    def lazy_load(self) -> Iterator[Document]:
        """Lazy load text from the url(s) in web_path with error handling."""
        for path in self.web_paths:
            try:
                soup = self._scrape(path, bs_kwargs=self.bs_kwargs)
                text = soup.get_text(**self.bs_get_text_kwargs)

                # Build metadata
                metadata = extract_metadata(soup, path)

                yield Document(page_content=text, metadata=metadata)
            except Exception as e:
                # Log the error and continue with the next URL
                log.exception(f'Error loading {path}: {e}')

    async def alazy_load(self) -> AsyncIterator[Document]:
        """Async lazy load text from the url(s) in web_path."""
        results = await self.ascrape_all(self.web_paths)
        for path, soup in zip(self.web_paths, results):
            text = soup.get_text(**self.bs_get_text_kwargs)
            metadata = {'source': path}
            if title := soup.find('title'):
                metadata['title'] = title.get_text()
            if description := soup.find('meta', attrs={'name': 'description'}):
                metadata['description'] = description.get('content', 'No description found.')
            if html := soup.find('html'):
                metadata['language'] = html.get('lang', 'No language found.')
            yield Document(page_content=text, metadata=metadata)

    async def aload(self) -> list[Document]:
        """Load data into Document objects."""
        return [document async for document in self.alazy_load()]


class _FirecrawlWebLoader(BaseLoader):
    def __init__(
        self,
        web_paths,
        api_key,
        api_url,
        verify_ssl=True,
        timeout=None,
        params=None,
        continue_on_failure=True,
    ):
        self.web_paths = web_paths if isinstance(web_paths, list) else [web_paths]
        self.api_key = api_key
        self.api_url = (api_url or 'https://api.firecrawl.dev').rstrip('/')
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.params = params or {}
        self.continue_on_failure = continue_on_failure

    def lazy_load(self) -> Iterator[Document]:
        for url in self.web_paths:
            try:
                doc = scrape_firecrawl_url(
                    self.api_url,
                    self.api_key,
                    url,
                    verify_ssl=self.verify_ssl,
                    timeout=self.timeout,
                    params=self.params,
                )
                if doc is not None:
                    yield doc
            except Exception as e:
                if self.continue_on_failure:
                    log.warning(f'Error extracting content from URL with Firecrawl: {e}')
                else:
                    raise


def get_web_loader(
    urls: Union[str, Sequence[str]],
    verify_ssl: bool = True,
    requests_per_second: int = 2,
    trust_env: bool = False,
):
    # Check if the URLs are valid
    safe_urls = safe_validate_urls([urls] if isinstance(urls, str) else urls)

    if not safe_urls:
        log.warning(f'All provided URLs were blocked or invalid: {urls}')
        raise ValueError(ERROR_MESSAGES.INVALID_URL)

    engine = WEB_LOADER_ENGINE.value
    web_loader_args = {
        'web_paths': safe_urls,
        'verify_ssl': verify_ssl,
        'requests_per_second': requests_per_second,
        'continue_on_failure': True,
        'trust_env': trust_env,
    }

    if engine == '' or engine == 'safe_web':
        request_kwargs = {}
        if WEB_LOADER_TIMEOUT.value:
            try:
                timeout_value = float(WEB_LOADER_TIMEOUT.value)
            except ValueError:
                timeout_value = None

            if timeout_value:
                request_kwargs['timeout'] = timeout_value

        if request_kwargs:
            web_loader_args['requests_kwargs'] = request_kwargs

        web_loader = SafeWebBaseLoader(**web_loader_args)

    elif engine == 'playwright':
        if PlaywrightURLLoader is None:
            raise ValueError(
                "Playwright web loader engine is not available. "
                'Install playwright and langchain-community to use this engine.'
            )
        web_loader = PlaywrightURLLoader(
            urls=safe_urls,
            continue_on_failure=True,
        )

    elif engine == 'firecrawl':
        if scrape_firecrawl_url is None:
            raise ValueError(
                "Firecrawl web loader engine is not available. "
                'Install the firecrawl loader module to use this engine.'
            )
        timeout = None
        if FIRECRAWL_TIMEOUT.value:
            try:
                timeout = int(FIRECRAWL_TIMEOUT.value)
            except ValueError:
                pass
        web_loader = _FirecrawlWebLoader(
            web_paths=safe_urls,
            api_key=FIRECRAWL_API_KEY.value,
            api_url=FIRECRAWL_API_BASE_URL.value,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )

    elif engine == 'tavily':
        if TavilyLoader is None:
            raise ValueError(
                "Tavily web loader engine is not available. "
                'Install the tavily loader module to use this engine.'
            )
        web_loader = TavilyLoader(
            urls=safe_urls,
            api_key=TAVILY_API_KEY.value,
            extract_depth=TAVILY_EXTRACT_DEPTH.value,
            continue_on_failure=True,
        )

    elif engine == 'external':
        if ExternalWebLoader is None:
            raise ValueError(
                "External web loader engine is not available. "
                'Install the external_web loader module to use this engine.'
            )
        web_loader = ExternalWebLoader(
            web_paths=safe_urls,
            external_url=EXTERNAL_WEB_LOADER_URL.value,
            external_api_key=EXTERNAL_WEB_LOADER_API_KEY.value,
        )

    else:
        raise ValueError(
            f'Invalid WEB_LOADER_ENGINE: {engine}. '
            "Please set it to 'safe_web', 'playwright', 'firecrawl', 'tavily', or 'external'."
        )

    log.debug(
        'Using WEB_LOADER_ENGINE %s for %s URLs',
        web_loader.__class__.__name__,
        len(safe_urls),
    )

    return web_loader
