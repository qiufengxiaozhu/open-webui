import logging
import os
import re
from typing import Optional

import requests

from open_webui.models.users import UserModel
from open_webui.models.files import Files
from open_webui.models.chats import Chats
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.misc import get_message_list

from open_webui.retrieval.web.utils import get_web_loader

try:
    from open_webui.retrieval.loaders.youtube import YoutubeLoader
except ImportError:
    YoutubeLoader = None

from open_webui.env import AIOHTTP_CLIENT_ALLOW_REDIRECTS

log = logging.getLogger(__name__)


def is_youtube_url(url: str) -> bool:
    youtube_regex = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$'
    return re.match(youtube_regex, url) is not None


def get_loader(request, url: str):
    if is_youtube_url(url):
        if YoutubeLoader is None:
            raise ValueError(
                'YouTube loader is not available. Install the youtube loader module to use YouTube URLs.'
            )
        return YoutubeLoader(
            url,
            language=request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            proxy_url=request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
        )
    else:
        return get_web_loader(
            url,
            verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            requests_per_second=request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS,
            trust_env=request.app.state.config.WEB_SEARCH_TRUST_ENV,
        )


def build_loader_from_config(request):
    """Build a Loader instance with the admin's configured extraction engine settings."""
    from open_webui.retrieval.loaders.main import Loader

    config = request.app.state.config
    return Loader(
        engine=config.CONTENT_EXTRACTION_ENGINE,
        DATALAB_MARKER_API_KEY=config.DATALAB_MARKER_API_KEY,
        DATALAB_MARKER_API_BASE_URL=config.DATALAB_MARKER_API_BASE_URL,
        DATALAB_MARKER_ADDITIONAL_CONFIG=config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        DATALAB_MARKER_SKIP_CACHE=config.DATALAB_MARKER_SKIP_CACHE,
        DATALAB_MARKER_FORCE_OCR=config.DATALAB_MARKER_FORCE_OCR,
        DATALAB_MARKER_PAGINATE=config.DATALAB_MARKER_PAGINATE,
        DATALAB_MARKER_STRIP_EXISTING_OCR=config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        DATALAB_MARKER_FORMAT_LINES=config.DATALAB_MARKER_FORMAT_LINES,
        DATALAB_MARKER_USE_LLM=config.DATALAB_MARKER_USE_LLM,
        DATALAB_MARKER_OUTPUT_FORMAT=config.DATALAB_MARKER_OUTPUT_FORMAT,
        EXTERNAL_DOCUMENT_LOADER_URL=config.EXTERNAL_DOCUMENT_LOADER_URL,
        EXTERNAL_DOCUMENT_LOADER_API_KEY=config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        TIKA_SERVER_URL=config.TIKA_SERVER_URL,
        DOCLING_SERVER_URL=config.DOCLING_SERVER_URL,
        DOCLING_API_KEY=config.DOCLING_API_KEY,
        DOCLING_PARAMS=config.DOCLING_PARAMS,
        PDF_EXTRACT_IMAGES=config.PDF_EXTRACT_IMAGES,
        PDF_LOADER_MODE=config.PDF_LOADER_MODE,
        DOCUMENT_INTELLIGENCE_ENDPOINT=config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        DOCUMENT_INTELLIGENCE_KEY=config.DOCUMENT_INTELLIGENCE_KEY,
        DOCUMENT_INTELLIGENCE_MODEL=config.DOCUMENT_INTELLIGENCE_MODEL,
        MISTRAL_OCR_API_BASE_URL=config.MISTRAL_OCR_API_BASE_URL,
        MISTRAL_OCR_API_KEY=config.MISTRAL_OCR_API_KEY,
        PADDLEOCR_VL_BASE_URL=config.PADDLEOCR_VL_BASE_URL,
        PADDLEOCR_VL_TOKEN=config.PADDLEOCR_VL_TOKEN,
        MINERU_API_MODE=config.MINERU_API_MODE,
        MINERU_API_URL=config.MINERU_API_URL,
        MINERU_API_KEY=config.MINERU_API_KEY,
        MINERU_API_TIMEOUT=config.MINERU_API_TIMEOUT,
        MINERU_PARAMS=config.MINERU_PARAMS,
    )


