from unittest.mock import AsyncMock, Mock

import pytest

from s3_mcp_server.resources.s3_resource import S3Resource


class AsyncByteStream:
    def __init__(self, chunks: list[bytes]):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _client_context(client):
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = False
    return context


def test_client_kwargs_include_s3_compat_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("S3_SESSION_TOKEN", "token")

    resource = S3Resource(
        region_name="us-east-1",
        endpoint_url="http://localhost:9000",
        addressing_style="path",
        signature_version="s3v4",
        verify_ssl=False,
    )

    kwargs = resource._client_kwargs()

    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["endpoint_url"] == "http://localhost:9000"
    assert kwargs["verify"] is False
    assert kwargs["aws_access_key_id"] == "access"
    assert kwargs["aws_secret_access_key"] == "secret"
    assert kwargs["aws_session_token"] == "token"
    assert resource.config.s3["addressing_style"] == "path"
    assert resource.config.signature_version == "s3v4"


def test_invalid_addressing_style_falls_back_to_auto():
    resource = S3Resource(addressing_style="invalid-style")
    assert resource.config.s3["addressing_style"] == "auto"


@pytest.mark.asyncio
async def test_list_objects_returns_empty_for_unconfigured_bucket():
    resource = S3Resource()
    resource.configured_buckets = ["allowed-bucket"]
    resource.session.client = Mock(
        side_effect=AssertionError("client should not be used")
    )

    result = await resource.list_objects("other-bucket")

    assert result == []


@pytest.mark.asyncio
async def test_list_buckets_filters_configured_buckets_and_start_after():
    resource = S3Resource(max_buckets=5)
    resource.configured_buckets = ["bucket-a", "bucket-c", "bucket-d"]
    resource.session.client = Mock(
        side_effect=AssertionError("list_buckets should not call remote API")
    )

    result = await resource.list_buckets(start_after="bucket-c")

    assert result == [{"Name": "bucket-d"}]


def test_constructor_accepts_configured_bucket_allow_list():
    resource = S3Resource(configured_buckets=["bucket-a", " bucket-b ", "bucket-a", ""])

    assert resource.configured_buckets == ["bucket-a", "bucket-b"]


@pytest.mark.asyncio
async def test_list_buckets_applies_max_bucket_limit_without_filtering():
    resource = S3Resource(max_buckets=2)
    resource.configured_buckets = []

    s3_client = AsyncMock()
    s3_client.list_buckets = AsyncMock(
        return_value={
            "Buckets": [
                {"Name": "bucket-a"},
                {"Name": "bucket-b"},
                {"Name": "bucket-c"},
            ]
        }
    )
    resource.session.client = Mock(return_value=_client_context(s3_client))

    result = await resource.list_buckets()

    assert result == [{"Name": "bucket-a"}, {"Name": "bucket-b"}]


@pytest.mark.asyncio
async def test_list_objects_returns_contents_from_s3():
    resource = S3Resource()

    s3_client = AsyncMock()
    s3_client.list_objects_v2 = AsyncMock(
        return_value={"Contents": [{"Key": "path/file.txt"}]}
    )
    resource.session.client = Mock(return_value=_client_context(s3_client))

    result = await resource.list_objects("bucket-a", prefix="path/", max_keys=10)

    assert result == [{"Key": "path/file.txt"}]


@pytest.mark.asyncio
async def test_get_object_raises_no_such_key_without_retry():
    resource = S3Resource()

    s3_client = AsyncMock()
    s3_client.get_object = AsyncMock(side_effect=Exception("NoSuchKey"))
    resource.session.client = Mock(return_value=_client_context(s3_client))

    with pytest.raises(Exception, match="NoSuchKey"):
        await resource.get_object("bucket-a", "missing.txt", max_retries=3)

    assert s3_client.get_object.await_count == 1


@pytest.mark.asyncio
async def test_get_object_retries_and_concatenates_stream(
    monkeypatch: pytest.MonkeyPatch,
):
    resource = S3Resource()
    resource.configured_buckets = ["allowed-bucket"]

    s3_client = AsyncMock()
    s3_client.get_object = AsyncMock(
        side_effect=[
            Exception("temporary error"),
            {"Body": AsyncByteStream([b"abc", b"def"])},
        ]
    )
    resource.session.client = Mock(return_value=_client_context(s3_client))

    sleep_mock = AsyncMock()
    monkeypatch.setattr("s3_mcp_server.resources.s3_resource.asyncio.sleep", sleep_mock)

    response = await resource.get_object(
        "allowed-bucket", "path/file.txt", max_retries=2
    )

    assert response["Body"] == b"abcdef"
    assert s3_client.get_object.await_count == 2
    sleep_mock.assert_awaited_once()


def test_is_text_file_detects_known_extensions():
    resource = S3Resource()

    assert resource.is_text_file("notes/readme.md") is True
    assert resource.is_text_file("images/photo.jpg") is False
