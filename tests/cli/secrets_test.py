"""Tests for the secrets command-line subcommand."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from click.testing import CliRunner
from cryptography.fernet import Fernet
from phalanx.cli import main
from phalanx.factory import Factory
from phalanx.models.gafaelfawr import Token
from safir.datetime import current_datetime

from ..support.data import (
    phalanx_test_path,
    read_input_static_secrets,
    read_output_data,
)
from ..support.vault import MockVaultClient


def _get_app_secret(mock_vault: MockVaultClient, path: str) -> dict[str, str]:
    """Get an application secret from the mock Vault.

    Parameters
    ----------
    mock_vault
        Mock Vault client.
    path
        Path within Vault to the application.

    Returns
    -------
    dict of str
        Key and value pairs for that application.
    """
    result = mock_vault.read_secret(path, raise_on_deleted_version=True)
    return result["data"]["data"]


def test_audit(mock_vault: MockVaultClient) -> None:
    input_path = phalanx_test_path()
    os.chdir(str(input_path))
    input_path / "vault" / "idfdev"
    factory = Factory()
    config_storage = factory.create_config_storage()
    environment = config_storage.load_environment("idfdev")
    mock_vault.load_test_data(environment.vault_path_prefix, "idfdev")

    secrets_path = input_path / "secrets" / "idfdev.yaml"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["secrets", "audit", "--secrets", str(secrets_path), "idfdev"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert result.output == read_output_data("idfdev", "secrets-audit")


def test_list() -> None:
    input_path = phalanx_test_path()
    os.chdir(str(input_path))
    runner = CliRunner()
    result = runner.invoke(
        main, ["secrets", "list", "idfdev"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert result.output == read_output_data("idfdev", "secrets-list")


def test_schema() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["secrets", "schema"], catch_exceptions=False)
    assert result.exit_code == 0
    current = (
        Path(__file__).parent.parent.parent
        / "docs"
        / "extras"
        / "schemas"
        / "secrets.json"
    )
    assert result.output == current.read_text()


def test_static_template() -> None:
    input_path = phalanx_test_path()
    os.chdir(str(input_path))
    runner = CliRunner()
    result = runner.invoke(
        main, ["secrets", "static-template", "idfdev"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert result.output == read_output_data("idfdev", "static-secrets.yaml")


def test_sync(mock_vault: MockVaultClient) -> None:
    input_path = phalanx_test_path()
    secrets_path = input_path / "secrets" / "idfdev.yaml"
    os.chdir(str(input_path))
    factory = Factory()
    config_storage = factory.create_config_storage()
    environment = config_storage.load_environment("idfdev")
    mock_vault.load_test_data(environment.vault_path_prefix, "idfdev")
    _, base_vault_path = environment.vault_path_prefix.split("/", 1)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["secrets", "sync", "--secrets", str(secrets_path), "idfdev"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert result.output == read_output_data("idfdev", "sync-output")

    # Check that all static secrets were copied over correctly.
    static_secrets = read_input_static_secrets("idfdev")
    for application, values in static_secrets.items():
        vault = _get_app_secret(mock_vault, f"{base_vault_path}/{application}")
        for key, value in values.items():
            if key in vault:
                assert value == vault[key]

    # Check generated or copied secrets.
    argocd = _get_app_secret(mock_vault, f"{base_vault_path}/argocd")
    assert re.match("^[0-9a-f]{64}$", argocd["server.secretkey"])
    gafaelfawr = _get_app_secret(mock_vault, f"{base_vault_path}/gafaelfawr")
    assert Fernet(gafaelfawr["session-secret"].encode())
    assert "-----BEGIN PRIVATE KEY-----" in gafaelfawr["signing-key"]
    webhook = static_secrets["mobu"]["app-alert-webhook"]
    assert gafaelfawr["slack-webhook"] == webhook
    nublado = _get_app_secret(mock_vault, f"{base_vault_path}/nublado")
    assert re.match("^[0-9a-f]{64}$", nublado["proxy_token"])
    postgres = _get_app_secret(mock_vault, f"{base_vault_path}/postgres")
    assert postgres["nublado3_password"] == nublado["hub_db_password"]

    # Now sync again with --delete. The only change should be that the stray
    # Gafaelfawr Vault secret key is deleted.
    result = runner.invoke(
        main,
        [
            "secrets",
            "sync",
            "--delete",
            "--secrets",
            str(secrets_path),
            "idfdev",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert result.output == read_output_data("idfdev", "sync-delete-output")
    after = _get_app_secret(mock_vault, f"{base_vault_path}/gafaelfawr")
    assert "cilogon" in gafaelfawr
    assert "cilogon" not in after
    del gafaelfawr["cilogon"]
    assert after == gafaelfawr


def test_sync_regenerate(mock_vault: MockVaultClient) -> None:
    input_path = phalanx_test_path()
    secrets_path = input_path / "secrets" / "idfdev.yaml"
    os.chdir(str(input_path))
    factory = Factory()
    config_storage = factory.create_config_storage()
    environment = config_storage.load_environment("idfdev")
    mock_vault.load_test_data(environment.vault_path_prefix, "idfdev")
    _, base_vault_path = environment.vault_path_prefix.split("/", 1)
    before = _get_app_secret(mock_vault, f"{base_vault_path}/gafaelfawr")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "secrets",
            "sync",
            "--secrets",
            str(secrets_path),
            "--regenerate",
            "idfdev",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    expected = read_output_data("idfdev", "sync-regenerate-output")
    assert result.output == expected

    # Check that all static secrets were copied over correctly.
    static_secrets = read_input_static_secrets("idfdev")
    for application, values in static_secrets.items():
        vault = _get_app_secret(mock_vault, f"{base_vault_path}/{application}")
        for key, value in values.items():
            if key in vault:
                assert value == vault[key]

    # Don't recheck all the details that match the regular sync test. Just
    # check that some secrets that would have been left alone without
    # --regenerate were regenerated and are syntactically valid.
    after = _get_app_secret(mock_vault, f"{base_vault_path}/gafaelfawr")
    assert Token.from_str(after["bootstrap-token"])
    assert before["bootstrap-token"] != after["bootstrap-token"]
    assert re.match("^[0-9a-f]{64}$", after["redis-password"])
    assert before["redis-password"] != after["redis-password"]

    # Check whether bcrypt passwords are generated properly.
    argocd = _get_app_secret(mock_vault, f"{base_vault_path}/argocd")
    password = argocd["admin.plaintext_password"].encode()
    assert bcrypt.checkpw(password, argocd["admin.password"].encode())

    # Check whether mtime secrets are generated properly.
    mtime = datetime.fromisoformat(argocd["admin.passwordMtime"])
    now = current_datetime()
    assert now - timedelta(seconds=5) <= mtime <= now


def test_vault_secrets(tmp_path: Path, mock_vault: MockVaultClient) -> None:
    input_path = phalanx_test_path()
    vault_input_path = input_path / "vault" / "idfdev"
    os.chdir(str(input_path))
    factory = Factory()
    config_storage = factory.create_config_storage()
    environment = config_storage.load_environment("idfdev")
    mock_vault.load_test_data(environment.vault_path_prefix, "idfdev")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["secrets", "vault-secrets", "idfdev", str(tmp_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert result.output == ""

    expected_files = {p.name for p in vault_input_path.iterdir()}
    output_files = {p.name for p in tmp_path.iterdir()}
    assert expected_files == output_files
    for expected_path in vault_input_path.iterdir():
        with expected_path.open() as fh:
            expected = json.load(fh)
        with (tmp_path / expected_path.name).open() as fh:
            assert expected == json.load(fh)