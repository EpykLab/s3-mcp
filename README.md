# Sample S3-Compatible Model Context Protocol Server

An MCP server implementation for retrieving data such as PDFs from S3-compatible object storage.

## Features
### Resources
Expose S3-compatible data through **Resources**. (think of these sort of like GET endpoints; they are used to load information into the LLM's context). Currently limited to **1000** objects per bucket listing.


### Tools
- **ListBuckets**
  - Returns a list of buckets available to the configured credentials
- **ListObjectsV2**
  - Returns some or all (up to 1,000) of the objects in a bucket with each request
- **GetObject**
  - Retrieves an object from an S3-compatible endpoint using bucket name and object key


## Configuration

### Setting up Credentials
You can use either:
1. Standard AWS-style env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) for AWS S3 and most compatible providers.
2. S3-specific aliases (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`) plus `S3_ENDPOINT_URL` for non-AWS providers.

You can also load credentials and endpoint settings from an optional config file:
- `S3_CONFIG_FILE=/path/to/s3.config.toml` (supports `.toml` and `.json`)
- `S3_CONFIG_PROFILE=default` (optional, defaults to `default`)
- See `s3.config.example.toml` for a complete example
- Optional allow-list in config file: `buckets = ["my-space"]`

Precedence order:
1. Environment variables (`S3_*`, `AWS_*`)
2. `S3_CONFIG_FILE` profile values
3. Botocore/AWS SDK credential provider chain

Common non-AWS settings:
- `S3_ENDPOINT_URL`: custom endpoint URL (required for most non-AWS providers)
- `S3_ADDRESSING_STYLE=path`: often required by MinIO and some self-hosted S3 APIs
- `S3_SIGNATURE_VERSION=s3v4`: default for modern providers
- `S3_VERIFY_SSL=false`: only for local dev with self-signed certs
- `S3_BUCKETS=my-space`: explicit bucket allow-list; bypasses `ListBuckets` permission requirement

### Usage with Claude Desktop

#### Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`
On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Development/Unpublished Servers Configuration</summary>

```json
{
  "mcpServers": {
    "s3-mcp-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/user/generative_ai/model_context_protocol/s3-mcp-server",
        "run",
        "s3-mcp-server"
      ]
    }
  }
}
```

</details>

<details>
  <summary>Published Servers Configuration</summary>

```json
{
  "mcpServers": {
    "s3-mcp-server": {
      "command": "uvx",
      "args": [
        "s3-mcp-server"
      ]
    }
  }
}
  ```
</details>

## Development

### Building and Publishing

To prepare the package for distribution:

1. Sync dependencies and update lockfile:
```bash
uv sync
```

2. Build package distributions:
```bash
uv build
```

This will create source and wheel distributions in the `dist/` directory.

3. Publish to PyPI:
```bash
uv publish
```

Note: You'll need to set PyPI credentials via environment variables or command flags:
- Token: `--token` or `UV_PUBLISH_TOKEN`
- Or username/password: `--username`/`UV_PUBLISH_USERNAME` and `--password`/`UV_PUBLISH_PASSWORD`

### Debugging

Since MCP servers run over stdio, debugging can be challenging. For the best debugging
experience, we strongly recommend using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector).


You can launch the MCP Inspector via [`npm`](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm) with this command:

```bash
npx @modelcontextprotocol/inspector uv --directory /Users/user/generative_ai/model_context_protocol/s3-mcp-server run s3-mcp-server
```


Upon launching, the Inspector will display a URL that you can access in your browser to begin debugging.


## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
