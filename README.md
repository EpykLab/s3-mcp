# s3-mcp

`s3-mcp` is a Model Context Protocol (MCP) server for AWS S3 and S3-compatible object stores (for example MinIO, Cloudflare R2, and Backblaze B2 S3). It exposes buckets and objects through MCP resources and tools so LLM clients can browse and fetch content from object storage.

## What this server exposes

### Resources

- `s3://<bucket>/<key>` resources generated from discovered buckets and objects
- Up to 1,000 objects per bucket listing request
- Bucket listing is limited by `S3_MAX_BUCKETS` (default: `5`)

### Tools

- `ListBuckets` - list buckets accessible by configured credentials
- `ListObjectsV2` - list objects in a bucket (supports `prefix` and `max_keys`)
- `GetObject` - fetch an object by `bucket_name` + `key`

## Quick start

1. Sync dependencies:

```bash
uv sync --no-install-project
```

2. Create a local environment file from the template:

```bash
cp env.example .env
```

3. Set credentials and endpoint settings in `.env`.

4. Run the server:

```bash
PYTHONPATH=src .venv/bin/python -m s3_mcp_server.server
```

The server runs over stdio (as MCP servers normally do).

## Configuration

You can configure this project with environment variables, an optional JSON/TOML config file, or the default AWS SDK credential chain.

### Credential and setting precedence

1. Environment variables (`S3_*`, `AWS_*`)
2. `S3_CONFIG_FILE` + `S3_CONFIG_PROFILE`
3. Botocore/AWS SDK credential provider chain

### Important environment variables

- `S3_REGION` / `AWS_REGION` - region used for request signing
- `S3_ENDPOINT_URL` - required for most non-AWS providers
- `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_SESSION_TOKEN`
- `S3_ADDRESSING_STYLE` - `auto`, `path`, or `virtual`
- `S3_SIGNATURE_VERSION` - usually `s3v4`
- `S3_VERIFY_SSL` - set `false` only for local/self-signed development
- `S3_MAX_BUCKETS` - max buckets processed per list call

### Bucket allow-list (recommended for restricted credentials)

You can bypass the `ListBuckets` permission requirement by explicitly declaring allowed buckets:

- `S3_BUCKETS=my-bucket-a,my-bucket-b`
- or indexed vars: `S3_BUCKET_1`, `S3_BUCKET_2`, ...
- or config file keys: `buckets`, `bucket_names`, or `s3_buckets`

### Optional config file

Set:

- `S3_CONFIG_FILE=/absolute/path/to/s3.config.toml` (or `.json`)
- `S3_CONFIG_PROFILE=default`

See `s3.config.example.toml` for profile examples.

## Claude Desktop example

Claude Desktop config path:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "s3-mcp-server": {
      "command": "/absolute/path/to/s3-mcp/.venv/bin/python",
      "args": ["-m", "s3_mcp_server.server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/s3-mcp/src",
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "replace-me",
        "S3_SECRET_ACCESS_KEY": "replace-me",
        "S3_BUCKETS": "my-bucket"
      }
    }
  }
}
```

## Debugging

The easiest way to inspect requests and responses is with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector /absolute/path/to/s3-mcp/.venv/bin/python -m s3_mcp_server.server
```

## License

This project is licensed under MIT-0. See `LICENSE`.
