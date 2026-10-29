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


USER = "00000000-0000-0000-0000-000000000002"


def personal_client(setup):
    config, _, _, _ = setup
    config.update(scope="personal", expected_user_id=USER)
    client = module.destination_for(config, "FAKE")
    client.metadata = Mock()
    return config, client


def test_personal_adapter_explicit_scope_and_user_bound_journal(setup):
    config, client = personal_client(setup)
    assert isinstance(client, module.PersonalOpenHands)
    assert client.url == config["base_url"] + "/api/v1/secrets"
    assert client.create_payload("CANARY", VALUE) == {
        "name": "CANARY",
        "value": VALUE,
        "scope": "personal",
    }
    mapping = config["mappings"][0]
    alice = module.mapping_key(config, mapping)
    config["expected_user_id"] = "00000000-0000-0000-0000-000000000003"
    assert module.mapping_key(config, mapping) != alice
    config.pop("scope")
    assert module.mapping_key(config, mapping) != alice


def test_personal_metadata_pagination_keeps_shared_names_to_avoid_shadowing(setup):
    config, client = personal_client(setup)
    client.metadata.side_effect = [
        {"org_id": config["org_id"], "bound_org_id": config["org_id"], "user_id": USER},
        {
            "items": [{"name": "PERSONAL", "scope": "personal"}],
            "next_page_id": "cursor&next",
        },
        {"items": [{"name": "SHARED", "scope": "organization"}], "next_page_id": None},
    ]
    assert client.names() == {"PERSONAL", "SHARED"}
    assert "page_id=cursor%26next" in client.metadata.call_args.args[0]


@pytest.mark.parametrize("bad_identity", ["user", "bound", "effective"])
def test_personal_identity_mismatch_stops_before_source_reads(setup, bad_identity):
    config, source, _, journal = setup
    _, client = personal_client(setup)
    identity = {
        "org_id": config["org_id"],
        "bound_org_id": config["org_id"],
        "user_id": USER,
    }
    identity[
        {"user": "user_id", "bound": "bound_org_id", "effective": "org_id"}[
            bad_identity
        ]
    ] = "wrong"
    client.metadata.return_value = identity
    with pytest.raises(module.SyncError):
        module.sync(config, lambda _region: source, client, journal, True)
    source.describe_secret.assert_not_called()
    source.get_secret_value.assert_not_called()
    assert client.metadata.call_count == 1


@pytest.mark.parametrize(
    "page",
    [
        {"items": [{"name": "CANARY"}]},
        {"items": [{"name": "CANARY", "scope": "invalid"}]},
        {"items": [], "next_page_id": ""},
        {"items": [], "next_page_id": 123},
    ],
)
def test_personal_listing_fails_closed(setup, page):
    config, client = personal_client(setup)
    client.metadata.side_effect = [
        {"org_id": config["org_id"], "bound_org_id": config["org_id"], "user_id": USER},
        page,
    ]
    with pytest.raises(module.SyncError):
        client.names()


def test_personal_repeated_cursor_refused(setup):
    config, client = personal_client(setup)
    client.metadata.side_effect = [
        {"org_id": config["org_id"], "bound_org_id": config["org_id"], "user_id": USER},
        {"items": [], "next_page_id": "repeat"},
        {"items": [], "next_page_id": "repeat"},
    ]
    with pytest.raises(module.SyncError, match="cursor"):
        client.names()


@pytest.mark.parametrize("change", ["missing_user", "invalid_user", "invalid_scope"])
def test_personal_config_requires_explicit_identity(setup, tmp_path, change):
    config, _ = personal_client(setup)
    if change == "missing_user":
        config.pop("expected_user_id")
    elif change == "invalid_user":
        config["expected_user_id"] = None
    else:
        config["scope"] = "all-users"
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(config))
    with pytest.raises(module.SyncError):
        module.load_config(p)


def test_existing_org_checkpoint_identity_preserved(setup):
    config, _, _, _ = setup
    previous = module.mapping_key(config, config["mappings"][0])
    config["scope"] = "organization"
    assert module.mapping_key(config, config["mappings"][0]) == previous


def test_personal_shared_name_cannot_inherit_personal_checkpoint(setup):
    config, source, _, journal = setup
    _, client = personal_client(setup)
    journal.record(module.mapping_key(config, config["mappings"][0]), "v1", "applied")
    client.metadata.side_effect = [
        {"org_id": config["org_id"], "bound_org_id": config["org_id"], "user_id": USER},
        {"items": [{"name": "CANARY", "scope": "organization"}], "next_page_id": None},
    ]
    with pytest.raises(module.SyncError, match="collides"):
        module.sync(config, lambda _region: source, client, journal, True)
    source.describe_secret.assert_not_called()


