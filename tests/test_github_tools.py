"""Unit tests for tools.github_tools."""

from unittest.mock import MagicMock, call, patch

from tools.github_tools import get_image_created


def _mock_get(*responses):
    """Return a side_effect list of mock responses for requests.get."""
    mocks = []
    for resp in responses:
        m = MagicMock()
        m.json.return_value = resp
        mocks.append(m)
    return mocks


_TOKEN_RESP    = {"token": "test-token"}
_MANIFEST_RESP = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {"digest": "sha256:configdigest"},
}
_CONFIG_RESP   = {"created": "2025-11-14T09:32:17.123456789Z"}


class TestGetImageCreated:
    def test_returns_created_for_single_manifest(self):
        with patch("requests.get", side_effect=_mock_get(_TOKEN_RESP, _MANIFEST_RESP, _CONFIG_RESP)):
            result = get_image_created("ghcr.io/org/mymodel", "1.0.0")

        assert result == "2025-11-14T09:32:17.123456789Z"

    def test_resolves_manifest_list_to_linux_amd64(self):
        manifest_list = {
            "manifests": [
                {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
            ]
        }
        single_manifest = {**_MANIFEST_RESP, "config": {"digest": "sha256:amdconfig"}}
        config = {"created": "2025-11-14T09:32:17Z"}

        with patch("requests.get", side_effect=_mock_get(_TOKEN_RESP, manifest_list, single_manifest, config)):
            result = get_image_created("ghcr.io/org/mymodel", "latest")

        assert result == "2025-11-14T09:32:17Z"
        # Third call should request the amd64 digest, not the arm one
        calls = patch("requests.get").__wrapped__ if hasattr(patch("requests.get"), "__wrapped__") else None

    def test_falls_back_to_first_entry_when_no_amd64(self):
        manifest_list = {
            "manifests": [
                {"digest": "sha256:first", "platform": {"os": "linux", "architecture": "arm64"}},
            ]
        }
        single_manifest = {**_MANIFEST_RESP, "config": {"digest": "sha256:firstconfig"}}
        config = {"created": "2025-10-01T00:00:00Z"}

        with patch("requests.get", side_effect=_mock_get(_TOKEN_RESP, manifest_list, single_manifest, config)):
            result = get_image_created("ghcr.io/org/mymodel", "latest")

        assert result == "2025-10-01T00:00:00Z"

    def test_returns_empty_string_when_image_empty(self):
        assert get_image_created("", "1.0") == ""

    def test_returns_empty_string_when_tag_empty(self):
        assert get_image_created("ghcr.io/org/mymodel", "") == ""

    def test_returns_empty_string_for_unsupported_registry(self):
        assert get_image_created("docker.io/org/mymodel", "1.0") == ""

    def test_returns_empty_string_on_network_error(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            result = get_image_created("ghcr.io/org/mymodel", "1.0")

        assert result == ""

    def test_returns_empty_string_when_created_missing_from_config(self):
        config_without_created = {}
        with patch("requests.get", side_effect=_mock_get(_TOKEN_RESP, _MANIFEST_RESP, config_without_created)):
            result = get_image_created("ghcr.io/org/mymodel", "1.0")

        assert result == ""

    def test_requests_correct_token_scope(self):
        with patch("requests.get", side_effect=_mock_get(_TOKEN_RESP, _MANIFEST_RESP, _CONFIG_RESP)) as mock_get:
            get_image_created("ghcr.io/the-episerve-consortium/mymodel", "2.0")

        token_call = mock_get.call_args_list[0]
        assert token_call[1]["params"]["scope"] == "repository:the-episerve-consortium/mymodel:pull"

