import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import AppConfiguration, Base, Folder, IPAddress, Project, User
from app.services.auth import hash_password
from app.services.settings import initialize


@pytest.fixture
def app_and_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    defaults = Settings(_env_file=None, app_env="local", session_cookie_secure=False,
                        app_name="Lab", interface_theme="green", project_table_default_page_size=50)
    with factory() as db:
        initialize(db, defaults)
        db.add_all(User(id=key, username=name, password_hash=hash_password(f"{name}-password"), is_admin=key == 3)
                   for key, name in enumerate(("reader1", "reader2", "admin"), start=1))
        db.add(Folder(id=1, name="Office"))
        db.add(Project(id=1, folder_id=1, name="Lab LAN", cidr="192.0.2.0/24"))
        db.add_all(IPAddress(project_id=1, ordinal=i, address=f"192.0.2.{i}", hostname=f"host-{i}")
                   for i in range(1, 121))
        db.commit()

    def database():
        with factory() as db:
            yield db

    monkeypatch.setattr("app.core.config._bootstrap_settings", lambda: defaults)
    monkeypatch.setattr("app.main.SessionLocal", factory)
    app = create_app()
    app.state.config_ready = True
    app.dependency_overrides[get_db] = database
    yield app, factory
    engine.dispose()


def csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return re.search(r'name="_csrf_token" value="([^"]+)"', response.text)[1]


def login(client, name):
    response = client.post("/login", data={
        "_csrf_token": csrf(client, "/login"), "username": name, "password": f"{name}-password",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    return csrf(client, "/settings")


def save(client, token, theme, language, size, **extra):
    return client.post("/settings/interface", data={
        "_csrf_token": token, "interface_theme": theme, "interface_language": language,
        "project_table_page_size": size, **extra,
    })


def test_account_preferences_are_isolated_and_persist(app_and_db):
    app, _ = app_and_db
    first = TestClient(app, follow_redirects=False)
    second = TestClient(app, follow_redirects=False)
    token = login(first, "reader1")
    login(second, "reader2")

    response = save(first, token, "dark", "EN", "100")
    assert response.status_code == 303
    saved_page = first.get(response.headers["location"]).text
    assert '<html lang="en" data-theme="dark">' in saved_page
    assert "Settings saved" in saved_page

    for client, html_attrs, rows in ((first, 'lang="en" data-theme="dark"', 100),
                                     (second, 'lang="ru" data-theme="green"', 50)):
        detail = client.get("/projects/1")
        assert detail.status_code == 200
        assert html_attrs in detail.text
        assert detail.text.count("data-row-address=") == rows
        assert f"per_page={rows}" in client.get("/search?q=host-115").text
    assert first.get("/projects/1?per_page=25").text.count("data-row-address=") == 25

    assert first.post("/logout", data={"_csrf_token": token}).status_code == 303
    token = login(first, "reader1")
    assert '<html lang="en" data-theme="dark">' in first.get("/").text

    response = save(first, token, "", "", "")
    assert response.status_code == 303
    assert "Настройки сохранены" in first.get(response.headers["location"]).text
    assert '<html lang="ru" data-theme="green">' in first.get("/").text
    assert first.get("/projects/1").text.count("data-row-address=") == 50


def test_settings_permissions_and_input_validation(app_and_db):
    app, factory = app_and_db
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get("/settings").headers["location"] == "/login"
    assert anonymous.post("/settings/interface").status_code == 403

    reader = TestClient(app, follow_redirects=False)
    token = login(reader, "reader1")
    page = reader.get("/settings").text
    assert 'href="/settings"' in page
    assert 'id="settings-interface"' in page
    for section in ("security", "ping", "limits", "integrations", "system"):
        assert f'id="settings-{section}"' not in page
    for path in ("security", "ping", "limits", "integrations/token/create"):
        assert reader.post(f"/settings/{path}", data={"_csrf_token": token}).status_code == 403

    assert save(reader, "bad", "dark", "EN", "25").status_code == 403
    for values in (("bogus", "EN", "25"), ("dark", "XX", "25"), ("dark", "EN", "33")):
        response = save(reader, token, *values)
        assert "error=" in response.headers["location"]
    with factory() as db:
        user = db.get(User, 1)
        assert (user.interface_theme, user.interface_language, user.project_table_page_size) == (None, None, None)

    admin = TestClient(app, follow_redirects=False)
    admin_token = login(admin, "admin")
    assert 'id="settings-security"' in admin.get("/settings").text
    assert 'name="app_name"' not in admin.get("/settings").text
    assert save(admin, admin_token, "blue", "RU", "25", app_name="Changed", user_id="1").status_code == 303
    with factory() as db:
        assert db.get(AppConfiguration, 1).app_name == "Lab"
        assert db.get(User, 1).interface_theme is None
        assert db.get(User, 3).interface_theme == "blue"
