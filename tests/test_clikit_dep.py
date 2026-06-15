"""F3a: clikit подключён как зависимость atlas."""


def test_clikit_importable():
    import clikit

    assert hasattr(clikit, "AppConfig")
    assert hasattr(clikit, "HttpClient")
    assert hasattr(clikit, "build_root_app")


def test_clikit_version_present():
    import clikit

    assert isinstance(clikit.__version__, str)
