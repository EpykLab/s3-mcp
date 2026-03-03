import pytest
import base64
import json

from s3_mcp_server import server as server_module


class StubS3Resource:
    def __init__(self):
        self.list_buckets_calls: list[str | None] = []
        self.list_objects_calls: list[dict] = []
        self.get_object_calls: list[dict] = []

    async def list_buckets(self, start_after=None):
        self.list_buckets_calls.append(start_after)
        return [{"Name": "bucket-a"}]

    async def list_objects(self, **kwargs):
        self.list_objects_calls.append(kwargs)
        return [{"Key": "docs/file.txt"}]

    async def get_object(self, **kwargs):
        self.get_object_calls.append(kwargs)
        return {"Body": b"hello world"}


class ResourceListStub:
    def __init__(self):
        self.configured_buckets = []
        self.max_buckets = 5

    async def list_buckets(self, start_after=None):
        return [{"Name": "bucket-a"}]

    async def list_objects(self, bucket_name, prefix="", max_keys=1000):
        return [{"Key": "docs/file.txt"}, {"Key": "docs/"}]

    def is_text_file(self, key: str):
        return key.endswith(".txt")


class ReadResourceStub:
    def __init__(self, payload: bytes = b"hello"):
        self.payload = payload
        self.list_calls = []

    async def get_object(self, bucket_name, key):
        return {"Body": self.payload, "ContentType": "text/plain"}

    async def list_objects(self, bucket_name, prefix=""):
        self.list_calls.append({"bucket_name": bucket_name, "prefix": prefix})
        return [{"Key": "docs/other.txt"}]

    def is_text_file(self, key: str):
        return key.endswith(".txt")


def test_parse_bool_handles_default_and_truthy_values():
    assert server_module._parse_bool(None, True) is True
    assert server_module._parse_bool("yes", False) is True
    assert server_module._parse_bool("0", True) is False


def test_load_s3_config_file_supports_profile_toml(tmp_path):
    config_file = tmp_path / "s3.toml"
    config_file.write_text(
        """
[default]
access_key_id = "default-ak"

[minio]
endpoint_url = "http://localhost:9000"
access_key_id = "minio-ak"
secret_access_key = "minio-sk"
verify_ssl = false
""".strip(),
        encoding="utf-8",
    )

    parsed = server_module._load_s3_config_file(str(config_file), "minio")

    assert parsed["endpoint_url"] == "http://localhost:9000"
    assert parsed["access_key_id"] == "minio-ak"
    assert parsed["verify_ssl"] is False


