"""Offline contract tests: no AWS or OpenHands credentials needed."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from botocore.exceptions import ClientError


spec = importlib.util.spec_from_file_location(
    "sync_secrets", Path(__file__).with_name("sync_secrets.py")
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-AbCdEf"
VALUE = "SYNTHETIC-DO-NOT-LOG"


@pytest.fixture
def setup(tmp_path):
    config = {
        "base_url": "https://openhands.example.com",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "mappings": [{"secret_arn": ARN, "name": "CANARY", "json_field": "token"}],
    }
    source = Mock()
    source.describe_secret.return_value = {
        "ARN": ARN,
        "VersionIdsToStages": {"v1": ["AWSCURRENT"]},
    }
    source.get_secret_value.return_value = {
        "ARN": ARN,
        "VersionId": "v1",
        "VersionStages": ["AWSCURRENT"],
        "SecretString": json.dumps({"token": VALUE}),
    }
    dest = Mock()
    dest.names.return_value = set()
    journal = module.Journal(tmp_path / "private" / "state.sqlite")
    yield config, source, dest, journal
    journal.close()


def run(setup, apply=True):
    config, source, dest, journal = setup
    return module.sync(config, lambda _region: source, dest, journal, apply)


def test_dry_run_never_reads_value_or_writes(setup):
    assert run(setup, apply=False) == 0
    _, source, dest, journal = setup
    source.get_secret_value.assert_not_called()
    dest.create.assert_not_called()
    assert journal.db.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0


def test_create_then_unchanged_checkpoint(setup, capsys):
    assert run(setup) == 0
    config, source, dest, journal = setup
    dest.create.assert_called_once_with("CANARY", VALUE)
    assert journal.get(module.mapping_key(config, config["mappings"][0])) == (
        "v1",
        "applied",
    )
    dest.names.return_value = {"CANARY"}
    source.get_secret_value.reset_mock()
    assert run(setup) == 0
    source.get_secret_value.assert_not_called()
    assert VALUE not in str(capsys.readouterr())
    assert (
        VALUE.encode()
        not in Path(
            journal.db.execute("PRAGMA database_list").fetchone()[2]
        ).read_bytes()
    )


def test_rotation_refused_without_reading_or_deleting(setup):
    assert run(setup) == 0
    _, source, dest, _ = setup
    dest.names.return_value = {"CANARY"}
    source.describe_secret.return_value["VersionIdsToStages"] = {"v2": ["AWSCURRENT"]}
    source.get_secret_value.reset_mock()
    assert run(setup) == 1
    source.get_secret_value.assert_not_called()
    assert dest.create.call_count == 1
    dest.delete.assert_not_called()


def test_unknown_existing_destination_is_never_adopted(setup):
    _, source, dest, _ = setup
    dest.names.return_value = {"CANARY"}
    assert run(setup) == 1
    source.get_secret_value.assert_not_called()
    dest.create.assert_not_called()


def test_uncertain_write_survives_restart_and_never_retries(setup):
    config, _, dest, journal = setup
    dest.create.side_effect = module.SyncError("Create outcome uncertain")
    assert run(setup) == 1
    assert journal.get(module.mapping_key(config, config["mappings"][0])) == (
        "v1",
        "pending",
    )
    assert run(setup) == 1
    assert dest.create.call_count == 1


@pytest.mark.parametrize("failure", ["source", "field", "binary", "race", "deletion"])
def test_source_failures_never_change_destination(setup, failure, capsys):
    config, source, dest, journal = setup
    if failure == "source":
        source.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": VALUE}},
            "DescribeSecret",
        )
    elif failure == "field":
        source.get_secret_value.return_value["SecretString"] = '{"other":"value"}'
    elif failure == "binary":
        source.get_secret_value.return_value.pop("SecretString")
    elif failure == "race":
        source.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequestException", "Message": VALUE}},
            "GetSecretValue",
        )
    else:
        source.describe_secret.return_value["DeletedDate"] = 123
    assert run(setup) == 1
    dest.create.assert_not_called()
    assert journal.get(module.mapping_key(config, config["mappings"][0])) is None
    assert VALUE not in str(capsys.readouterr())


def test_post_no_redirect_and_no_retry_even_on_timeout(setup):
    config, _, _, _ = setup
    client = module.OpenHands(config, "FAKE-KEY")
    client.session = Mock()
    client.session.post.side_effect = requests.Timeout(VALUE)
    with pytest.raises(module.SyncError, match="uncertain") as exc:
        client.create("CANARY", VALUE)
    assert VALUE not in str(exc.value)
    assert client.session.post.call_count == 1
    assert client.session.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [302, 401, 403, 409, 429, 500])
def test_non_created_response_does_not_acknowledge(status, setup):
    config, _, _, _ = setup
    client = module.OpenHands(config, "FAKE-KEY")
    client.session = Mock()
    client.session.post.return_value.status_code = status
    with pytest.raises(module.SyncError, match=f"HTTP {status}"):
        client.create("CANARY", VALUE)
    assert client.session.post.call_count == 1


def test_get_retries_transient_then_succeeds(setup, monkeypatch):
    config, _, _, _ = setup
    client = module.OpenHands(config, "FAKE-KEY")
    client.session = Mock()
    identity = Mock(status_code=200)
    identity.json.return_value = {
        "org_id": config["org_id"],
        "bound_org_id": config["org_id"],
    }
    good = Mock(status_code=200)
    good.json.return_value = {"items": [{"name": "CANARY"}]}
    client.session.get.side_effect = [Mock(status_code=503), identity, good]
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert client.names() == {"CANARY"}
    assert client.session.get.call_count == 3


def test_mapping_change_does_not_reuse_checkpoint(setup):
    config, _, _, _ = setup
    old = module.mapping_key(config, config["mappings"][0])
    config["mappings"][0]["json_field"] = "another"
    assert module.mapping_key(config, config["mappings"][0]) != old


@pytest.mark.parametrize("change", ["duplicate", "http", "invalid_name", "unknown"])
def test_config_validation(setup, tmp_path, change):
    config, _, _, _ = setup
    if change == "duplicate":
        config["mappings"] *= 2
    elif change == "http":
        config["base_url"] = "http://openhands.example.com"
    elif change == "invalid_name":
        config["mappings"][0]["name"] = "BAD-NAME"
    else:
        config["mappings"][0]["value"] = VALUE
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(module.SyncError):
        module.load_config(path)


@pytest.mark.parametrize("bound", [None, "another-org", "missing"])
def test_effective_org_does_not_prove_bound_key(setup, bound):
    config, _, _, _ = setup
    client = module.OpenHands(config, "FAKE")
    identity = {"org_id": config["org_id"]}
    if bound != "missing":
        identity["bound_org_id"] = bound
    client.metadata = Mock(return_value=identity)
    with pytest.raises(module.SyncError, match="bound"):
        client.names()
    assert client.metadata.call_count == 1


@pytest.mark.parametrize(
    "page",
    [
        {"items": [], "next_page_id": "more"},
        {"items": None},
        {"items": ["CANARY"]},
        {"items": [{"name": 3}]},
        {"items": [{"name": "CANARY"}, {"name": "CANARY"}]},
    ],
)
def test_incomplete_or_malformed_listing_refused(setup, page):
    config, _, _, _ = setup
    client = module.OpenHands(config, "FAKE")
    client.metadata = Mock(
        side_effect=[
            {"org_id": config["org_id"], "bound_org_id": config["org_id"]},
            page,
        ]
    )
    with pytest.raises(module.SyncError):
        client.names()


def test_partial_batch_recovers_without_rewriting_successes(setup):
    config, source, dest, journal = setup
    config["mappings"].append(
        {"secret_arn": ARN, "name": "SECOND", "json_field": "other"}
    )
    assert run(setup) == 1
    dest.create.assert_called_once_with("CANARY", VALUE)
    assert journal.get(module.mapping_key(config, config["mappings"][1])) is None
    source.get_secret_value.return_value["SecretString"] = json.dumps(
        {"token": VALUE, "other": "FAKE-SECOND"}
    )
    dest.names.return_value = {"CANARY"}
    assert run(setup) == 0
    assert dest.create.call_count == 2
    dest.create.assert_called_with("SECOND", "FAKE-SECOND")


def test_removing_mapping_never_deletes_destination(setup):
    assert run(setup) == 0
    config, _, dest, _ = setup
    config["mappings"] = []
    assert run(setup) == 0
    dest.delete.assert_not_called()


def test_wrong_returned_version_stage_refused(setup):
    _, source, dest, _ = setup
    source.get_secret_value.return_value["VersionStages"] = ["AWSPREVIOUS"]
    assert run(setup) == 1
    dest.create.assert_not_called()


@pytest.mark.parametrize("value", ["", 1, None, [], {}])
def test_invalid_json_field_types_never_write(setup, value):
    _, source, dest, _ = setup
    source.get_secret_value.return_value["SecretString"] = json.dumps({"token": value})
    assert run(setup) == 1
    dest.create.assert_not_called()


def test_multiple_regions_and_whole_strings(setup):
    config, source, dest, journal = setup
    second = ARN.replace("us-east-1", "eu-west-1")
    config["mappings"] = [
        {"secret_arn": ARN, "name": "WHOLE"},
        {"secret_arn": second, "name": "SECOND"},
    ]
    another = Mock()
    another.describe_secret.return_value = {
        "ARN": second,
        "VersionIdsToStages": {"v2": ["AWSCURRENT"]},
    }
    another.get_secret_value.return_value = {
        "ARN": second,
        "VersionId": "v2",
        "VersionStages": ["AWSCURRENT"],
        "SecretString": "FAKE-REGION2",
    }
    factory = Mock(
        side_effect=lambda region: {"us-east-1": source, "eu-west-1": another}[region]
    )
    assert module.sync(config, factory, dest, journal, True) == 0
    assert dest.create.call_count == 2
    assert {call.args[0] for call in factory.call_args_list} == {
        "us-east-1",
        "eu-west-1",
    }