def test_personal_create_posts_explicit_scope_without_user_override(setup):
    _, client = personal_client(setup)
    client.session = Mock()
    client.session.post.return_value.status_code = 201
    client.create("CANARY", VALUE)
    body = client.session.post.call_args.kwargs["json"]
    assert body == {"name": "CANARY", "value": VALUE, "scope": "personal"}
    assert client.session.post.call_args.kwargs["allow_redirects"] is False


def rotation_setup(setup, personal=False):
    config, source, dest, journal = setup
    if personal:
        config.update(
            scope="personal", expected_user_id="00000000-0000-0000-0000-000000000002"
        )
    assert run(setup) == 0
    dest.names.return_value = {"CANARY"}
    source.describe_secret.return_value["VersionIdsToStages"] = {"v2": ["AWSCURRENT"]}
    source.get_secret_value.return_value.update(
        VersionId="v2", SecretString=json.dumps({"token": VALUE + "-2"})
    )
    source.get_secret_value.reset_mock()
    dest.create.reset_mock()
    return config, source, dest, journal


def rotate(setup, apply=True, allow=True):
    config, source, dest, journal = setup
    return module.sync(config, lambda _: source, dest, journal, apply, True, allow)


def test_personal_rotation_upserts_without_delete(setup):
    config, source, dest, journal = rotation_setup(setup, personal=True)
    assert rotate(setup, allow=False) == 0
    dest.create.assert_called_once_with("CANARY", VALUE + "-2")
    dest.delete.assert_not_called()
    assert journal.get(module.mapping_key(config, config["mappings"][0])) == (
        "v2",
        "applied",
    )


def test_org_rotation_requires_opt_in_before_read(setup):
    _, source, dest, _ = rotation_setup(setup)
    assert rotate(setup, allow=False) == 1
    source.get_secret_value.assert_not_called()
    dest.delete.assert_not_called()


def test_rotation_dry_run_never_reads_or_mutates(setup):
    _, source, dest, _ = rotation_setup(setup)
    assert rotate(setup, apply=False) == 0
    source.get_secret_value.assert_not_called()
    dest.delete.assert_not_called()
    dest.create.assert_not_called()


def test_org_rotation_order_and_durable_phases(setup):
    config, source, dest, journal = rotation_setup(setup)
    key = module.mapping_key(config, config["mappings"][0])

    def delete(name):
        assert name == "CANARY"
        assert source.get_secret_value.call_count == 1
        assert journal.get(key) == ("v2", "delete_pending")

    def create(name, value):
        assert name == "CANARY" and value == VALUE + "-2"
        assert dest.delete.call_count == 1
        assert journal.get(key) == ("v2", "pending")

    dest.delete.side_effect = delete
    dest.create.side_effect = create
    assert rotate(setup) == 0
    assert journal.get(key) == ("v2", "applied")


def test_invalid_rotated_value_does_not_delete(setup):
    _, source, dest, _ = rotation_setup(setup)
    source.get_secret_value.return_value["SecretString"] = "{}"
    assert rotate(setup) == 1
    dest.delete.assert_not_called()


@pytest.mark.parametrize("phase", ["pending", "delete_pending"])
def test_uncertain_rotation_never_retries_even_when_name_absent(setup, phase):
    config, source, dest, journal = rotation_setup(setup)
    journal.record(module.mapping_key(config, config["mappings"][0]), "v2", phase)
    dest.names.return_value = set()
    assert rotate(setup) == 1
    source.get_secret_value.assert_not_called()
    dest.create.assert_not_called()
    dest.delete.assert_not_called()


def test_deleted_checkpoint_recovers_latest_version_without_second_delete(setup):
    config, _, dest, journal = rotation_setup(setup)
    key = module.mapping_key(config, config["mappings"][0])
    journal.record(key, "earlier-version", "deleted")
    dest.names.return_value = set()
    assert rotate(setup) == 0
    dest.delete.assert_not_called()
    dest.create.assert_called_once_with("CANARY", VALUE + "-2")
    assert journal.get(key) == ("v2", "applied")


def test_deleted_checkpoint_conflict_blocks_writes(setup):
    config, source, dest, journal = rotation_setup(setup)
    journal.record(module.mapping_key(config, config["mappings"][0]), "v2", "deleted")
    assert rotate(setup) == 1
    source.get_secret_value.assert_not_called()
    dest.delete.assert_not_called()
    dest.create.assert_not_called()


