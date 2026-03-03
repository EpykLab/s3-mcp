import inspect

import s3_mcp_server


def test_package_main_runs_server_coroutine(monkeypatch):
    async def fake_server_main():
        return None

    captured = {}

    def fake_asyncio_run(coro):
        captured["is_coroutine"] = inspect.iscoroutine(coro)
        coro.close()

    monkeypatch.setattr("s3_mcp_server.server.main", fake_server_main)
    monkeypatch.setattr("s3_mcp_server.asyncio.run", fake_asyncio_run)

    s3_mcp_server.main()

    assert captured["is_coroutine"] is True
