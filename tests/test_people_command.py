import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_people_command_lists_all(temp_db_path):
    import database as db
    db.save_person("Megan", [], "dating", "2026-02-15", None)
    db.save_person("Sprink", [], "friend", "2024-01-01", None)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    from handlers.people import people_command
    await people_command(update, context)

    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    assert "Megan" in args[0]
    assert "Sprink" in args[0]


@pytest.mark.asyncio
async def test_people_merge(temp_db_path):
    import database as db
    keep = db.save_person("Spinkel", [], "friend", "2024-01-01", None)
    merge = db.save_person("Sprink", [], "friend", "2024-01-01", None)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["merge", str(merge), "into", str(keep)]

    from handlers.people import people_command
    await people_command(update, context)

    assert db.get_person(merge) is None
    assert db.get_person(keep) is not None