def test_unknown_existing_still_refused_with_rotation_enabled(setup):
    _, source, dest, _ = setup
    dest.names.return_value = {"CANARY"}
    assert rotate(setup) == 1
    source.get_secret_value.assert_not_called()
    dest.delete.assert_not_called()


def test_ambiguous_delete_persists_and_blocks(setup):
    config, _, dest, journal = rotation_setup(setup)
    dest.delete.side_effect = module.SyncError("Delete outcome uncertain")
    assert rotate(setup) == 1
    assert journal.get(module.mapping_key(config, config["mappings"][0])) == (
        "v2",
        "delete_pending",
    )
    dest.names.return_value = set()
    assert rotate(setup) == 1
    assert dest.delete.call_count == 1
    dest.create.assert_not_called()


def test_delete_transport_no_retry_or_response_body(setup):
    config, _, _, _ = setup
    destination = module.OpenHands(config, "FAKE")
    destination.session = Mock()
    destination.session.delete.side_effect = requests.Timeout(VALUE)
    with pytest.raises(module.SyncError) as exc:
        destination.delete("CANARY")
    assert VALUE not in str(exc.value)
    destination.session.delete.assert_called_once_with(
        destination.url + "/CANARY", timeout=(10, 30), allow_redirects=False
    )


def test_personal_search_suffix_is_not_a_cross_scope_uniqueness_guarantee(setup):
    config, source, _, journal = rotation_setup(setup, personal=True)
    client = module.PersonalOpenHands(config, "FAKE")
    client.metadata = Mock(
        side_effect=[
            {
                "org_id": config["org_id"],
                "bound_org_id": config["org_id"],
                "user_id": config["expected_user_id"],
            },
            {
                "items": [
                    {"name": "CANARY", "scope": "personal"},
                    {"name": "CANARY_2", "scope": "organization"},
                ],
                "next_page_id": None,
            },
        ]
    )
    client.session = Mock()
    client.session.post.return_value.status_code = 201
    # The platform can suffix a shared name added after the personal import.
    # We see only its effective name, so cannot detect the stored-name collision.
    assert module.sync(config, lambda _: source, client, journal, True, True) == 0
    assert client.session.post.call_args.kwargs["json"] == {
        "name": "CANARY",
        "value": VALUE + "-2",
        "scope": "personal",
    }
    client.session.delete.assert_not_called()


@pytest.mark.parametrize("outcome", ["rejected", "timeout_absent", "timeout_committed"])
def test_failed_post_after_shared_delete_blocks_without_retry(setup, outcome, capsys):
    config, source, _, journal = rotation_setup(setup)
    stored = {"CANARY": VALUE}
    client = module.OpenHands(config, "FAKE")
    client.names = Mock(side_effect=lambda: set(stored))
    client.session = Mock()

    def delete(*_args, **_kwargs):
        stored.pop("CANARY")
        return Mock(status_code=204)

    def post(*_args, **kwargs):
        if outcome == "rejected":
            return Mock(status_code=403)
        if outcome == "timeout_committed":
            stored["CANARY"] = kwargs["json"]["value"]
        raise requests.Timeout(VALUE)

    client.session.delete.side_effect = delete
    client.session.post.side_effect = post
    assert module.sync(config, lambda _: source, client, journal, True, True, True) == 1
    key = module.mapping_key(config, config["mappings"][0])
    assert journal.get(key) == ("v2", "pending")
    # A lost response may hide a commit. Both presence and absence must block.
    assert ("CANARY" in stored) == (outcome == "timeout_committed")
    if stored:
        assert stored["CANARY"] == VALUE + "-2"
    source.get_secret_value.reset_mock()
    assert module.sync(config, lambda _: source, client, journal, True, True, True) == 1
    source.get_secret_value.assert_not_called()
    assert client.session.delete.call_count == 1
    assert client.session.post.call_count == 1
    assert VALUE not in str(capsys.readouterr())


@pytest.mark.parametrize("directory_mode", [0o700, 0o750, 0o704])
def test_journal_requires_private_directory_before_creating_state(
    tmp_path, directory_mode
):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    directory.chmod(directory_mode)
    path = directory / "journal.sqlite"
    if directory_mode != 0o700:
        with pytest.raises(module.SyncError, match="State directory must be private"):
            module.Journal(path)
        assert not path.exists()
        return
    journal = module.Journal(path)
    try:
        assert directory.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        journal.close()
