import logging
import os
from typing import List, Dict, Any, Optional
import aioboto3
import asyncio
from botocore.config import Config

logger = logging.getLogger("s3_mcp_server")


class S3Resource:
    """
    S3 Resource provider that handles interactions with S3-compatible buckets.
    Part of a collection of resource providers (S3, DynamoDB, etc.) for the MCP server.
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        profile_name: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
        configured_buckets: Optional[List[str]] = None,
        max_buckets: int = 5,
        addressing_style: str = "auto",
        signature_version: Optional[str] = None,
        verify_ssl: bool = True,
    ):
        """
        Initialize S3 resource provider
        Args:
            region_name: Region name used for request signing
            profile_name: Optional shared credentials profile name
            endpoint_url: Optional custom S3-compatible endpoint URL
            access_key_id: Optional access key for S3-compatible auth
            secret_access_key: Optional secret key for S3-compatible auth
            session_token: Optional session token for temporary credentials
            configured_buckets: Optional allow-list of bucket names
            max_buckets: Maximum number of buckets to process (default: 5)
            addressing_style: S3 addressing style (auto, path, virtual)
            signature_version: Optional custom signing algorithm
            verify_ssl: Whether to verify TLS certificates
        """
        supported_addressing_styles = {"auto", "path", "virtual"}
        if addressing_style not in supported_addressing_styles:
            logger.warning(
                "Unsupported S3 addressing style '%s'. Falling back to 'auto'.",
                addressing_style,
            )
            addressing_style = "auto"

        # Configure boto3 with retries and timeouts
        config_kwargs: Dict[str, Any] = dict(
            retries=dict(max_attempts=3, mode="adaptive"),
            connect_timeout=5,
            read_timeout=60,
            max_pool_connections=50,
            s3={
                "addressing_style": addressing_style,
            },
        )

        if signature_version:
            config_kwargs["signature_version"] = signature_version

        self.config = Config(**config_kwargs)

        session_kwargs: Dict[str, Any] = {}
        if profile_name:
            session_kwargs["profile_name"] = profile_name

        self.session = aioboto3.Session(**session_kwargs)
        self.region_name = region_name
        self.endpoint_url = endpoint_url
        self.verify_ssl = verify_ssl
        self.access_key_id = (
            access_key_id
            or os.getenv("S3_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
        self.secret_access_key = (
            secret_access_key
            or os.getenv("S3_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        self.session_token = (
            session_token
            or os.getenv("S3_SESSION_TOKEN")
            or os.getenv("AWS_SESSION_TOKEN")
        )
        self.max_buckets = max_buckets
        if configured_buckets is None:
            self.configured_buckets = self._get_configured_buckets()
        else:
            self.configured_buckets = self._normalize_bucket_names(configured_buckets)

    def _normalize_bucket_names(self, buckets: List[str]) -> List[str]:
        normalized: List[str] = []
        for bucket in buckets:
            bucket_name = bucket.strip()
            if not bucket_name:
                continue
            if bucket_name not in normalized:
                normalized.append(bucket_name)
        return normalized

    def _client_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "region_name": self.region_name,
            "endpoint_url": self.endpoint_url,
            "config": self.config,
            "verify": self.verify_ssl,
        }

        if self.access_key_id and self.secret_access_key:
            kwargs["aws_access_key_id"] = self.access_key_id
            kwargs["aws_secret_access_key"] = self.secret_access_key
            if self.session_token:
                kwargs["aws_session_token"] = self.session_token

        return {k: v for k, v in kwargs.items() if v is not None}

    def _get_configured_buckets(self) -> List[str]:
        """
        Get configured bucket names from environment variables.
        Format in .env file:
        S3_BUCKETS=bucket1,bucket2,bucket3
        or
        S3_BUCKET_1=bucket1
        S3_BUCKET_2=bucket2
        see env.example ############
        """
        # Try comma-separated list first
        bucket_list = os.getenv("S3_BUCKETS")
        if bucket_list:
            return self._normalize_bucket_names(bucket_list.split(","))

        buckets = []
        i = 1
        while True:
            bucket = os.getenv(f"S3_BUCKET_{i}")
            if not bucket:
                break
            buckets.append(bucket.strip())
            i += 1

        return self._normalize_bucket_names(buckets)

    async def list_buckets(self, start_after: Optional[str] = None) -> List[dict]:
        """
        List S3 buckets using async client with pagination
        """
        if self.configured_buckets:
            buckets = [{"Name": bucket_name} for bucket_name in self.configured_buckets]

            if start_after:
                buckets = [b for b in buckets if b["Name"] > start_after]

            return buckets[: self.max_buckets]

        async with self.session.client("s3", **self._client_kwargs()) as s3:
            response = await s3.list_buckets()
            buckets = response.get("Buckets", [])

            if start_after:
                buckets = [b for b in buckets if b["Name"] > start_after]

            return buckets[: self.max_buckets]

    async def list_objects(
        self, bucket_name: str, prefix: str = "", max_keys: int = 1000
    ) -> List[dict]:
        """
        List objects in a specific bucket using async client with pagination
        Args:
            bucket_name: Name of the S3 bucket
            prefix: Object prefix for filtering
            max_keys: Maximum number of keys to return
        """
        logger.info(
            f"s3_resource.list_objects received bucket {bucket_name}, prefix {prefix}"
        )

        if self.configured_buckets and bucket_name not in self.configured_buckets:
            logger.warning(
                f"list_objects failed! Bucket {bucket_name} not in configured bucket list"
            )
            return []

        async with self.session.client("s3", **self._client_kwargs()) as s3:
            response = await s3.list_objects_v2(
                Bucket=bucket_name, Prefix=prefix, MaxKeys=max_keys
            )
            logger.info(f"s3_resource.list_objects got response {response}")

            return response.get("Contents", [])

    async def get_object(
        self, bucket_name: str, key: str, max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Get object from S3 using streaming to handle large files and PDFs reliably.
        The method reads the stream in chunks and concatenates them before returning.
        """
        logger.info(f"s3_resource.get_object received bucket {bucket_name}, key {key}")

        if self.configured_buckets and bucket_name not in self.configured_buckets:
            raise ValueError(
                f"get_object failed! Bucket {bucket_name} not in configured bucket list"
            )

        attempt = 0
        last_exception = None

        while attempt < max_retries:
            try:
                async with self.session.client("s3", **self._client_kwargs()) as s3:
                    # Get the object and its stream
                    response = await s3.get_object(Bucket=bucket_name, Key=key)
                    stream = response["Body"]

                    # Read the entire stream in chunks
                    chunks = []
                    async for chunk in stream:
                        chunks.append(chunk)

                    # Replace the stream with the complete data
                    response["Body"] = b"".join(chunks)
                    return response

            except Exception as e:
                last_exception = e
                if "NoSuchKey" in str(e):
                    raise

                attempt += 1
                if attempt < max_retries:
                    wait_time = 2**attempt
                    logger.warning(
                        f"Attempt {attempt} failed, retrying in {wait_time} seconds: {str(e)}"
                    )
                    await asyncio.sleep(wait_time)
                continue

        raise last_exception or Exception("Failed to get object after all retries")

    def is_text_file(self, key: str) -> bool:
        """Determine if a file is text-based by its extension"""
        text_extensions = {
            ".txt",
            ".log",
            ".json",
            ".xml",
            ".yml",
            ".yaml",
            ".md",
            ".csv",
            ".ini",
            ".conf",
            ".py",
            ".js",
            ".html",
            ".css",
            ".sh",
            ".bash",
            ".cfg",
            ".properties",
        }
        return any(key.lower().endswith(ext) for ext in text_extensions)