def _extract_text_from_binary_response(request, response: requests.Response, url: str) -> tuple[str, list]:
    """Download response body to a temp file and extract text using the Loader pipeline."""
    import mimetypes
    import tempfile
    import urllib.parse

    content_type = response.headers.get('Content-Type', '').split(';')[0].strip()

    # Derive filename from URL path, falling back to Content-Disposition or mime guess
    url_path = urllib.parse.urlparse(url).path
    filename = os.path.basename(url_path) if url_path else ''

    if not filename or '.' not in filename:
        # Try Content-Disposition header
        cd = response.headers.get('Content-Disposition', '')
        if 'filename=' in cd:
            filename = cd.split('filename=')[-1].strip('"\'')

    if not filename or '.' not in filename:
        ext = mimetypes.guess_extension(content_type) or ''
        filename = f'download{ext}'

    suffix = '.' + filename.split('.')[-1].lower() if '.' in filename else ''

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        loader = build_loader_from_config(request)
        docs = loader.load(filename, content_type, tmp_path)
        for doc in docs:
            doc.metadata['source'] = url
        content = ' '.join([doc.page_content for doc in docs])
        return content, docs
    finally:
        os.remove(tmp_path)


def _is_text_content_type(content_type: str) -> bool:
    """Return True if the content type should be handled by the web loader."""
    ct = content_type.split(';')[0].strip().lower()
    if ct.startswith('text/'):
        return True
    if any(t in ct for t in ['xml', 'json', 'javascript']):
        return True
    return not ct  # empty / missing → assume HTML


