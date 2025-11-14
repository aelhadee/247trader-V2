import json

from infra.state_store import (
    JsonFileBackend,
    SQLiteStateBackend,
    StateStore,
    StateStoreSupervisor,
    create_state_store_from_config,
)


def test_create_state_store_from_config_respects_sqlite(tmp_path):
    cfg = {"store": "sqlite", "path": str(tmp_path / "state.db")}
    store = create_state_store_from_config(cfg)
    assert isinstance(store._backend, SQLiteStateBackend)
    # Should point at configured path
    assert "state.db" in store._backend.describe()


def test_state_store_supervisor_can_persist_and_backup(tmp_path):
    state_path = tmp_path / "state.json"
    backend = JsonFileBackend(state_path)
    store = StateStore(backend=backend)
    state = store.load()
    state["events"] = []
    store.save(state)

    supervisor = StateStoreSupervisor(
        store,
        persist_interval_seconds=0.1,
        backup_config={
            "enabled": True,
            "interval_seconds": 0.1,
            "path": tmp_path / "backups",
            "max_files": 2,
        },
    )

    try:
    supervisor.start()
    state = store.load()
    state["pnl_today"] = 42.0
    # Do not save – supervisor flush should persist update
    state["pnl_today"] = 43.0
    supervisor.force_persist()
        on_disk = json.loads(state_path.read_text())
        assert on_disk["pnl_today"] == 43.0

        supervisor.force_backup()
        backups = list((tmp_path / "backups").glob("state-*.json"))
        assert backups, "backup file should be written"
    finally:
        supervisor.stop()
*** End Patch