def test_load_s3_config_file_supports_json_profiles_key(tmp_path):
    config_file = tmp_path / "s3.json"
    config_file.write_text(
        json.dumps(
            {
                "profiles": {
                    "default": {"region": "us-east-1"},
                    "r2": {
                        "endpoint_url": "https://example.r2.cloudflarestorage.com",
                        "access_key_id": "r2-ak",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    parsed = server_module._load_s3_config_file(str(config_file), "r2")

    assert parsed["endpoint_url"] == "https://example.r2.cloudflarestorage.com"
    assert parsed["access_key_id"] == "r2-ak"


def test_build_s3_resource_reads_credentials_from_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    config_file = tmp_path / "s3.toml"
    config_file.write_text(
        """
[default]
region = "us-east-1"
endpoint_url = "http://localhost:9000"
access_key_id = "file-ak"
secret_access_key = "file-sk"
buckets = ["bucket-a", "bucket-b"]
addressing_style = "path"
verify_ssl = false
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("S3_CONFIG_FILE", str(config_file))
    monkeypatch.delenv("S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)

    resource = server_module._build_s3_resource()

    assert resource.endpoint_url == "http://localhost:9000"
    assert resource.access_key_id == "file-ak"
    assert resource.secret_access_key == "file-sk"
    assert resource.configured_buckets == ["bucket-a", "bucket-b"]
    assert resource.config.s3["addressing_style"] == "path"
    assert resource.verify_ssl is False


def test_build_s3_resource_env_overrides_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    config_file = tmp_path / "s3.toml"
    config_file.write_text(
        """
[default]
access_key_id = "file-ak"
secret_access_key = "file-sk"
buckets = ["file-bucket"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("S3_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "env-ak")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "env-sk")
    monkeypatch.setenv("S3_BUCKETS", "env-bucket")

    resource = server_module._build_s3_resource()

    assert resource.access_key_id == "env-ak"
    assert resource.secret_access_key == "env-sk"
    assert resource.configured_buckets == ["env-bucket"]


def test_build_s3_resource_reads_indexed_env_bucket_list(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("S3_BUCKETS", raising=False)
    monkeypatch.setenv("S3_BUCKET_1", "bucket-a")
    monkeypatch.setenv("S3_BUCKET_2", "bucket-b")
    monkeypatch.delenv("S3_CONFIG_FILE", raising=False)

    resource = server_module._build_s3_resource()

    assert resource.configured_buckets == ["bucket-a", "bucket-b"]


@pytest.mark.asyncio
async def test_listbuckets_supports_start_after_alias(monkeypatch: pytest.MonkeyPatch):
    stub = StubS3Resource()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    result = await server_module.handle_call_tool(
        "ListBuckets", {"StartAfter": "bucket-1"}
    )

    assert stub.list_buckets_calls == ["bucket-1"]
    assert result[0].text == "[{'Name': 'bucket-a'}]"


@pytest.mark.asyncio
async def test_listobjectsv2_forwards_max_keys(monkeypatch: pytest.MonkeyPatch):
    stub = StubS3Resource()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    await server_module.handle_call_tool(
        "ListObjectsV2",
        {
            "bucket_name": "bucket-a",
            "prefix": "docs/",
            "max_keys": 50,
        },
    )

    assert stub.list_objects_calls == [
        {
            "bucket_name": "bucket-a",
            "prefix": "docs/",
            "max_keys": 50,
        }
    ]


@pytest.mark.asyncio
async def test_getobject_decodes_bytes_response(monkeypatch: pytest.MonkeyPatch):
    stub = StubS3Resource()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    result = await server_module.handle_call_tool(
        "GetObject",
        {
            "bucket_name": "bucket-a",
            "key": "docs/file.txt",
            "max_retries": 2,
        },
    )

    assert stub.get_object_calls == [
        {
            "bucket_name": "bucket-a",
            "key": "docs/file.txt",
            "max_retries": 2,
        }
    ]
    assert result[0].text == "hello world"


@pytest.mark.asyncio
async def test_getobject_tool_schema_uses_expected_required_fields():
    tools = await server_module.handle_list_tools()
    get_object_tool = next(tool for tool in tools if tool.name == "GetObject")
    assert get_object_tool.inputSchema["required"] == ["bucket_name", "key"]


@pytest.mark.asyncio
async def test_list_resources_builds_s3_uris(monkeypatch: pytest.MonkeyPatch):
    stub = ResourceListStub()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    resources = await server_module.list_resources()

    assert len(resources) == 1
    assert str(resources[0].uri) == "s3://bucket-a/docs/file.txt"
    assert resources[0].name == "docs/file.txt"
    assert resources[0].mimeType == "text/plain"


@pytest.mark.asyncio
async def test_read_resource_encodes_text_body_as_base64(
    monkeypatch: pytest.MonkeyPatch,
):
    stub = ReadResourceStub(payload=b"hello")
    monkeypatch.setattr(server_module, "s3_resource", stub)

    result = await server_module.read_resource("s3://bucket-a/docs/file.txt")

    assert result == base64.b64encode(b"hello").decode("utf-8")


@pytest.mark.asyncio
async def test_read_resource_rejects_non_s3_uri():
    with pytest.raises(ValueError, match="Invalid S3 URI"):
        await server_module.read_resource("https://example.com/file.txt")


@pytest.mark.asyncio
async def test_read_resource_requires_bucket_and_key():
    with pytest.raises(ValueError, match="Invalid S3 URI format"):
        await server_module.read_resource("s3://bucket-only")


@pytest.mark.asyncio
async def test_read_resource_wraps_no_such_key_errors(monkeypatch: pytest.MonkeyPatch):
    class FailingReadStub(ReadResourceStub):
        async def get_object(self, bucket_name, key):
            raise Exception("NoSuchKey")

    stub = FailingReadStub()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    with pytest.raises(ValueError, match="NoSuchKey"):
        await server_module.read_resource("s3://bucket-a/docs/missing.txt")

    assert stub.list_calls == [{"bucket_name": "bucket-a", "prefix": "docs"}]


@pytest.mark.asyncio
async def test_getobject_returns_string_for_non_bytes_body(
    monkeypatch: pytest.MonkeyPatch,
):
    class NonBytesObjectStub(StubS3Resource):
        async def get_object(self, **kwargs):
            self.get_object_calls.append(kwargs)
            return {"Body": {"raw": "value"}}

    stub = NonBytesObjectStub()
    monkeypatch.setattr(server_module, "s3_resource", stub)

    result = await server_module.handle_call_tool(
        "GetObject",
        {
            "bucket_name": "bucket-a",
            "key": "docs/file.txt",
        },
    )

    assert result[0].text == "{'raw': 'value'}"
