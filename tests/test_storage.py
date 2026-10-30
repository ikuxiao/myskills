import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.storage as storage
from app.storage import AppError, FileStore, json_bytes, safe_path


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'C:/outside', 'a\\b', './x', 'CON', 'a/NUL.txt', 'a/..', 'a.'])
def test_path_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(AppError):
        safe_path(tmp_path, name)


def test_transaction_rolls_back_all_files_after_write_failure(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    store.commit({'data/a.json': b'original', 'data/b.json': b'untouched'})
    original = storage.atomic_write
    count = 0
    def broken(path, value):
        nonlocal count
        if path.name == 'b.json' and count == 0:
            count += 1
            raise OSError('simulated disk failure')
        original(path, value)
    monkeypatch.setattr(storage, 'atomic_write', broken)
    with pytest.raises(OSError):
        store.commit({'data/a.json': b'changed', 'data/b.json': b'changed'})
    assert store.path('data/a.json').read_bytes() == b'original'
    assert store.path('data/b.json').read_bytes() == b'untouched'
    assert not store.path('.transaction.json').exists()


def test_recover_interrupted_transaction_on_restart(tmp_path):
    store = FileStore(tmp_path)
    storage.atomic_write(store.path('.transaction.json'), json_bytes({'phase': 'applying', 'before': {'data/a.json': base64.b64encode(b'original').decode(), 'data/new.json': None}}))
    storage.atomic_write(store.path('data/a.json'), b'partial')
    storage.atomic_write(store.path('data/new.json'), b'new')
    recovered = FileStore(tmp_path)
    assert recovered.path('data/a.json').read_bytes() == b'original'
    assert not recovered.path('data/new.json').exists()


def test_corrupt_json_is_never_silently_replaced(tmp_path):
    store = FileStore(tmp_path)
    store.commit({'data/x.json': b'{broken'})
    with pytest.raises(AppError, match='损坏'):
        store.read_json('data/x.json')
    assert store.path('data/x.json').read_bytes() == b'{broken'


def test_parallel_writers_preserve_updates(tmp_path):
    store = FileStore(tmp_path)
    store.commit({'counter.json': json_bytes({'count': 0})})
    def increment(_):
        local = FileStore(tmp_path)
        with local.locked():
            doc = local.read_json('counter.json')
            local.commit({'counter.json': json_bytes({'count': doc['count'] + 1})})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(16)))
    assert store.read_json('counter.json')['count'] == 16


def test_config_backup_contains_exact_previous_bytes(tmp_path):
    store = FileStore(tmp_path)
    store.commit({'data/config.json': b'{"v":1}'})
    store.commit({'data/config.json': b'{"v":2}'})
    backups = list((tmp_path / 'backups').glob('*config.json'))
    assert len(backups) == 1 and backups[0].read_bytes() == b'{"v":1}'
