"""Stored secrets survive a SECRET_KEY change and a data-key rotation."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from jubran.application.qr_service import QrService
from jubran.application.secret_rotation import reencrypt_stored_secrets
from jubran.infrastructure.auth.secret_box import decrypt_secret, encrypt_secret, uses_current_key
from jubran.infrastructure.db.models import AiModelConfigModel, PhysicalTableModel, TableQrTokenModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings


async def stored(db):
    db.expire_all()
    ai = (await db.execute(select(AiModelConfigModel).where(AiModelConfigModel.purpose == "embedding"))).scalar_one()
    qr = (await db.execute(select(TableQrTokenModel).where(TableQrTokenModel.is_active.is_(True)))).scalars().first()
    return ai.api_key_ciphertext, qr.token_ciphertext


@pytest.mark.asyncio
async def test_keys_can_be_rotated_without_losing_ai_keys_or_qr_codes(db_session, monkeypatch):
    await seed_database(db_session)
    # Saved the old way: DATA_ENCRYPTION_KEYS not set yet (key derived from SECRET_KEY).
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", "")
    db_session.add(AiModelConfigModel(purpose="embedding", provider="gemini", model_id="m",
                                      api_key_ciphertext=encrypt_secret("AIza-provider-key")))
    table = (await db_session.execute(select(PhysicalTableModel).limit(1))).scalar_one()
    qr_token = (await QrService.create_table_qr(db_session, table.id))["qr_token"]
    await db_session.commit()

    # 1. The owner adds a real data key: old values stay readable, then move to it.
    first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", first)
    ai_key, qr = await stored(db_session)
    assert decrypt_secret(ai_key) == "AIza-provider-key" and not uses_current_key(ai_key)
    assert await reencrypt_stored_secrets(db_session) == {"updated": 2, "unreadable": 0}
    ai_key, qr = await stored(db_session)
    assert uses_current_key(ai_key) and uses_current_key(qr)

    # 2. SECRET_KEY can now change freely.
    monkeypatch.setattr(settings, "SECRET_KEY", "a-completely-different-secret-key-for-sessions-2027")
    assert decrypt_secret(ai_key) == "AIza-provider-key" and decrypt_secret(qr) == qr_token

    # 3. Data key rotation: new key first, old one kept until the restart re-encrypts.
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", f"{second},{first}")
    assert await reencrypt_stored_secrets(db_session) == {"updated": 2, "unreadable": 0}
    assert await reencrypt_stored_secrets(db_session) == {"updated": 0, "unreadable": 0}  # nothing left
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", second)
    ai_key, qr = await stored(db_session)
    assert decrypt_secret(ai_key) == "AIza-provider-key" and decrypt_secret(qr) == qr_token


@pytest.mark.asyncio
async def test_removing_a_key_too_early_keeps_the_data_and_reports_it(db_session, monkeypatch):
    await seed_database(db_session)
    old = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", old)
    db_session.add(AiModelConfigModel(purpose="embedding", provider="gemini", model_id="m",
                                      api_key_ciphertext=encrypt_secret("AIza-provider-key")))
    await db_session.commit()
    before = (await stored_ai(db_session))

    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    assert await reencrypt_stored_secrets(db_session) == {"updated": 0, "unreadable": 1}
    assert await stored_ai(db_session) == before  # untouched: putting the old key back recovers it
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", old)
    assert decrypt_secret(before) == "AIza-provider-key"


async def stored_ai(db):
    db.expire_all()
    return (await db.execute(select(AiModelConfigModel.api_key_ciphertext)
                             .where(AiModelConfigModel.purpose == "embedding"))).scalar_one()
