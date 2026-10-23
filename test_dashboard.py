from decimal import Decimal
from fastapi.testclient import TestClient
import dashboard_api


def test_health_endpoint():
    c = TestClient(dashboard_api.app)
    r = c.get('/health')
    assert r.status_code == 200
    assert r.json()['ok'] is True


def test_wallet_invalid_address():
    c = TestClient(dashboard_api.app)
    r = c.get('/api/wallet/not-an-address')
    assert r.status_code == 400


def test_yield_normalization(monkeypatch):
    monkeypatch.setattr(dashboard_api, '_json_get', lambda url: [
        {'protocol':'Euler','asset':'BOLD Vault','weekly_apr':0.36,'tvl':0,'link':'https://example.com'},
        {'protocol':'Test','asset':'Missing APR','weekly_apr':None,'tvl':None,'link':''},
    ])
    data = dashboard_api._yield_data()
    assert data['count'] == 2
    assert data['venues'][0]['apr_pct'] == 36.0
    assert data['venues'][1]['apr_pct'] == 0.0
    assert data['venues'][1]['tvl_usd'] is None
