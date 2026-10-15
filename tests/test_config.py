import pytest

from tradebuddy.config import load_config, require_safe_bind


def test_defaults_bind_to_loopback():
    cfg = load_config({})
    assert (cfg.host, cfg.port, cfg.db_path, cfg.api_token) == ("127.0.0.1", 8080, "data/tradebuddy.db", "")


def test_serving_beyond_loopback_needs_a_credential():
    """Off loopback, either the API token or a dashboard PIN has to be there. Nothing at all is
    refused outright — including when DASHBOARD_LOOPBACK_ONLY claims the port is not published,
    because nothing in this process can see what was put in front of it later."""
    with pytest.raises(RuntimeError):
        require_safe_bind(load_config({"HOST": "0.0.0.0", "AUTH_PIN": ""}))
    with pytest.raises(RuntimeError):
        require_safe_bind(load_config({"HOST": "0.0.0.0", "AUTH_PIN": "", "DASHBOARD_LOOPBACK_ONLY": "true"}))
    require_safe_bind(load_config({"HOST": "0.0.0.0", "API_TOKEN": "t", "AUTH_PIN": ""}))
    require_safe_bind(load_config({"HOST": "0.0.0.0", "AUTH_PIN": "135790"}))


def test_the_published_default_pin_warns_off_loopback(caplog):
    """It cannot refuse: the PIN may have been changed from the Settings page, where it lives in
    the database as a hash this process never reads. Api.get_auth_status makes the real check."""
    require_safe_bind(load_config({"HOST": "0.0.0.0"}))
    assert "dashboard_default_pin" in caplog.text
    caplog.clear()
    require_safe_bind(load_config({"HOST": "127.0.0.1"}))
    assert caplog.text == ""


def test_non_serving_roles_load_without_token():
    # engine, feed and worker containers share HOST=0.0.0.0 but serve no HTTP
    assert load_config({"HOST": "0.0.0.0"}).host == "0.0.0.0"


def test_zmq_and_celery_come_from_env():
    cfg = load_config({"ZMQ_RPC_URL": "tcp://engine:5557", "CELERY_BROKER_URL": "redis://redis:6379/0"})
    assert (cfg.zmq_rpc_url, cfg.zmq_rpc_bind, cfg.celery_broker_url) == ("tcp://engine:5557", "tcp://127.0.0.1:5557", "redis://redis:6379/0")
