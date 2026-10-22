"""Running without a password: open on this computer, closed to everything else."""

from __future__ import annotations

import httpx2
import pytest
from fastapi.testclient import TestClient

from magpie.api import is_local_host
from magpie.app import create_app
from magpie.config import Settings
from magpie.core import Magpie


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000", "magpie.localhost", "0.0.0.0"],
)
def test_names_for_this_computer(host):
    assert is_local_host(host)


@pytest.mark.parametrize(
    "host",
    ["evil.example", "localhost.evil.example", "192.168.1.5:8000", "notes.example.com", ""],
)
def test_names_for_anywhere_else(host):
    assert not is_local_host(host)


@pytest.fixture
def magpie(tmp_path, router):
    settings = Settings(_env_file=None, data_dir=tmp_path, brain="fake", login=False)
    return Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))


@pytest.fixture
def local(magpie):
    app = create_app(magpie.settings, magpie=magpie, background=False)
    with TestClient(app, base_url="http://localhost:8000") as http:
        yield http


def test_no_password_opens_straight_away_on_this_computer(local):
    session = local.get("/api/session").json()
    assert session["authenticated"] is True and session["login"] is False
    assert local.get("/api/status").json()["login"] is False
    note = local.post("/api/notes", json={"text": "buy milk"}, headers={"X-Magpie": "1"})
    assert note.status_code == 201


def test_other_sites_still_cannot_post_on_your_behalf(local):
    refused = local.post("/api/notes", json={"text": "buy milk"})
    assert refused.status_code == 403 and "X-Magpie" in refused.json()["detail"]


def test_a_rebinding_site_is_turned_away(local):
    """A page whose own domain points at 127.0.0.1 still sends its own name as the Host."""
    headers = {"Host": "evil.example", "X-Magpie": "1"}
    refused = local.get("/api/status", headers=headers)
    assert refused.status_code == 403 and "only opens on the computer" in refused.json()["detail"]
    assert local.post("/api/notes", json={"text": "x"}, headers=headers).status_code == 403
    session = local.get("/api/session", headers=headers).json()
    assert session["authenticated"] is False and "localhost" in session["message"]


def test_the_api_token_works_from_anywhere(local, magpie):
    headers = {"Host": "notes.example.com", "Authorization": f"Bearer {magpie.secrets.api_token}"}
    assert local.get("/api/status", headers=headers).status_code == 200


def test_no_password_is_made_up(magpie):
    assert magpie.secrets.password_generated is False
    assert magpie.store.get_kv("generated_password") is None


def test_logging_in_is_not_needed(local):
    assert local.post("/api/login", json={"password": "anything"}).json() == {"authenticated": True}


def test_a_password_is_still_required_by_default(tmp_path, router):
    settings = Settings(_env_file=None, data_dir=tmp_path, brain="fake")
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    with TestClient(create_app(settings, magpie=magpie, background=False)) as http:
        assert http.get("/api/status").status_code == 401
        assert http.get("/api/session").json()["login"] is True
