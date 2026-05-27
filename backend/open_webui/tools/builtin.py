"""
Built-in tools for Open WebUI.

These tools are automatically available when native function calling is enabled.

IMPORTANT: DO NOT IMPORT THIS MODULE DIRECTLY IN OTHER PARTS OF THE CODEBASE.
"""

import hashlib
import json
import logging
import os
import re
import time
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import Request

from open_webui.models.users import UserModel
from open_webui.models.chats import Chats
from open_webui.models.messages import Messages, Message
from open_webui.models.groups import Groups

log = logging.getLogger(__name__)


# =============================================================================
# TIME UTILITIES
# =============================================================================


async def get_current_timestamp(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp in seconds.

    :return: JSON with current_timestamp (seconds), current_iso (UTC ISO format), and user_local_iso (user's local time)
    """
    try:
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(datetime.timezone.utc)
        result = {
            'current_timestamp': int(now.timestamp()),
            'current_iso': now.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                user_tz = ZoneInfo(tz_name)
                user_now = now.astimezone(user_tz)
                result['user_local_iso'] = user_now.isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'get_current_timestamp error: {e}')
        return json.dumps({'error': str(e)})


async def calculate_timestamp(
    days_ago: int = 0,
    weeks_ago: int = 0,
    months_ago: int = 0,
    years_ago: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp, optionally adjusted by days, weeks, months, or years.
    Use this to calculate timestamps for date filtering in search functions.
    Examples: "last week" = weeks_ago=1, "3 days ago" = days_ago=3, "a year ago" = years_ago=1

    :param days_ago: Number of days to subtract from current time (default: 0)
    :param weeks_ago: Number of weeks to subtract from current time (default: 0)
    :param months_ago: Number of months to subtract from current time (default: 0)
    :param years_ago: Number of years to subtract from current time (default: 0)
    :return: JSON with current_timestamp and calculated_timestamp (both in seconds)
    """
    try:
        import datetime
        from dateutil.relativedelta import relativedelta

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())

        # Calculate the adjusted time
        total_days = days_ago + (weeks_ago * 7)
        adjusted = now - datetime.timedelta(days=total_days)

        # Handle months and years separately (variable length)
        if months_ago > 0 or years_ago > 0:
            adjusted = adjusted - relativedelta(months=months_ago, years=years_ago)

        adjusted_ts = int(adjusted.timestamp())

        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return json.dumps(result, ensure_ascii=False)
    except ImportError:
        # Fallback without dateutil
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())
        total_days = days_ago + (weeks_ago * 7) + (months_ago * 30) + (years_ago * 365)
        adjusted = now - datetime.timedelta(days=total_days)
        adjusted_ts = int(adjusted.timestamp())
        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'calculate_timestamp error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# WEB SEARCH TOOLS
# =============================================================================