def get_content_from_url(request, url: str) -> str:
    from open_webui.retrieval.web.utils import validate_url

    # Validate URL before making any request (blocks private IPs, non-HTTP, filter list)
    validate_url(url)

    # Streamed GET to check Content-Type without downloading the body.
    # allow_redirects=False prevents redirect-based SSRF: validate_url() above is
    # called on the originally-submitted URL only; following 3xx redirects without
    # re-validation would let an attacker reach private IPs (RFC1918, loopback,
    # cloud-metadata 169.254.169.254) via a public host that redirects internally.
    try:
        response = requests.get(url, stream=True, timeout=30, allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
    except Exception:
        content_type = ''
        response = None

    # Text / HTML / unknown — use the configured web loader
    if response is None or _is_text_content_type(content_type):
        if response is not None:
            response.close()
        loader = get_loader(request, url)
        docs = loader.load()
        content = ' '.join([doc.page_content for doc in docs])
        return content, docs

    # Binary content (PDF, DOCX, XLSX, PPTX, etc.) — download and extract
    try:
        return _extract_text_from_binary_response(request, response, url)
    finally:
        response.close()


async def _filter_accessible_collection_names(
    collection_names: set[str],
    user: UserModel,
    access_type: str = 'read',
) -> set[str]:
    """Simple collection access checks without vector DB dependencies."""
    if user.role == 'admin':
        return collection_names

    validated = set()
    for name in collection_names:
        if name == 'knowledge-bases':
            continue
        elif name.startswith('file-'):
            file_id = name[len('file-') :]
            if await has_access_to_file(file_id=file_id, access_type=access_type, user=user):
                validated.add(name)
        elif name.startswith('user-memory-'):
            if name == f'user-memory-{user.id}':
                validated.add(name)
        elif name.startswith('web-search-'):
            validated.add(name)
        else:
            validated.add(name)
    return validated


async def get_sources_from_items(
    request,
    items,
    queries,
    embedding_function,
    k,
    reranking_function,
    k_reranker,
    r,
    hybrid_bm25_weight,
    hybrid_search,
    full_context=False,
    user: Optional[UserModel] = None,
):
    log.debug(f'items: {items} {queries} {embedding_function} {reranking_function} {full_context}')

    extracted_collections = []
    query_results = []

    for item in items:
        query_result = None
        collection_names = []

        if item.get('type') == 'text':
            # Raw Text
            # Used during temporary chat file uploads or web page & youtube attachements

            if item.get('context') == 'full':
                if item.get('file'):
                    # if item has file data, use it
                    query_result = {
                        'documents': [[item.get('file', {}).get('data', {}).get('content')]],
                        'metadatas': [[item.get('file', {}).get('meta', {})]],
                    }

            if query_result is None:
                # Fallback
                if item.get('collection_name'):
                    # If item has a collection name, use it
                    collection_names.append(item.get('collection_name'))
                elif item.get('file'):
                    # If item has file data, use it
                    query_result = {
                        'documents': [[item.get('file', {}).get('data', {}).get('content')]],
                        'metadatas': [[item.get('file', {}).get('meta', {})]],
                    }
                else:
                    # Fallback to item content
                    query_result = {
                        'documents': [[item.get('content')]],
                        'metadatas': [[{'file_id': item.get('id'), 'name': item.get('name')}]],
                    }

        elif item.get('type') == 'chat':
            # Chat Attached
            chat = await Chats.get_chat_by_id(item.get('id'))

            if chat and (user.role == 'admin' or chat.user_id == user.id):
                messages_map = chat.chat.get('history', {}).get('messages', {})
                message_id = chat.chat.get('history', {}).get('currentId')

                if messages_map and message_id:
                    # Reconstruct the message list in order
                    message_list = get_message_list(messages_map, message_id)
                    message_history = '\n'.join(
                        [f'#### {m.get("role", "user").capitalize()}\n{m.get("content")}\n' for m in message_list]
                    )

                    # User has access to the chat
                    query_result = {
                        'documents': [[message_history]],
                        'metadatas': [[{'file_id': chat.id, 'name': chat.title}]],
                    }

        elif item.get('type') == 'url':
            content, docs = get_content_from_url(request, item.get('url'))
            if docs:
                query_result = {
                    'documents': [[content]],
                    'metadatas': [[{'url': item.get('url'), 'name': item.get('url')}]],
                }
        elif item.get('type') == 'file':
            if item.get('context') == 'full' or request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL:
                if item.get('file', {}).get('data', {}).get('content', ''):
                    # Manual Full Mode Toggle
                    # Used from chat file modal, we can assume that the file content will be available from item.get("file").get("data", {}).get("content")
                    query_result = {
                        'documents': [[item.get('file', {}).get('data', {}).get('content', '')]],
                        'metadatas': [
                            [
                                {
                                    'file_id': item.get('id'),
                                    'name': item.get('name'),
                                    **item.get('file').get('data', {}).get('metadata', {}),
                                }
                            ]
                        ],
                    }
                elif item.get('id'):
                    file_object = await Files.get_file_by_id(item.get('id'))
                    if file_object and (
                        user.role == 'admin'
                        or file_object.user_id == user.id
                        or await has_access_to_file(item.get('id'), 'read', user)
                    ):
                        query_result = {
                            'documents': [[file_object.data.get('content', '')]],
                            'metadatas': [
                                [
                                    {
                                        'file_id': item.get('id'),
                                        'name': file_object.filename,
                                        'source': file_object.filename,
                                    }
                                ]
                            ],
                        }
            else:
                # Fallback to collection names
                if item.get('legacy'):
                    collection_names.append(f'{item["id"]}')
                else:
                    collection_names.append(f'file-{item["id"]}')

        elif item.get('type') == 'collection':
            if item.get('legacy'):
                collection_names.extend(item.get('collection_names', []))
            else:
                collection_names.append(item['id'])

        elif item.get('docs'):
            # BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
            query_result = {
                'documents': [[doc.get('content') for doc in item.get('docs')]],
                'metadatas': [[doc.get('metadata') for doc in item.get('docs')]],
            }
        elif item.get('collection_name'):
            # Direct Collection Name
            collection_names.append(item['collection_name'])
        elif item.get('collection_names'):
            # Collection Names List
            collection_names.extend(item['collection_names'])

        # Vector search fallback is unavailable in the cropped retrieval module.
        if query_result is None and collection_names:
            collection_names = set(collection_names).difference(extracted_collections)
            if not collection_names:
                log.debug(f'skipping {item} as it has already been extracted')
                continue

            if user:
                collection_names = await _filter_accessible_collection_names(collection_names, user)
                if not collection_names:
                    log.debug(f'access denied for all collections in item {item}')
                    continue

            log.debug(
                'vector search unavailable; skipping collections %s for item %s',
                collection_names,
                item,
            )
            extracted_collections.extend(collection_names)
            continue

        if query_result:
            if 'data' in item:
                del item['data']
            query_results.append({**query_result, 'file': item})

    sources = []
    for query_result in query_results:
        try:
            if 'documents' in query_result:
                if 'metadatas' in query_result:
                    source = {
                        'source': query_result['file'],
                        'document': query_result['documents'][0],
                        'metadata': query_result['metadatas'][0],
                    }
                    if 'distances' in query_result and query_result['distances']:
                        source['distances'] = query_result['distances'][0]

                    sources.append(source)
        except Exception as e:
            log.exception(e)
    return sources
