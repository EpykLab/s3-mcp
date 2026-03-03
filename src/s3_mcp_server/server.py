import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import tomllib
from typing import Any, Dict, List, Optional

from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
import mcp.server.stdio
from dotenv import load_dotenv
from mcp.types import (
    Resource,
    LoggingLevel,
    EmptyResult,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    BlobResourceContents,
    ReadResourceResult,
)

from .resources.s3_resource import S3Resource
from pydantic import AnyUrl

# Initialize server
server = Server("s3_service")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_s3_server")


def _parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False

    logger.warning("Invalid boolean value '%s'. Using default %s", value, default)
    return default


def _load_s3_config_file(config_path: str, profile: str = "default") -> Dict[str, Any]:
    path = Path(config_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"S3 config file not found: {path}")

    if path.suffix.lower() == ".json":
        parsed = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() in {".toml", ".tml"}:
        with path.open("rb") as file_handle:
            parsed = tomllib.load(file_handle)
    else:
        raise ValueError("Unsupported S3 config format. Use .json or .toml")

    if not isinstance(parsed, dict):
        raise ValueError("S3 config file must contain a JSON/TOML object")

    if profile in parsed and isinstance(parsed[profile], dict):
        return parsed[profile]

    profiles = parsed.get("profiles")
    if (
        isinstance(profiles, dict)
        and profile in profiles
        and isinstance(profiles[profile], dict)
    ):
        return profiles[profile]

    direct_config_keys = {
        "region",
        "profile_name",
        "endpoint_url",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "buckets",
        "bucket_names",
        "s3_buckets",
        "addressing_style",
        "signature_version",
        "verify_ssl",
        "max_buckets",
    }
    if any(key in parsed for key in direct_config_keys):
        if profile != "default":
            logger.warning(
                "Requested profile '%s' not found in config file. Falling back to top-level keys.",
                profile,
            )
        return parsed

    raise KeyError(f"Profile '{profile}' not found in S3 config file")


