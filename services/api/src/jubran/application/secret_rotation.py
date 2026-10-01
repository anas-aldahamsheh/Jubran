"""Re-encrypt stored secrets with the current data encryption key (after a key rotation)."""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.auth.secret_box import reencrypt_secret, uses_current_key
from jubran.infrastructure.db.models import AiModelConfigModel, AssistantConversationModel, TableQrTokenModel

logger = logging.getLogger("jubran.secrets")

# (table, encrypted column)
ENCRYPTED_COLUMNS = (
    (AiModelConfigModel, AiModelConfigModel.api_key_ciphertext),
    (TableQrTokenModel, TableQrTokenModel.token_ciphertext),
    (AssistantConversationModel, AssistantConversationModel.pending_token_ciphertext),
)


async def reencrypt_stored_secrets(db: AsyncSession) -> dict:
    """Bring every stored secret to the current key. Returns counts: updated / unreadable.

    Safe to run at every start-up: values already on the current key are left alone,
    and values no configured key can read are kept (and reported) rather than lost.
    """
    updated = unreadable = 0
    for model, column in ENCRYPTED_COLUMNS:
        rows = (await db.execute(select(model).where(column.is_not(None)))).scalars().all()
        for row in rows:
            ciphertext = getattr(row, column.key)
            if uses_current_key(ciphertext):
                continue
            try:
                setattr(row, column.key, reencrypt_secret(ciphertext))
                updated += 1
            except ValueError:
                unreadable += 1
    await db.commit()
    if updated:
        logger.info("Re-encrypted %d stored secret(s) with the current DATA_ENCRYPTION_KEYS key.", updated)
    if unreadable:
        logger.warning("%d stored secret(s) cannot be read with any configured key (an old key was removed "
                       "too early?). Re-enter those AI keys / replace those table QR codes.", unreadable)
    return {"updated": updated, "unreadable": unreadable}
