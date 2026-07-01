"""F3a: AtlasConfig — типизированный конфиг CLI на clikit.AppConfig."""
from atlas.appconfig import AtlasConfig, load_config


def test_defaults():
    cfg = AtlasConfig()
    assert cfg.base_url == "http://localhost:8000"
    assert cfg.portal_id == "atlas-local"
    assert cfg.api_key == ""


def test_env_override(monkeypatch, tmp_path):
    # Изолируем global-конфиг во временный каталог (не читать реальный ~/.config).
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_BASE_URL", "https://hub.example.com")
    monkeypatch.setenv("ATLAS_API_KEY", "secret123")
    cfg = AtlasConfig.load("atlas")
    assert cfg.base_url == "https://hub.example.com"
    assert cfg.api_key == "secret123"
    assert cfg.portal_id == "atlas-local"


def test_load_config_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    cfg = load_config()
    assert isinstance(cfg, AtlasConfig)
    assert cfg.portal_id == "atlas-local"