def _get_setting(
    env_keys: list[str],
    file_config: Dict[str, Any],
    file_key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    for env_key in env_keys:
        env_value = os.getenv(env_key)
        if env_value not in (None, ""):
            return env_value

    file_value = file_config.get(file_key)
    if file_value in (None, ""):
        return default

    if isinstance(file_value, str):
        return file_value

    return str(file_value)


def _get_int_setting(
    env_key: str,
    file_config: Dict[str, Any],
    file_key: str,
    default: int,
) -> int:
    env_value = os.getenv(env_key)
    if env_value not in (None, ""):
        try:
            return int(env_value)
        except ValueError:
            logger.warning(
                "Invalid integer value '%s' for %s. Using default %s.",
                env_value,
                env_key,
                default,
            )
            return default

    file_value = file_config.get(file_key)
    if file_value in (None, ""):
        return default

    try:
        return int(file_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid integer value '%s' for config key %s. Using default %s.",
            file_value,
            file_key,
            default,
        )
        return default


def _get_bool_setting(
    env_key: str,
    file_config: Dict[str, Any],
    file_key: str,
    default: bool,
) -> bool:
    env_value = os.getenv(env_key)
    if env_value is not None:
        return _parse_bool(env_value, default)

    file_value = file_config.get(file_key)
    return _parse_bool(file_value, default)


def _parse_bucket_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []

    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        logger.warning("Invalid bucket list value '%s'. Ignoring.", value)
        return []

    buckets: List[str] = []
    for candidate in candidates:
        bucket_name = str(candidate).strip()
        if not bucket_name:
            continue
        if bucket_name not in buckets:
            buckets.append(bucket_name)

    return buckets


def _get_configured_bucket_setting(file_config: Dict[str, Any]) -> Optional[List[str]]:
    env_buckets = _parse_bucket_list(os.getenv("S3_BUCKETS"))
    if env_buckets:
        return env_buckets

    indexed_buckets: List[str] = []
    i = 1
    while True:
        bucket_value = os.getenv(f"S3_BUCKET_{i}")
        if bucket_value in (None, ""):
            break
        bucket_name = bucket_value.strip()
        if bucket_name and bucket_name not in indexed_buckets:
            indexed_buckets.append(bucket_name)
        i += 1
    if indexed_buckets:
        return indexed_buckets

    for file_key in ("buckets", "bucket_names", "s3_buckets"):
        file_buckets = _parse_bucket_list(file_config.get(file_key))
        if file_buckets:
            return file_buckets

    return None


def _build_s3_resource() -> S3Resource:
    file_config: Dict[str, Any] = {}
    config_file_path = os.getenv("S3_CONFIG_FILE")
    config_profile = os.getenv("S3_CONFIG_PROFILE", "default")
    if config_file_path:
        file_config = _load_s3_config_file(config_file_path, config_profile)

    max_buckets = _get_int_setting("S3_MAX_BUCKETS", file_config, "max_buckets", 5)
    configured_buckets = _get_configured_bucket_setting(file_config)

    return S3Resource(
        region_name=_get_setting(
            ["S3_REGION", "AWS_REGION"], file_config, "region", "us-east-1"
        ),
        profile_name=_get_setting(
            ["S3_PROFILE", "AWS_PROFILE"], file_config, "profile_name"
        ),
        endpoint_url=_get_setting(["S3_ENDPOINT_URL"], file_config, "endpoint_url"),
        access_key_id=_get_setting(
            ["S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"], file_config, "access_key_id"
        ),
        secret_access_key=_get_setting(
            ["S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"],
            file_config,
            "secret_access_key",
        ),
        session_token=_get_setting(
            ["S3_SESSION_TOKEN", "AWS_SESSION_TOKEN"], file_config, "session_token"
        ),
        configured_buckets=configured_buckets,
        max_buckets=max_buckets,
        addressing_style=_get_setting(
            ["S3_ADDRESSING_STYLE"], file_config, "addressing_style", "auto"
        )
        or "auto",
        signature_version=_get_setting(
            ["S3_SIGNATURE_VERSION"], file_config, "signature_version"
        ),
        verify_ssl=_get_bool_setting("S3_VERIFY_SSL", file_config, "verify_ssl", True),
    )


# Initialize S3 resource
s3_resource = _build_s3_resource()


@server.set_logging_level()
async def set_logging_level(level: LoggingLevel) -> EmptyResult:
    logger.setLevel(level.lower())
    await server.request_context.session.send_log_message(
        level="info", data=f"Log level set to {level}", logger="mcp_s3_server"
    )
    return EmptyResult()


@server.list_resources()
async def list_resources(start_after: Optional[str] = None) -> List[Resource]:
    """
    List S3 buckets and their contents as resources with pagination
    Args:
        start_after: Start listing after this bucket name
    """
    resources = []
    logger.debug("Starting to list resources")
    logger.debug(f"Configured buckets: {s3_resource.configured_buckets}")

    try:
        # Get limited number of buckets
        buckets = await s3_resource.list_buckets(start_after)
        logger.debug(
            f"Processing {len(buckets)} buckets (max: {s3_resource.max_buckets})"
        )

        # limit concurrent operations
        async def process_bucket(bucket):
            bucket_name = bucket["Name"]
            logger.debug(f"Processing bucket: {bucket_name}")

            try:
                # List objects in the bucket with a reasonable limit
                objects = await s3_resource.list_objects(bucket_name, max_keys=1000)

                for obj in objects:
                    if "Key" in obj and not obj["Key"].endswith("/"):
                        object_key = obj["Key"]
                        mime_type = (
                            "text/plain"
                            if s3_resource.is_text_file(object_key)
                            else "text/markdown"
                        )

                        resource = Resource(
                            uri=f"s3://{bucket_name}/{object_key}",
                            name=object_key,
                            mimeType=mime_type,
                        )
                        resources.append(resource)
                        logger.debug(f"Added resource: {resource.uri}")

            except Exception as e:
                logger.error(f"Error listing objects in bucket {bucket_name}: {str(e)}")

        # Use semaphore to limit concurrent bucket processing
        semaphore = asyncio.Semaphore(3)  # Limit concurrent bucket processing

        async def process_bucket_with_semaphore(bucket):
            async with semaphore:
                await process_bucket(bucket)

        # Process buckets concurrently
        await asyncio.gather(
            *[process_bucket_with_semaphore(bucket) for bucket in buckets]
        )

    except Exception as e:
        logger.error(f"Error listing buckets: {str(e)}")
        raise

    logger.info(f"Returning {len(resources)} resources")
    return resources


@server.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """
    Read content from an S3 resource and return structured response

    Returns:
        Dict containing 'contents' list with uri, mimeType, and text for each resource
    """
    uri_str = str(uri)
    logger.debug(f"Reading resource: {uri_str}")

    if not uri_str.startswith("s3://"):
        raise ValueError("Invalid S3 URI")

    # Parse the S3 URI
    from urllib.parse import unquote

    path = uri_str[5:]  # Remove "s3://"
    path = unquote(path)  # Decode URL-encoded characters
    parts = path.split("/", 1)

    if len(parts) < 2:
        raise ValueError("Invalid S3 URI format")

    bucket_name = parts[0]
    key = parts[1]

    logger.debug(f"Attempting to read - Bucket: {bucket_name}, Key: {key}")

    try:
        response = await s3_resource.get_object(bucket_name, key)
        content_type = response.get("ContentType", "")
        logger.debug(f"Read MIMETYPE response: {content_type}")

        # Content type mapping for specific file types
        content_type_mapping = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/markdown",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/csv",
            "application/vnd.ms-excel": "application/csv",
        }

        # Check if content type needs to be modified
        export_mime_type = content_type_mapping.get(content_type, content_type)
        logger.debug(f"Export MIME type: {export_mime_type}")

        if "Body" in response:
            if isinstance(response["Body"], bytes):
                data = response["Body"]
            else:
                # Handle streaming response
                async with response["Body"] as stream:
                    data = await stream.read()

            # Process the data based on file type
            if s3_resource.is_text_file(key):
                # text_content = data.decode('utf-8')
                text_content = base64.b64encode(data).decode("utf-8")

                return text_content
            else:
                text_content = str(base64.b64encode(data))

                result = ReadResourceResult(
                    contents=[
                        BlobResourceContents(
                            blob=text_content, uri=uri_str, mimeType=export_mime_type
                        )
                    ]
                )

                logger.debug(result)

                return text_content

        else:
            raise ValueError("No data in response body")

    except Exception as e:
        logger.error(f"Error reading object {key} from bucket {bucket_name}: {str(e)}")
        if "NoSuchKey" in str(e):
            try:
                # List similar objects to help debugging
                objects = await s3_resource.list_objects(
                    bucket_name, prefix=key.split("/")[0]
                )
                similar_objects = [obj["Key"] for obj in objects if "Key" in obj]
                logger.debug(f"Similar objects found: {similar_objects}")
            except Exception as list_err:
                logger.error(f"Error listing similar objects: {str(list_err)}")
        raise ValueError(f"Error reading resource: {str(e)}")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="ListBuckets",  # https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListBuckets.html
            description="Returns a list of buckets accessible by the configured S3-compatible credentials.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_after": {
                        "type": "string",
                        "description": "Start listing after this bucket name",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="ListObjectsV2",  # https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
            description="Returns up to 1,000 objects in a bucket from an S3-compatible API.",
            inputSchema={
                "type": "object",
                "properties": {
                    "bucket_name": {
                        "type": "string",
                        "description": "Name of the bucket to list objects from.",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "the prefix of the keys to list.",
                    },
                    "max_keys": {
                        "type": "integer",
                        "description": "Sets the maximum number of keys returned in the response. By default, the action returns up to 1,000 key names. The response might contain fewer keys but will never contain more.",
                    },
                },
                "required": ["bucket_name"],
            },
        ),
        Tool(
            name="GetObject",  # https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html
            description="Retrieves an object by bucket and key from an S3-compatible API.",
            inputSchema={
                "type": "object",
                "properties": {
                    "bucket_name": {
                        "type": "string",
                        "description": "Name of the bucket containing the object.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key of the object to get. Length Constraints: Minimum length of 1.",
                    },
                    "max_retries": {
                        "type": "integer",
                        "description": "max number of attempts to download the file.",
                    },
                },
                "required": ["bucket_name", "key"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[TextContent | ImageContent | EmbeddedResource]:
    logger.info(f"handle_call_tool got name {name}, args {arguments}")
    try:
        match name:
            case "ListBuckets":
                start_after = None
                if arguments:
                    start_after = arguments.get("start_after") or arguments.get(
                        "StartAfter"
                    )
                buckets = await s3_resource.list_buckets(start_after)
                logger.info(f"listBuckets returning buckets {buckets}")
                return [TextContent(type="text", text=str(buckets))]
            case "ListObjectsV2":
                args = {"bucket_name": arguments["bucket_name"]}
                if "prefix" in arguments:
                    args["prefix"] = arguments["prefix"]
                if "max_keys" in arguments:
                    args["max_keys"] = arguments["max_keys"]

                objects = await s3_resource.list_objects(**args)

                logger.info(f"ListObjectsV2 returning objects {objects}")

                return [TextContent(type="text", text=str(objects))]
            case "GetObject":
                args = {
                    "bucket_name": arguments["bucket_name"],
                    "key": arguments["key"],
                }
                if "max_retries" in arguments:
                    args["max_retries"] = arguments["max_retries"]
                response = await s3_resource.get_object(**args)
                logger.info(f"GetObject got response {response}")

                body = response["Body"]
                if isinstance(body, bytes):
                    file_content = body.decode("utf-8", errors="replace")
                else:
                    file_content = str(body)

                logger.info(f"GetObject got file_content {file_content}")
                return [TextContent(type="text", text=str(file_content))]
    except Exception as error:
        return [TextContent(type="text", text=f"Error: {str(error)}")]


async def main():
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="s3-mcp-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
