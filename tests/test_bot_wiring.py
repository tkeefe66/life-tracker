"""Verify bot.py registers new Life Log handlers."""


def test_create_application_registers_log_command(temp_db_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    import importlib
    import config
    importlib.reload(config)
    import bot
    importlib.reload(bot)

    app = bot.create_application()
    cmds = []
    for handler_group in app.handlers.values():
        for h in handler_group:
            for cmd in getattr(h, "commands", []) or []:
                cmds.append(cmd)
    assert "log" in cmds


def test_buildstories_and_syncstories_registered(temp_db_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    import importlib
    import config
    importlib.reload(config)
    import bot
    importlib.reload(bot)
    app = bot.create_application()
    cmds = []
    for grp in app.handlers.values():
        for h in grp:
            for cmd in getattr(h, "commands", []) or []:
                cmds.append(cmd)
    assert "buildstories" in cmds
    assert "syncstories" in cmds
    assert "pushstories" in cmds
    assert "showstory" in cmds


def test_legacy_proposal_commands_removed(temp_db_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    import importlib
    import config
    importlib.reload(config)
    import bot
    importlib.reload(bot)
    app = bot.create_application()
    cmds = []
    for grp in app.handlers.values():
        for h in grp:
            for cmd in getattr(h, "commands", []) or []:
                cmds.append(cmd)
    assert "syncproposals" not in cmds
    assert "pushproposals" not in cmds
    assert "proposals" not in cmds
    assert "showproposal" not in cmds
