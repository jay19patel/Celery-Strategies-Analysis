import pytest

from tradebuddy.config import load_config, require_safe_bind


def test_defaults_bind_to_loopback():
    cfg = load_config({})
    assert (cfg.host, cfg.port, cfg.db_path, cfg.api_token) == ("127.0.0.1", 8080, "data/tradebuddy.db", "")


def test_serving_beyond_loopback_needs_token():
    with pytest.raises(RuntimeError):
        require_safe_bind(load_config({"HOST": "0.0.0.0"}))
    require_safe_bind(load_config({"HOST": "0.0.0.0", "API_TOKEN": "t"}))


def test_loopback_published_container_needs_no_token():
    require_safe_bind(load_config({"HOST": "0.0.0.0", "DASHBOARD_LOOPBACK_ONLY": "true"}))


def test_non_serving_roles_load_without_token():
    # engine, feed and worker containers share HOST=0.0.0.0 but serve no HTTP
    assert load_config({"HOST": "0.0.0.0"}).host == "0.0.0.0"


def test_zmq_and_celery_come_from_env():
    cfg = load_config({"ZMQ_RPC_URL": "tcp://engine:5557", "CELERY_BROKER_URL": "redis://redis:6379/0"})
    assert (cfg.zmq_rpc_url, cfg.zmq_rpc_bind, cfg.celery_broker_url) == ("tcp://engine:5557", "tcp://127.0.0.1:5557", "redis://redis:6379/0")
