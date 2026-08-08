from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_claim_evidence_can_be_queried_and_digest_conflicts_rejected(tmp_path):
    from substrata.errors import ValidationError

    store = ReplayStore(tmp_path / "claims.sqlite3")
    assert store.claim("stellar:testnet", "proof-1", digest="digest-a")
    claim = store.get_claim("stellar:testnet", "proof-1")
    assert claim is not None
    assert claim.digest == "digest-a"
    assert claim.scope == "stellar:testnet"
    assert claim.proof_id == "proof-1"
    assert store.count_claims() == 1
    assert store.count_claims(scope="stellar:testnet") == 1
    assert store.count_claims(scope="stellar:pubnet") == 0
    with pytest.raises(ValidationError, match="different digest"):
        store.claim("stellar:testnet", "proof-1", digest="digest-b")
    assert store.get_claim("stellar:testnet", "missing") is None
