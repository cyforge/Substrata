from concurrent.futures import ThreadPoolExecutor

from substrata.storage import ReplayStore


def test_claim_persists_and_scopes_proof_ids(tmp_path):
    path = tmp_path / "claims.sqlite3"
    store = ReplayStore(path)
    assert store.claim("route-a", "proof-1", digest="abc")
    assert not store.claim("route-a", "proof-1", digest="abc")
    assert store.claim("route-b", "proof-1", digest="abc")
    store.close()
    reopened = ReplayStore(path)
    assert not reopened.claim("route-a", "proof-1")
    reopened.close()


def test_concurrent_claim_is_single_winner(tmp_path):
    path = tmp_path / "claims.sqlite3"
    stores = [ReplayStore(path) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda store: store.claim("route", "proof"), stores))
    assert sum(results) == 1
    for store in stores:
        store.close()
