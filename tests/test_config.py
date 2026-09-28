import pytest

from tradebuddy.config import load_config


def test_defaults_bind_to_loopback():
    cfg = load_config({})
    assert (cfg.host, cfg.port, cfg.db_path, cfg.api_token) == ("127.0.0.1", 8080, "data/tradebuddy.db", "")


def test_network_bind_needs_token():
    with pytest.raises(RuntimeError):
        load_config({"HOST": "0.0.0.0"})
    assert load_config({"HOST": "0.0.0.0", "API_TOKEN": "t"}).host == "0.0.0.0"
