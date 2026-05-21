import logging

from open_webui.models.users import UserModel
from open_webui.models.files import Files
from open_webui.models.chats import Chats
from open_webui.models.groups import Groups
from open_webui.models.models import Models
from open_webui.models.access_grants import AccessGrants

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def has_access_to_file(
    file_id: str | None,
    access_type: str,
    user: UserModel,
    db: AsyncSession | None = None,
) -> bool:
    """
    Check if a user has the specified access to a file through any of:
    - Shared workspace models that attach the file directly
    - Shared chats

    NOTE: This does NOT check direct file ownership — callers should check
    file.user_id == user.id separately before calling this.
    """
    file = await Files.get_file_by_id(file_id, db=db)
    log.debug(f'Checking if user has {access_type} access to file')
    if not file:
        return False

    # Direct ownership
    if file.user_id == user.id:
        return True

    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    # Check if the file is associated with any chats the user has access to
    shared_chat_ids = await Chats.get_shared_chat_ids_by_file_id(file_id, db=db)
    if shared_chat_ids:
        accessible_ids = await AccessGrants.get_accessible_resource_ids(
            user_id=user.id,
            resource_type='shared_chat',
            resource_ids=shared_chat_ids,
            permission='read',
            user_group_ids=user_group_ids,
            db=db,
        )
        if accessible_ids:
            return True

    # Check if the file is directly attached to a shared workspace model
    for model in await Models.get_models_by_user_id(user.id, permission=access_type, db=db):
        knowledge_items = getattr(model.meta, 'knowledge', None) or []
        for item in knowledge_items:
            if isinstance(item, dict) and item.get('type') == 'file' and item.get('id') == file.id:
                return True

    return False


async def get_accessible_folder_files(
    entries: list[dict] | None,
    user: UserModel,
    db: AsyncSession | None = None,
) -> list[dict]:
    """Filter folder.data['files'] entries to those the caller can read.

    Each entry is expected to have 'type' ('file' or 'collection') and 'id'.
    Admins bypass all checks. Unknown types are kept as-is.
    """
    if not entries:
        return []
    if user.role == 'admin':
        return list(entries)

    accessible: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get('type')
        entry_id = entry.get('id')
        if not entry_id:
            accessible.append(entry)
            continue
        if entry_type == 'file':
            if await has_access_to_file(entry_id, 'read', user, db=db):
                accessible.append(entry)
        elif entry_type == 'collection':
            continue
        else:
            accessible.append(entry)
    return accessible