async def search_web(
    query: str,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the public web for information. Best for current events, external references,
    or topics not covered in internal documents.

    :param query: The search query to look up
    :param count: Number of results to return (default: admin-configured value)
    :return: JSON with search results containing title, link, and snippet for each result
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    try:
        engine = __request__.app.state.config.WEB_SEARCH_ENGINE
        user = UserModel(**__user__) if __user__ else None

        configured = __request__.app.state.config.WEB_SEARCH_RESULT_COUNT
        max_count = 5 if configured is None else configured
        count = max(1, min(count, max_count)) if count is not None else max_count

        try:
            from open_webui.routers.retrieval import search_web as _search_web
        except ImportError:
            return json.dumps({'error': 'Web search is not available'})

        results = await asyncio.to_thread(_search_web, __request__, engine, query, user)

        # Limit results
        results = results[:count] if results else []

        return json.dumps(
            [{'title': r.title, 'link': r.link, 'snippet': r.snippet} for r in results],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_web error: {e}')
        return json.dumps({'error': str(e)})


async def fetch_url(
    url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Fetch and extract the main text content from a web page URL.

    :param url: The URL to fetch content from
    :return: The extracted text content from the page
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    try:
        try:
            from open_webui.retrieval.utils import get_content_from_url
        except ImportError:
            return json.dumps({'error': 'URL fetching is not available'})

        content, _ = await asyncio.to_thread(get_content_from_url, __request__, url)

        # Truncate if configured (WEB_FETCH_MAX_CONTENT_LENGTH)
        # Guard: content may be None if the web loader silently failed
        if content is not None:
            max_length = getattr(__request__.app.state.config, 'WEB_FETCH_MAX_CONTENT_LENGTH', None)
            if max_length and max_length > 0 and len(content) > max_length:
                content = content[:max_length] + '\n\n[Content truncated...]'
        else:
            content = ''

        return content
    except Exception as e:
        log.exception(f'fetch_url error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# NOTES TOOLS
# =============================================================================


async def search_notes(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's notes by title and content.

    :param query: The search query to find matching notes
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include notes updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include notes updated before this Unix timestamp (seconds)
    :return: JSON with matching notes containing id, title, and content snippet
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id)]

        result = await Notes.search_notes(
            user_id=user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
                'permission': 'read',
            },
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        # Convert timestamps to nanoseconds for comparison
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        notes = []
        for note in result.items:
            # Apply date filters (updated_at is in nanoseconds)
            if start_ts and note.updated_at < start_ts:
                continue
            if end_ts and note.updated_at > end_ts:
                continue

            # Extract a snippet from the markdown content
            content_snippet = ''
            if note.data and note.data.get('content', {}).get('md'):
                md_content = note.data['content']['md']
                content_lower = md_content.lower()

                # Find the first matching word to center the snippet around.
                search_words = query.lower().split()
                match_pos = -1
                match_len = len(query)
                for word in search_words:
                    found_pos = content_lower.find(word)
                    if found_pos != -1:
                        match_pos = found_pos
                        match_len = len(word)
                        break

                if match_pos != -1:
                    snippet_start = max(0, match_pos - 50)
                    snippet_end = min(len(md_content), match_pos + match_len + 100)
                    content_snippet = (
                        ('...' if snippet_start > 0 else '')
                        + md_content[snippet_start:snippet_end]
                        + ('...' if snippet_end < len(md_content) else '')
                    )
                else:
                    content_snippet = md_content[:150] + ('...' if len(md_content) > 150 else '')

            notes.append(
                {
                    'id': note.id,
                    'title': note.title,
                    'snippet': content_snippet,
                    'updated_at': note.updated_at,
                }
            )

            if len(notes) >= count:
                break

        return json.dumps(notes, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_notes error: {e}')
        return json.dumps({'error': str(e)})


async def view_note(
    note_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a note by its ID.

    :param note_id: The ID of the note to retrieve
    :return: JSON with the note's id, title, and full markdown content
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        note = await Notes.get_note_by_id(note_id)

        if not note:
            return json.dumps({'error': 'Note not found'})

        # Check access permission
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id)]

        from open_webui.models.access_grants import AccessGrants

        if note.user_id != user_id and not await AccessGrants.has_access(
            user_id=user_id,
            resource_type='note',
            resource_id=note.id,
            permission='read',
            user_group_ids=set(user_group_ids),
        ):
            return json.dumps({'error': 'Access denied'})

        # Extract markdown content
        content = ''
        if note.data and note.data.get('content', {}).get('md'):
            content = note.data['content']['md']

        return json.dumps(
            {
                'id': note.id,
                'title': note.title,
                'content': content,
                'updated_at': note.updated_at,
                'created_at': note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_note error: {e}')
        return json.dumps({'error': str(e)})


async def write_note(
    title: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a new note with the given title and content.

    :param title: The title of the new note
    :param content: The markdown content for the note
    :return: JSON with success status and new note id
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteForm

        user_id = __user__.get('id')

        form = NoteForm(
            title=title,
            data={'content': {'md': content}},
            access_grants=[],  # Private by default - only owner can access
        )

        new_note = await Notes.insert_new_note(user_id, form)

        if not new_note:
            return json.dumps({'error': 'Failed to create note'})

        return json.dumps(
            {
                'status': 'success',
                'id': new_note.id,
                'title': new_note.title,
                'created_at': new_note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'write_note error: {e}')
        return json.dumps({'error': str(e)})


async def replace_note_content(
    note_id: str,
    content: str,
    title: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update the content of a note. Use this to modify task lists, add notes, or update content.

    :param note_id: The ID of the note to update
    :param content: The new markdown content for the note
    :param title: Optional new title for the note
    :return: JSON with success status and updated note info
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteUpdateForm

        note = await Notes.get_note_by_id(note_id)

        if not note:
            return json.dumps({'error': 'Note not found'})

        # Check write permission
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id)]

        from open_webui.models.access_grants import AccessGrants

        if note.user_id != user_id and not await AccessGrants.has_access(
            user_id=user_id,
            resource_type='note',
            resource_id=note.id,
            permission='write',
            user_group_ids=set(user_group_ids),
        ):
            return json.dumps({'error': 'Write access denied'})

        # Build update form
        update_data = {'data': {'content': {'md': content}}}
        if title:
            update_data['title'] = title

        form = NoteUpdateForm(**update_data)
        updated_note = await Notes.update_note_by_id(note_id, form)

        if not updated_note:
            return json.dumps({'error': 'Failed to update note'})

        return json.dumps(
            {
                'status': 'success',
                'id': updated_note.id,
                'title': updated_note.title,
                'updated_at': updated_note.updated_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'replace_note_content error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# CHATS TOOLS
# =============================================================================


async def search_chats(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search the user's previous chat conversations by title and message content.

    :param query: The search query to find matching chats
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include chats updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include chats updated before this Unix timestamp (seconds)
    :return: JSON with matching chats containing id, title, updated_at, and content snippet
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chats = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=query,
            include_archived=False,
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        results = []
        for chat in chats:
            # Skip the current chat to avoid showing it in search results
            if __chat_id__ and chat.id == __chat_id__:
                continue

            # Apply date filters (updated_at is in seconds)
            if start_timestamp and chat.updated_at < start_timestamp:
                continue
            if end_timestamp and chat.updated_at > end_timestamp:
                continue

            # Find a matching message snippet
            snippet = ''
            messages = chat.chat.get('history', {}).get('messages', {})
            lower_query = query.lower()

            for msg_id, msg in messages.items():
                content = msg.get('content', '')
                if isinstance(content, str) and lower_query in content.lower():
                    idx = content.lower().find(lower_query)
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query) + 100)
                    snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
                    break

            if not snippet and lower_query in chat.title.lower():
                snippet = f'Title match: {chat.title}'

            results.append(
                {
                    'id': chat.id,
                    'title': chat.title,
                    'snippet': snippet,
                    'updated_at': chat.updated_at,
                }
            )

            if len(results) >= count:
                break

        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_chats error: {e}')
        return json.dumps({'error': str(e)})


async def view_chat(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full conversation history of a chat by its ID.

    :param chat_id: The ID of the chat to retrieve
    :return: JSON with the chat's id, title, and messages
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

        if not chat:
            return json.dumps({'error': 'Chat not found or access denied'})

        # Extract messages from history
        messages = []
        history = chat.chat.get('history', {})
        msg_dict = history.get('messages', {})

        # Build message chain from currentId
        current_id = history.get('currentId')
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            msg = msg_dict.get(current_id)
            if msg:
                messages.append(
                    {
                        'role': msg.get('role', ''),
                        'content': msg.get('content', ''),
                    }
                )
            current_id = msg.get('parentId') if msg else None

        # Reverse to get chronological order
        messages.reverse()

        return json.dumps(
            {
                'id': chat.id,
                'title': chat.title,
                'messages': messages,
                'updated_at': chat.updated_at,
                'created_at': chat.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_chat error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# CHANNELS TOOLS
# =============================================================================


async def search_channels(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for channels by name and description that the user has access to.

    :param query: The search query to find matching channels
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with matching channels containing id, name, description, and type
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get all channels the user has access to
        all_channels = await Channels.get_channels_by_user_id(user_id)

        # Filter by query
        lower_query = query.lower()
        matching_channels = []

        for channel in all_channels:
            name_match = lower_query in channel.name.lower() if channel.name else False
            desc_match = lower_query in (channel.description or '').lower()

            if name_match or desc_match:
                matching_channels.append(
                    {
                        'id': channel.id,
                        'name': channel.name,
                        'description': channel.description or '',
                        'type': channel.type or 'public',
                    }
                )

            if len(matching_channels) >= count:
                break

        return json.dumps(matching_channels, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channels error: {e}')
        return json.dumps({'error': str(e)})


async def search_channel_messages(
    query: str,
    count: int = 10,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for messages in channels the user is a member of, including thread replies.

    :param query: The search query to find matching messages
    :param count: Maximum number of results to return (default: 10)
    :param start_timestamp: Only include messages created after this Unix timestamp (seconds)
    :param end_timestamp: Only include messages created before this Unix timestamp (seconds)
    :return: JSON with matching messages containing channel info, message content, and thread context
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get all channels the user has access to
        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]
        channel_map = {c.id: c for c in user_channels}

        if not channel_ids:
            return json.dumps([])

        # Convert timestamps to nanoseconds (Message.created_at is in nanoseconds)
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        # Search messages using the model method
        matching_messages = await Messages.search_messages_by_channel_ids(
            channel_ids=channel_ids,
            query=query,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            limit=count,
        )

        results = []
        for msg in matching_messages:
            channel = channel_map.get(msg.channel_id)

            # Extract snippet around the match
            content = msg.content or ''
            lower_query = query.lower()
            idx = content.lower().find(lower_query)
            if idx != -1:
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 100)
                snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
            else:
                snippet = content[:150] + ('...' if len(content) > 150 else '')

            results.append(
                {
                    'channel_id': msg.channel_id,
                    'channel_name': channel.name if channel else 'Unknown',
                    'message_id': msg.id,
                    'content_snippet': snippet,
                    'is_thread_reply': msg.parent_id is not None,
                    'parent_id': msg.parent_id,
                    'created_at': msg.created_at,
                }
            )

        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channel_messages error: {e}')
        return json.dumps({'error': str(e)})


async def view_channel_message(
    message_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a channel message by its ID, including thread replies.

    :param message_id: The ID of the message to retrieve
    :return: JSON with the message content, channel info, and thread replies if any
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        message = await Messages.get_message_by_id(message_id)

        if not message:
            return json.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(message.channel_id)
        if not channel:
            return json.dumps({'error': 'Channel not found'})

        # Check if user has access to the channel
        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]

        if message.channel_id not in channel_ids:
            return json.dumps({'error': 'Access denied'})

        # Build response with thread information
        result = {
            'id': message.id,
            'channel_id': message.channel_id,
            'channel_name': channel.name,
            'content': message.content,
            'user_id': message.user_id,
            'is_thread_reply': message.parent_id is not None,
            'parent_id': message.parent_id,
            'reply_count': message.reply_count,
            'created_at': message.created_at,
            'updated_at': message.updated_at,
        }

        # Include user info if available
        if message.user:
            result['user_name'] = message.user.name

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_channel_message error: {e}')
        return json.dumps({'error': str(e)})


async def view_channel_thread(
    parent_message_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get all messages in a channel thread, including the parent message and all replies.

    :param parent_message_id: The ID of the parent message that started the thread
    :return: JSON with the parent message and all thread replies in chronological order
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get the parent message
        parent_message = await Messages.get_message_by_id(parent_message_id)

        if not parent_message:
            return json.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(parent_message.channel_id)
        if not channel:
            return json.dumps({'error': 'Channel not found'})

        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]

        if parent_message.channel_id not in channel_ids:
            return json.dumps({'error': 'Access denied'})

        # Get all thread replies
        thread_replies = await Messages.get_thread_replies_by_message_id(parent_message_id)

        # Build the response
        messages = []

        # Add parent message first
        messages.append(
            {
                'id': parent_message.id,
                'content': parent_message.content,
                'user_id': parent_message.user_id,
                'user_name': parent_message.user.name if parent_message.user else None,
                'is_parent': True,
                'created_at': parent_message.created_at,
            }
        )

        # Add thread replies (reverse to get chronological order)
        for reply in reversed(thread_replies):
            messages.append(
                {
                    'id': reply.id,
                    'content': reply.content,
                    'user_id': reply.user_id,
                    'user_name': reply.user.name if reply.user else None,
                    'is_parent': False,
                    'reply_to_id': reply.reply_to_id,
                    'created_at': reply.created_at,
                }
            )

        return json.dumps(
            {
                'channel_id': parent_message.channel_id,
                'channel_name': channel.name,
                'thread_id': parent_message_id,
                'message_count': len(messages),
                'messages': messages,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_channel_thread error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# FILE TOOLS
# =============================================================================

# Hard cap for view_file output
MAX_VIEW_FILE_CHARS = 100_000
DEFAULT_VIEW_FILE_MAX_CHARS = 10_000


async def view_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = DEFAULT_VIEW_FILE_MAX_CHARS,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the content of a file by its ID. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0)
    :param max_chars: Maximum characters to return (default: 10000, hard cap: 100000)
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = DEFAULT_VIEW_FILE_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), MAX_VIEW_FILE_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.models.files import Files
        from open_webui.utils.access_control.files import has_access_to_file

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')

        file = await Files.get_file_by_id(file_id)
        if not file:
            return json.dumps({'error': 'File not found'})

        if (
            file.user_id != user_id
            and user_role != 'admin'
            and not await has_access_to_file(
                file_id=file_id,
                access_type='read',
                user=UserModel(**__user__),
            )
        ):
            return json.dumps({'error': 'File not found'})

        content = ''
        if file.data:
            content = file.data.get('content', '')

        total_chars = len(content)
        sliced = content[offset : offset + max_chars]
        is_truncated = (offset + len(sliced)) < total_chars

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = len(sliced)
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + len(sliced)

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_file error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# SKILLS TOOLS
# =============================================================================


async def view_skill(
    id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Load the full instructions of a skill by its id from the available skills manifest.
    Use this when you need detailed instructions for a skill listed in <available_skills>.

    :param id: The id of the skill to load (as shown in the manifest)
    :return: The full skill instructions as markdown content
    """
    if __request__ is None:
        return json.dumps({'error': 'Request context not available'})

    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.skills import Skills
        from open_webui.models.access_grants import AccessGrants

        user_id = __user__.get('id')

        # Direct DB lookup by id (case-insensitive since IDs are stored lowercase)
        skill = await Skills.get_skill_by_id(id.lower())

        if not skill or not skill.is_active:
            return json.dumps({'error': f"Skill '{id}' not found"})

        # Check user access
        user_role = __user__.get('role', 'user')
        if user_role != 'admin' and skill.user_id != user_id:
            user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='skill',
                resource_id=skill.id,
                permission='read',
                user_group_ids=set(user_group_ids),
            ):
                return json.dumps({'error': 'Access denied'})

        return json.dumps(
            {
                'name': skill.name,
                'content': skill.content,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_skill error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# TASK MANAGEMENT TOOLS
# =============================================================================

from pydantic import BaseModel, Field
from typing import Literal

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a task checklist to track progress on multi-step work.
    Call this once at the start to define all steps, then use
    update_task to mark each task as you complete it.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if __chat_id__ is None:
        return json.dumps({'error': 'Chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return json.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return json.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single task as completed, in_progress, pending, or cancelled.
    Call this after finishing each step. You MUST call this for every
    task, including the very last one.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if __chat_id__ is None:
        return json.dumps({'error': 'Chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return json.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return json.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return json.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# RCA LOG DIAGNOSTIC TOOLS
# =============================================================================

MAX_RCA_OUTPUT_BYTES = 50 * 1024
MAX_GREP_MATCHES = 30
MAX_LINE_LENGTH = 500


def _json_response(data: dict) -> str:
    truncated = False
    while True:
        result = json.dumps(data, ensure_ascii=False)
        if len(result.encode('utf-8')) <= MAX_RCA_OUTPUT_BYTES:
            break
        list_keys = [k for k, v in data.items() if isinstance(v, list) and v]
        if not list_keys:
            preview = result.encode('utf-8')[: MAX_RCA_OUTPUT_BYTES - 80].decode('utf-8', errors='ignore')
            return json.dumps({'error': 'Result too large', 'truncated_preview': preview}, ensure_ascii=False)
        key = max(list_keys, key=lambda k: len(data[k]))
        data[key] = data[key][: -max(1, len(data[key]) // 5)]
        truncated = True
    if truncated:
        data['truncated'] = True
    return json.dumps(data, ensure_ascii=False)


async def _get_chat_files(metadata: dict, user: dict, filename_filter: str = '') -> list:
    """Get files associated with current chat from metadata."""
    if not user:
        return []

    from open_webui.models.files import Files
    from open_webui.utils.access_control.files import has_access_to_file

    user_id = user.get('id')
    user_role = user.get('role', 'user')

    file_ids = []
    for item in (metadata or {}).get('files', []):
        if isinstance(item, dict) and item.get('id'):
            file_ids.append(item['id'])

    files = []
    for fid in file_ids:
        f = await Files.get_file_by_id(fid)
        if not f:
            continue
        if filename_filter and filename_filter.lower() not in f.filename.lower():
            continue
        if (
            f.user_id != user_id
            and user_role != 'admin'
            and not await has_access_to_file(
                file_id=fid,
                access_type='read',
                user=UserModel(**user),
            )
        ):
            continue
        files.append(f)
    return files


def _file_lines(file) -> list[str]:
    content = (file.data or {}).get('content', '') if file.data else ''
    return content.splitlines()


def _get_extracted_dir(file) -> str | None:
    """获取压缩包文件对应的磁盘解压目录路径，不存在则返回 None。"""
    data = file.data or {} if file.data else {}
    extracted_dir = data.get('extracted_dir')
    if extracted_dir and os.path.isdir(extracted_dir):
        return extracted_dir
    return None


def _read_all_lines_from_extracted(extracted_dir: str, filename_filter: str = '') -> list[tuple[str, list[str]]]:
    """从解压目录中读取所有日志文件，返回 (相对路径, 行列表) 元组。"""
    results = []
    for root, _dirs, files in os.walk(extracted_dir):
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, extracted_dir)
            if filename_filter and filename_filter.lower() not in rel_path.lower():
                continue
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                    lines = [l.rstrip('\n') for l in fh.readlines()]
                results.append((rel_path, lines))
            except Exception:
                continue
    return results


def _truncate_line(line: str, max_len: int = MAX_LINE_LENGTH) -> str:
    """截断过长的日志行，避免返回给模型的数据量过大。"""
    if len(line) <= max_len:
        return line
    return line[:max_len] + f'... [截断，原长{len(line)}字符]'


def _grep_in_extracted_dir(extracted_dir: str, regex, filename_filter: str = '',
                           context: int = 3, max_matches: int = 200) -> list[dict]:
    """在磁盘上的解压目录中搜索日志文件，返回匹配结果。"""
    matches = []
    for root, _dirs, files in os.walk(extracted_dir):
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, extracted_dir)
            if filename_filter and filename_filter.lower() not in rel_path.lower():
                continue
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                    lines = fh.readlines()
                for idx, line in enumerate(lines):
                    line = line.rstrip('\n')
                    if not regex.search(line):
                        continue
                    line_no = idx + 1
                    before_start = max(0, idx - context)
                    after_end = min(len(lines), idx + context + 1)
                    matches.append({
                        'file': rel_path,
                        'line_number': line_no,
                        'location': f'{rel_path}:{line_no}',
                        'content': _truncate_line(line),
                        'context_before': [
                            {'line_number': before_start + i + 1, 'content': _truncate_line(l.rstrip('\n'))}
                            for i, l in enumerate(lines[before_start:idx])
                        ],
                        'context_after': [
                            {'line_number': idx + 2 + i, 'content': _truncate_line(l.rstrip('\n'))}
                            for i, l in enumerate(lines[idx + 1:after_end])
                        ],
                    })
                    if len(matches) >= max_matches:
                        return matches
            except Exception:
                continue
    return matches


def _parse_time_bound(value: str) -> tuple[Optional[datetime], bool]:
    """Parse a time bound string. Returns (datetime, is_time_only)."""
    from open_webui.utils.log_analyzer import _parse_timestamp

    value = value.strip()
    if re.match(r'^\d{2}:\d{2}(:\d{2})?(?:\.\d+)?$', value):
        for fmt in ('%H:%M:%S.%f', '%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(value, fmt), True
            except ValueError:
                continue
        return None, True

    parsed = _parse_timestamp(value)
    return parsed, False


def _line_in_time_window(line: str, start: str, end: str) -> bool:
    from open_webui.utils.log_analyzer import _extract_timestamp, _parse_timestamp

    ts_str = _extract_timestamp(line)
    if not ts_str:
        return False

    line_dt = _parse_timestamp(ts_str)
    if not line_dt:
        return False

    start_dt, start_time_only = _parse_time_bound(start)
    end_dt, end_time_only = _parse_time_bound(end)
    if start_dt is None or end_dt is None:
        return False

    if start_time_only or end_time_only:
        line_time = line_dt.time()
        return start_dt.time() <= line_time <= end_dt.time()

    return start_dt <= line_dt <= end_dt


def _line_matches_level(line: str, level: str) -> bool:
    if not level:
        return True
    from open_webui.utils.log_analyzer import LOG_LEVEL_PATTERN, _normalize_level

    target = _normalize_level(level.strip())
    for match in LOG_LEVEL_PATTERN.finditer(line):
        if _normalize_level(match.group(1)) == target:
            return True
    return False


async def grep_log(
    pattern: str,
    file: str = '',
    context: int = 3,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Search uploaded log files using a regex pattern. Returns matching lines with context.
    Use this when you need to find specific error patterns, keywords, or log entries in uploaded files.

    :param pattern: Regular expression pattern to search for
    :param file: Optional filename to search in. If empty, searches all uploaded files in the current chat.
    :param context: Number of lines of context to show before and after each match (default: 3)
    :return: JSON with matches [{file, line_number, content, context_before, context_after}]
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    if isinstance(context, str):
        try:
            context = int(context)
        except ValueError:
            context = 3
    context = max(0, context)

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return json.dumps({'error': f'Invalid regex pattern: {e}'})

    try:
        log.info(f'[grep_log] 开始搜索 pattern={pattern}, file={file}')
        files = await _get_chat_files(__metadata__, __user__, filename_filter='')
        if not files:
            log.info('[grep_log] 未找到任何文件')
            return json.dumps({'error': 'No accessible files found in current chat', 'matches': []})

        matches = []
        for f in files:
            extracted_dir = _get_extracted_dir(f)
            if extracted_dir:
                disk_matches = await asyncio.to_thread(
                    _grep_in_extracted_dir, extracted_dir, regex,
                    filename_filter=file, context=context, max_matches=MAX_GREP_MATCHES,
                )
                for m in disk_matches:
                    m['file'] = f'{f.filename}/{m["file"]}'
                    m['location'] = f'{m["file"]}:{m["line_number"]}'
                matches.extend(disk_matches)
            else:
                if file and file.lower() not in f.filename.lower():
                    continue
                lines = _file_lines(f)
                for idx, line in enumerate(lines):
                    if not regex.search(line):
                        continue
                    line_no = idx + 1
                    before_start = max(0, idx - context)
                    after_end = min(len(lines), idx + context + 1)
                    matches.append(
                        {
                            'file': f.filename,
                            'line_number': line_no,
                            'location': f'{f.filename}:{line_no}',
                            'content': _truncate_line(line),
                            'context_before': [
                                {'line_number': before_start + i + 1, 'content': _truncate_line(l)}
                                for i, l in enumerate(lines[before_start:idx])
                            ],
                            'context_after': [
                                {'line_number': idx + 2 + i, 'content': _truncate_line(l)}
                                for i, l in enumerate(lines[idx + 1 : after_end])
                            ],
                        }
                    )
                    if len(matches) >= MAX_GREP_MATCHES:
                        break
            if len(matches) >= MAX_GREP_MATCHES:
                break

        log.info(f'[grep_log] 搜索完成，找到 {len(matches)} 条匹配')
        return _json_response({'matches': matches, 'total': len(matches), 'limit': MAX_GREP_MATCHES})
    except Exception as e:
        log.exception(f'grep_log error: {e}')
        return json.dumps({'error': str(e)})


async def get_context(
    file: str,
    line: int,
    before: int = 10,
    after: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Get lines around a specific line number in an uploaded file.
    Use this after grep_log finds something interesting, to see more surrounding context.

    :param file: Filename to read from
    :param line: The line number to center on (1-based)
    :param before: Number of lines to show before the target line (default: 10)
    :param after: Number of lines to show after the target line (default: 10)
    :return: JSON with the requested lines and metadata
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    if isinstance(line, str):
        try:
            line = int(line)
        except ValueError:
            return json.dumps({'error': 'Invalid line number'})
    if isinstance(before, str):
        try:
            before = int(before)
        except ValueError:
            before = 10
    if isinstance(after, str):
        try:
            after = int(after)
        except ValueError:
            after = 10

    before = max(0, before)
    after = max(0, after)

    try:
        all_files = await _get_chat_files(__metadata__, __user__)
        if not all_files:
            return json.dumps({'error': f'File not found: {file}'})

        # Try extracted dirs first: file param may be "archive.zip/subdir/file.log"
        target_lines = None
        actual_file_name = file

        for f in all_files:
            extracted_dir = _get_extracted_dir(f)
            if extracted_dir:
                prefix = f'{f.filename}/'
                sub_path = file[len(prefix):] if file.startswith(prefix) else file
                file_results = await asyncio.to_thread(
                    _read_all_lines_from_extracted, extracted_dir, sub_path,
                )
                if file_results:
                    actual_file_name = f'{f.filename}/{file_results[0][0]}'
                    target_lines = file_results[0][1]
                    break

        if target_lines is None:
            matched = [f for f in all_files if file.lower() in f.filename.lower()]
            if not matched:
                return json.dumps({'error': f'File not found: {file}'})
            actual_file_name = matched[0].filename
            target_lines = _file_lines(matched[0])

        total_lines = len(target_lines)
        if line < 1 or line > total_lines:
            return json.dumps(
                {
                    'error': f'Line {line} out of range (file has {total_lines} lines)',
                    'file': actual_file_name,
                    'total_lines': total_lines,
                }
            )

        idx = line - 1
        start = max(0, idx - before)
        end = min(total_lines, idx + after + 1)
        numbered_lines = [
            {
                'line_number': i + 1,
                'location': f'{actual_file_name}:{i + 1}',
                'content': target_lines[i],
                'is_target': i == idx,
            }
            for i in range(start, end)
        ]

        return _json_response(
            {
                'file': actual_file_name,
                'target_line': line,
                'target_location': f'{actual_file_name}:{line}',
                'total_lines': total_lines,
                'lines': numbered_lines,
            }
        )
    except Exception as e:
        log.exception(f'get_context error: {e}')
        return json.dumps({'error': str(e)})


async def time_window(
    start: str,
    end: str,
    level: str = '',
    file: str = '',
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Filter log entries by time range. Optionally filter by log level.
    Use this when you need to focus on a specific time period around the fault.

    :param start: Start time (e.g., "2026-05-21 03:12:00" or "03:12:00")
    :param end: End time (e.g., "2026-05-21 03:45:00" or "03:45:00")
    :param level: Optional log level filter (e.g., "ERROR", "WARN")
    :param file: Optional filename. If empty, searches all files.
    :return: JSON with filtered log entries
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        all_files = await _get_chat_files(__metadata__, __user__)
        if not all_files:
            return json.dumps({'error': 'No accessible files found in current chat', 'entries': []})

        entries = []
        for f in all_files:
            extracted_dir = _get_extracted_dir(f)
            if extracted_dir:
                file_results = await asyncio.to_thread(
                    _read_all_lines_from_extracted, extracted_dir, file,
                )
                for rel_path, lines in file_results:
                    for idx, line in enumerate(lines):
                        if not _line_in_time_window(line, start, end):
                            continue
                        if not _line_matches_level(line, level):
                            continue
                        line_no = idx + 1
                        file_label = f'{f.filename}/{rel_path}'
                        entries.append({
                            'file': file_label,
                            'line_number': line_no,
                            'location': f'{file_label}:{line_no}',
                            'content': line,
                        })
                        if len(entries) >= MAX_GREP_MATCHES:
                            break
                    if len(entries) >= MAX_GREP_MATCHES:
                        break
            else:
                if file and file.lower() not in f.filename.lower():
                    continue
                lines = _file_lines(f)
                for idx, line_text in enumerate(lines):
                    if not _line_in_time_window(line_text, start, end):
                        continue
                    if not _line_matches_level(line_text, level):
                        continue
                    line_no = idx + 1
                    entries.append({
                        'file': f.filename,
                        'line_number': line_no,
                        'location': f'{f.filename}:{line_no}',
                        'content': line_text,
                    })
                    if len(entries) >= MAX_GREP_MATCHES:
                        break
            if len(entries) >= MAX_GREP_MATCHES:
                break

        return _json_response(
            {
                'start': start,
                'end': end,
                'level': level or None,
                'entries': entries,
                'total': len(entries),
                'limit': MAX_GREP_MATCHES,
            }
        )
    except Exception as e:
        log.exception(f'time_window error: {e}')
        return json.dumps({'error': str(e)})


async def count_errors(
    file: str = '',
    top_n: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Aggregate and count error patterns in uploaded log files. Groups similar errors and returns top N.
    Use this to understand the distribution of errors and identify the most frequent issues.

    :param file: Optional filename. If empty, analyzes all uploaded files.
    :param top_n: Number of top error patterns to return (default: 10)
    :return: JSON with error pattern statistics
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    if isinstance(top_n, str):
        try:
            top_n = int(top_n)
        except ValueError:
            top_n = 10
    top_n = max(1, top_n)

    try:
        from open_webui.utils.log_analyzer import _is_error_line, _normalize_error_pattern

        all_files = await _get_chat_files(__metadata__, __user__)
        if not all_files:
            return json.dumps({'error': 'No accessible files found in current chat', 'patterns': []})

        pattern_stats: dict[str, dict] = {}

        def _count_lines(lines: list[str], file_label: str):
            for idx, line in enumerate(lines):
                if not _is_error_line(line):
                    continue
                normalized = _normalize_error_pattern(line)
                pattern_hash = hashlib.md5(normalized.encode()).hexdigest()
                if pattern_hash not in pattern_stats:
                    pattern_stats[pattern_hash] = {
                        'pattern': normalized,
                        'count': 0,
                        'first_line': idx + 1,
                        'first_file': file_label,
                        'example': line.strip()[:500],
                    }
                pattern_stats[pattern_hash]['count'] += 1

        for f in all_files:
            extracted_dir = _get_extracted_dir(f)
            if extracted_dir:
                file_results = await asyncio.to_thread(
                    _read_all_lines_from_extracted, extracted_dir, file,
                )
                for rel_path, lines in file_results:
                    _count_lines(lines, f'{f.filename}/{rel_path}')
            else:
                if file and file.lower() not in f.filename.lower():
                    continue
                _count_lines(_file_lines(f), f.filename)

        patterns = sorted(pattern_stats.values(), key=lambda x: x['count'], reverse=True)[:top_n]
        total_errors = sum(item['count'] for item in pattern_stats.values())

        return _json_response(
            {
                'total_errors': total_errors,
                'unique_patterns': len(pattern_stats),
                'patterns': patterns,
            }
        )
    except Exception as e:
        log.exception(f'count_errors error: {e}')
        return json.dumps({'error': str(e)})


async def list_files(
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    List all files uploaded in the current chat session.
    Use this to see what files are available for analysis.

    :return: JSON with file list [{id, filename, size, content_type, has_content}]
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    try:
        files = await _get_chat_files(__metadata__, __user__)
        result = []
        for f in files:
            meta = f.meta or {}
            content = (f.data or {}).get('content', '') if f.data else ''
            data = f.data or {}
            extracted_files = data.get('extracted_files', []) if data else []
            extracted_dir = _get_extracted_dir(f)
            entry = {
                'id': f.id,
                'filename': f.filename,
                'size': meta.get('size'),
                'content_type': meta.get('content_type'),
                'has_content': bool(content),
            }
            if extracted_files:
                entry['is_archive'] = True
                entry['extracted_files'] = extracted_files
                entry['extracted_on_disk'] = bool(extracted_dir)
            result.append(entry)
        return _json_response({'files': result, 'total': len(result)})
    except Exception as e:
        log.exception(f'list_files error: {e}')
        return json.dumps({'error': str(e)})


# =============================================================================
# RCA SANDBOX TOOL
# =============================================================================


async def run_script(
    script: str,
    lang: str = 'bash',
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute an analysis script in a restricted sandbox environment.
    Only whitelisted commands (grep, awk, sed, sort, uniq, wc, head, tail, cat, python3, jq, etc.)
    are allowed. Network access is disabled and execution is time-limited.
    Use this when you need to run custom analysis that the other tools can't handle.

    :param script: The script content to execute
    :param lang: Script language - "bash" or "python3" (default: bash)
    :return: JSON with stdout, stderr, exit_code, and execution metadata
    """
    if not __user__:
        return json.dumps({'error': 'User context not available'})

    from open_webui.env import ENABLE_SANDBOX_SHELL, SANDBOX_TIMEOUT, SANDBOX_MAX_OUTPUT, SANDBOX_ALLOWED_COMMANDS

    if not ENABLE_SANDBOX_SHELL:
        return json.dumps({
            'error': 'Sandbox shell is disabled. Set ENABLE_SANDBOX_SHELL=true to enable.',
            'hint': 'Use grep_log, get_context, time_window, and count_errors tools instead.',
        })

    user_role = (__user__ or {}).get('role', 'user')
    if user_role != 'admin':
        return json.dumps({'error': 'Only administrators can use the sandbox shell.'})

    lang = lang.strip().lower()
    if lang not in ('bash', 'python3'):
        return json.dumps({'error': f'Unsupported language: {lang}. Use "bash" or "python3".'})

    if not script or not script.strip():
        return json.dumps({'error': 'Script content is empty.'})

    if lang == 'bash':
        validation_error = _validate_bash_script(script, SANDBOX_ALLOWED_COMMANDS)
        if validation_error:
            return json.dumps({
                'error': f'Script uses disallowed commands: {validation_error}',
                'allowed_commands': sorted(SANDBOX_ALLOWED_COMMANDS),
            })

    import subprocess
    import tempfile
    import os

    chat_files = await _get_chat_files(__metadata__ or {}, __user__)
    file_paths = {}
    extracted_dirs = []
    for f in chat_files:
        extracted_dir = _get_extracted_dir(f)
        if extracted_dir:
            extracted_dirs.append(extracted_dir)
        else:
            content = (f.data or {}).get('content', '') if f.data else ''
            if content:
                file_paths[f.filename] = content

    try:
        with tempfile.TemporaryDirectory(prefix='rca_sandbox_') as tmpdir:
            for fname, content in file_paths.items():
                safe_name = re.sub(r'[^\w.\-]', '_', fname)
                fpath = os.path.join(tmpdir, safe_name)
                with open(fpath, 'w', encoding='utf-8') as fh:
                    fh.write(content)

            # 将解压目录通过符号链接注入沙箱
            for idx, edir in enumerate(extracted_dirs):
                link_name = os.path.join(tmpdir, f'logs_{idx}' if idx > 0 else 'logs')
                if not os.path.exists(link_name):
                    os.symlink(edir, link_name)

            # 将预置的分析脚本目录链接到沙箱中
            from open_webui.env import SKILLS_DIR
            skills_scripts_dir = os.path.join(str(SKILLS_DIR), 'scripts')
            if os.path.isdir(skills_scripts_dir):
                scripts_link = os.path.join(tmpdir, 'scripts')
                if not os.path.exists(scripts_link):
                    os.symlink(skills_scripts_dir, scripts_link)

            if lang == 'bash':
                script_file = os.path.join(tmpdir, '_script.sh')
                with open(script_file, 'w', encoding='utf-8') as fh:
                    fh.write(script)
                cmd = ['bash', script_file]
            else:
                script_file = os.path.join(tmpdir, '_script.py')
                with open(script_file, 'w', encoding='utf-8') as fh:
                    fh.write(script)
                cmd = ['python3', script_file]

            log_dir = extracted_dirs[0] if extracted_dirs else tmpdir
            env = {
                'PATH': '/usr/local/bin:/usr/bin:/bin',
                'HOME': tmpdir,
                'LANG': 'en_US.UTF-8',
                'FILES_DIR': tmpdir,
                'LOG_DIR': log_dir,
                'SCRIPTS_DIR': os.path.join(tmpdir, 'scripts'),
            }

            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT,
                cwd=tmpdir,
                env=env,
            )

            stdout = proc.stdout or ''
            stderr = proc.stderr or ''

            stdout_truncated = len(stdout.encode('utf-8')) > SANDBOX_MAX_OUTPUT
            stderr_truncated = len(stderr.encode('utf-8')) > SANDBOX_MAX_OUTPUT

            if stdout_truncated:
                stdout = stdout.encode('utf-8')[:SANDBOX_MAX_OUTPUT].decode('utf-8', errors='ignore')
            if stderr_truncated:
                stderr = stderr.encode('utf-8')[:SANDBOX_MAX_OUTPUT].decode('utf-8', errors='ignore')

            return _json_response({
                'exit_code': proc.returncode,
                'stdout': stdout,
                'stderr': stderr,
                'stdout_truncated': stdout_truncated,
                'stderr_truncated': stderr_truncated,
                'lang': lang,
                'timeout': SANDBOX_TIMEOUT,
                'files_available': list(file_paths.keys()),
                'extracted_log_dirs': extracted_dirs,
                'log_dir': log_dir,
            })

    except subprocess.TimeoutExpired:
        return json.dumps({
            'error': f'Script execution timed out after {SANDBOX_TIMEOUT} seconds.',
            'timeout': SANDBOX_TIMEOUT,
        })
    except Exception as e:
        log.exception(f'run_script error: {e}')
        return json.dumps({'error': str(e)})


def _validate_bash_script(script: str, allowed: set) -> str:
    """Check bash script for disallowed commands. Returns error string or empty."""
    dangerous = {
        'rm', 'rmdir', 'mkfs', 'dd', 'chmod', 'chown', 'kill', 'killall',
        'shutdown', 'reboot', 'halt', 'poweroff', 'mount', 'umount',
        'wget', 'curl', 'nc', 'ncat', 'ssh', 'scp', 'rsync', 'ftp',
        'pip', 'pip3', 'npm', 'apt', 'yum', 'dnf', 'pacman',
        'docker', 'kubectl', 'systemctl', 'service',
        'su', 'sudo', 'passwd', 'useradd', 'userdel',
        'iptables', 'nft', 'firewall-cmd',
        'eval', 'exec', 'source',
    }
    lines = script.splitlines()
    violations = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'[|;&\s]+', line)
        for part in parts:
            cmd = part.strip('(').strip(')').strip()
            if not cmd:
                continue
            base_cmd = cmd.split('/')[-1]
            if base_cmd in dangerous:
                violations.add(base_cmd)
    return ', '.join(sorted(violations)) if violations else ''


