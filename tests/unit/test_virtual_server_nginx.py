"""Unit tests for virtual server nginx configuration generation."""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from registry.schemas.virtual_server_models import (
    ToolMapping,
    ToolScopeOverride,
    VirtualServerConfig,
)


def _make_vs_config(
    path="/virtual/dev-essentials",
    server_name="Dev Essentials",
    tool_mappings=None,
    tool_scope_overrides=None,
    is_enabled=True,
):
    """Helper to build VirtualServerConfig objects for tests."""
    if tool_mappings is None:
        tool_mappings = [
            ToolMapping(
                tool_name="search",
                backend_server_path="/github",
            ),
        ]
    if tool_scope_overrides is None:
        tool_scope_overrides = []
    return VirtualServerConfig(
        path=path,
        server_name=server_name,
        tool_mappings=tool_mappings,
        tool_scope_overrides=tool_scope_overrides,
        is_enabled=is_enabled,
    )


class TestGenerateVirtualServerBlocks:
    """Tests for _generate_virtual_server_blocks.

    Uses the conftest-provided mock_virtual_server_repository (autouse fixture).
    """

    @pytest.mark.asyncio
    async def test_no_enabled_virtual_servers(self, mock_virtual_server_repository):
        """Test empty string returned when no enabled virtual servers exist."""
        mock_virtual_server_repository.list_enabled.return_value = []

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert result == ""

    @pytest.mark.asyncio
    async def test_generates_location_block(self, mock_virtual_server_repository):
        """Test location block is generated for an enabled virtual server."""
        vs = _make_vs_config()
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert "/virtual/dev-essentials" in result

    @pytest.mark.asyncio
    async def test_block_includes_set_virtual_server_id(self, mock_virtual_server_repository):
        """Test that generated block includes set $virtual_server_id."""
        vs = _make_vs_config()
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert 'set $virtual_server_id "dev-essentials"' in result

    @pytest.mark.asyncio
    async def test_block_includes_auth_request(self, mock_virtual_server_repository):
        """Test that generated block includes auth_request directive."""
        vs = _make_vs_config()
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert "auth_request /validate" in result

    @pytest.mark.asyncio
    async def test_block_includes_lua_directives(self, mock_virtual_server_repository):
        """Test that generated block includes Lua directives."""
        vs = _make_vs_config()
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert "rewrite_by_lua_file" in result
        assert "content_by_lua_file" in result
        assert "virtual_router.lua" in result

    @pytest.mark.asyncio
    async def test_block_routes_401_through_auth_error(self, mock_virtual_server_repository):
        """Virtual-server blocks must route 401s through @auth_error so the
        RFC 9728 WWW-Authenticate header is emitted (issue #989)."""
        vs = _make_vs_config()
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert "error_page 401 = @auth_error" in result
        assert "error_page 403 = @forbidden_error" in result

    @pytest.mark.asyncio
    async def test_location_normalised_to_trailing_slash(self, mock_virtual_server_repository):
        """Issue #1501: the virtual-server location must render with a trailing
        slash so nginx does a subtree prefix match (`/virtual/dev/`) instead of
        hijacking any URL that merely starts with the path (`/virtual/dev` would
        otherwise prefix-match `/virtual/devtools`)."""
        vs = _make_vs_config(path="/virtual/dev", server_name="Dev")
        mock_virtual_server_repository.list_enabled.return_value = [vs]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        # The location directive is normalised to end with a slash ...
        assert "location {{ROOT_PATH}}/virtual/dev/ {" in result
        # ... and must NOT emit the bare-path form that prefix-matches
        # /virtual/devtools, /virtual/development, etc.
        assert "location {{ROOT_PATH}}/virtual/dev {" not in result

    @pytest.mark.asyncio
    async def test_multiple_virtual_servers(self, mock_virtual_server_repository):
        """Test that multiple virtual servers produce multiple location blocks."""
        vs1 = _make_vs_config(path="/virtual/dev", server_name="Dev")
        vs2 = _make_vs_config(path="/virtual/staging", server_name="Staging")
        mock_virtual_server_repository.list_enabled.return_value = [vs1, vs2]

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_server_blocks()

        assert "/virtual/dev" in result
        assert "/virtual/staging" in result


class TestGenerateVirtualBackendLocations:
    """Tests for _generate_virtual_backend_locations.

    Uses the conftest-provided mock_server_repository (autouse fixture).
    """

    @pytest.mark.asyncio
    async def test_no_backends(self, mock_server_repository):
        """Test empty string returned when virtual servers have no tool mappings."""
        vs = _make_vs_config(tool_mappings=[])

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert result == ""

    @pytest.mark.asyncio
    async def test_generates_internal_locations(self, mock_server_repository):
        """Test that internal location blocks are generated for backends."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert "/_vs_backend" in result
        assert "internal;" in result
        # A normal external host (has a dot) uses a literal proxy_pass that nginx
        # resolves once at startup, so there is no per-request DNS cost and no
        # resolver directive for this common case.
        assert "proxy_pass https://api.github.com/mcp" in result
        assert "resolver " not in result

    @pytest.mark.asyncio
    async def test_preserves_nested_mcp_transport_path(self, mock_server_repository):
        """A configured /mcp/... endpoint must not receive a second /mcp suffix."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://insights.example.com/mcp/http",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert "proxy_pass https://insights.example.com/mcp/http;" in result
        assert "/mcp/http/mcp" not in result

    @pytest.mark.asyncio
    async def test_explicit_mcp_endpoint_keeps_proxy_host(self, mock_server_repository):
        """Explicit endpoint paths use the private proxy host for internal routing."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "http://insights-service:8000",
            "mcp_endpoint": "https://public.example.com/custom/mcp/http",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert 'set $vs_backend_github "http://insights-service:8000/custom/mcp/http"' in result
        assert "public.example.com" not in result

    @pytest.mark.asyncio
    async def test_credentials_not_forwarded_to_untrusted_backend(self, mock_server_repository):
        """The caller's credential must not be relayed to a registrant-controlled backend.

        This location proxies directly to the MCP backend URL supplied by whoever
        registered the server. Forwarding ``Authorization`` or the parent
        request's ``Cookie`` (inherited by the Lua subrequest) would leak the
        caller's registry-scoped token or session cookie to that untrusted
        upstream. Both must be cleared instead.
        """
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert 'proxy_set_header Authorization "";' in result
        assert "proxy_set_header Authorization $http_authorization;" not in result
        # The Lua subrequest inherits the parent request's Cookie header, so the
        # user's registry session cookie must also be cleared before reaching
        # the untrusted backend.
        assert 'proxy_set_header Cookie "";' in result

    @pytest.mark.asyncio
    async def test_identity_headers_forwarded_via_http_vars(self, mock_server_repository):
        """Caller identity is forwarded using $http_x_user/$http_x_username variables.

        The Lua virtual_router sets X-User and X-Username as request headers
        (ngx.req.set_header) before ngx.location.capture subrequests.  The
        _vs_backend location block then forwards them to the upstream via
        proxy_set_header using the $http_x_user/$http_x_username variables
        (which read from incoming request headers, not auth_request_set vars).

        This two-part approach is required because auth_request_set variables
        ($auth_user) do not propagate into subrequest contexts.
        """
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # Identity headers forwarded via $http_x_* (request header vars, set by Lua)
        assert "proxy_set_header X-User $http_x_user;" in result
        assert "proxy_set_header X-Username $http_x_username;" in result
        # Must NOT use $auth_user (doesn't propagate to subrequests)
        assert "proxy_set_header X-User $auth_user" not in result
        assert "proxy_set_header X-Username $auth_username" not in result

    @pytest.mark.asyncio
    async def test_bare_hostname_backend_uses_deferred_resolution(self, mock_server_repository):
        """Bare hostnames defer DNS resolution so they cannot crash nginx at startup."""
        vs = _make_vs_config()
        # A docker-compose-style service name (no dot) is not resolvable in every
        # environment; a literal proxy_pass to it would make nginx fail to start.
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "http://currenttime-server:8000/",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # Resolver + variable form means nginx resolves at request time, turning an
        # unresolvable backend into a per-request 502 instead of a startup crash.
        assert "resolver " in result
        assert 'set $vs_backend_github "http://currenttime-server:8000/mcp"' in result
        assert "proxy_pass $vs_backend_github" in result

    @pytest.mark.asyncio
    async def test_deduplicates_backends(self, mock_server_repository):
        """Test that duplicate backend paths are deduplicated."""
        mappings = [
            ToolMapping(tool_name="search", backend_server_path="/github"),
            ToolMapping(tool_name="issues", backend_server_path="/github"),
        ]
        vs = _make_vs_config(tool_mappings=mappings)
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # Should only have one /_vs_backend block for /github
        assert result.count("/_vs_backend") == 1

    @pytest.mark.asyncio
    async def test_skips_missing_backends(self, mock_server_repository):
        """Test that missing backend servers are skipped."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = None

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert result == ""

    @pytest.mark.asyncio
    async def test_skips_backends_without_proxy_url(self, mock_server_repository):
        """Test that backends without proxy_pass_url are skipped."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "server_name": "GitHub",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert result == ""


class TestGenerateVirtualBackendEgressLocations:
    """Tests for egress-authed virtual backend locations (issue #1607).

    A backend registered with gateway egress auth (oauth_user / pat /
    obo_exchange) must NOT be proxied directly; it must route through the same
    /validate + /mcp-proxy seam as a directly-registered server so the per-user
    credential is vended/injected and the caller's gateway credential is stripped.
    """

    @pytest.mark.asyncio
    async def test_egress_backend_routes_through_mcp_proxy(self, mock_server_repository):
        """Egress backends proxy to the auth-server mcp_proxy hop, not the backend."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        from registry.core.config import settings

        mcp_proxy_target = f"{settings.auth_server_url.rstrip('/')}/mcp-proxy/github/"
        # Routes through the mcp_proxy hop keyed on the resolved backend path ...
        assert f"proxy_pass {mcp_proxy_target};" in result
        # ... and never directly to the registrant-controlled backend.
        assert "proxy_pass https://api.github.com/mcp;" not in result

    @pytest.mark.asyncio
    async def test_egress_backend_binds_resolved_upstream(self, mock_server_repository):
        """The resolved backend upstream is bound into $backend_url (token upstream claim)."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert 'set $backend_url "https://api.github.com/mcp";' in result

    @pytest.mark.asyncio
    async def test_egress_backend_binds_backend_server_path(self, mock_server_repository):
        """The resolved backend server path is bound so /validate mints the right token."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert 'set $vs_backend_server_name "github";' in result

    @pytest.mark.asyncio
    async def test_egress_backend_mints_backend_bound_token(self, mock_server_repository):
        """The egress backend hop mints + forwards a backend-bound internal token.

        auth_request does NOT run in ngx.location.capture subrequests, so the token
        is minted by virtual_backend_mint.lua capturing the sibling validate
        location, then forwarded to mcp_proxy via $auth_internal_token.
        """
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # No auth_request (it is a no-op in a subrequest) ...
        assert "auth_request /validate;" not in result
        # ... instead the mint helper captures the sibling validate location ...
        assert 'set $vs_validate_location "/_vs_validate_github";' in result
        assert "rewrite_by_lua_file /etc/nginx/lua/virtual_backend_mint.lua;" in result
        # ... and the minted token is forwarded to mcp_proxy.
        assert 'set $auth_internal_token "";' in result
        assert "proxy_set_header X-Internal-Token $auth_internal_token;" in result

    @pytest.mark.asyncio
    async def test_egress_backend_validate_uses_get_method(self, mock_server_repository):
        """The minting validate location must proxy to /validate with GET (the
        /validate contract), not forward the capture's POST.

        virtual_backend_mint.lua captures the sibling validate location with
        ngx.HTTP_POST; without proxy_method GET, that POST would reach auth-server
        /validate and be rejected 405 Method Not Allowed.
        """
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert "proxy_method GET;" in result

    @pytest.mark.asyncio
    async def test_egress_backend_passes_identity_into_validate(self, mock_server_repository):
        """The minting validate location forwards the backend identity + upstream to
        auth-server /validate (so it authorizes + mints against the backend)."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        from registry.core.config import settings

        validate_target = f"{settings.auth_server_url.rstrip('/')}/validate"
        assert "location /_vs_validate_github {" in result
        assert f"proxy_pass {validate_target};" in result
        assert "proxy_set_header X-Resolved-Upstream $backend_url;" in result
        assert "proxy_set_header X-Vs-Backend-Server-Name $vs_backend_server_name;" in result
        assert "proxy_set_header X-Validate-Source-Secret" in result

    @pytest.mark.asyncio
    async def test_egress_backend_captures_body_for_scope_check(self, mock_server_repository):
        """The minting validate location captures the proxied body so /validate
        authorizes the real backend-original tool, not the virtual alias."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert "rewrite_by_lua_file /etc/nginx/lua/capture_body.lua;" in result

    @pytest.mark.asyncio
    async def test_egress_backend_does_not_leak_cookie(self, mock_server_repository):
        """The caller's registry session cookie must never reach the backend."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert 'proxy_set_header Cookie "";' in result

    @pytest.mark.asyncio
    async def test_non_egress_backend_keeps_direct_proxy(self, mock_server_repository):
        """A backend without egress auth keeps the direct (no-mcp-proxy) path."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "none",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        assert "proxy_pass https://api.github.com/mcp;" in result
        assert "mcp-proxy" not in result

    @pytest.mark.asyncio
    async def test_egress_backend_identity_not_client_derived(self, mock_server_repository):
        """The backend identity + upstream are bound from trusted config, never from
        client-supplied request headers (no $http_* passthrough)."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # Identity/upstream are `set` from registry-generated config, not read from
        # any inbound header a caller could forge.
        assert 'set $backend_url "https://api.github.com/mcp";' in result
        assert 'set $vs_backend_server_name "github";' in result
        assert "$http_x_vs_backend_server_name" not in result
        assert "$http_x_resolved_upstream" not in result
        assert "proxy_set_header X-Vs-Backend-Server-Name $http_" not in result

    @pytest.mark.asyncio
    async def test_parent_virtual_auth_stays_separate(self, mock_server_repository):
        """The parent virtual /validate (server=virtual/...) stays a SEPARATE main-request
        auth_request; the backend hop mints its OWN token via a dedicated validate
        location rather than reusing/overriding the parent's auth_request."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://api.github.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        # The backend hop never emits auth_request (which is main-request-only and
        # would clobber the parent's server='virtual/...' authorization).
        assert "auth_request /validate;" not in result
        # It mints via its own dedicated validate location, keyed on the backend.
        assert "location /_vs_validate_github {" in result
        assert "location /_vs_backend_github {" in result

    @pytest.mark.asyncio
    async def test_egress_backend_federated_path_binding(self, mock_server_repository):
        """Multi-segment federated backend paths are bound + proxied correctly."""
        vs = _make_vs_config(
            tool_mappings=[
                ToolMapping(tool_name="doc", backend_server_path="/peer/lob/cloudflare-docs"),
            ],
        )
        mock_server_repository.get.return_value = {
            "proxy_pass_url": "https://docs.mcp.cloudflare.com",
            "egress_auth_mode": "obo_exchange",
        }

        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = await service._generate_virtual_backend_locations([vs])

        from registry.core.config import settings

        mcp_proxy_target = (
            f"{settings.auth_server_url.rstrip('/')}/mcp-proxy/peer/lob/cloudflare-docs/"
        )
        assert f"proxy_pass {mcp_proxy_target};" in result
        assert 'set $vs_backend_server_name "peer/lob/cloudflare-docs";' in result


class TestWriteVirtualServerMappings:
    """Tests for _write_virtual_server_mappings.

    Uses the conftest-provided mock_server_repository (autouse fixture).
    """

    @pytest.mark.asyncio
    async def test_writes_mapping_file(self, mock_server_repository):
        """Test that mapping JSON file is written for each virtual server."""
        vs = _make_vs_config()
        mock_server_repository.get.return_value = {
            "server_name": "GitHub",
            "tool_list": [
                {
                    "name": "search",
                    "description": "Search repos",
                    "inputSchema": {"type": "object"},
                },
            ],
        }

        m = mock_open()
        with patch("registry.core.nginx_service.Path") as mock_path_cls, patch("builtins.open", m):
            mock_mappings_dir = MagicMock()
            mock_path_cls.return_value = mock_mappings_dir
            mock_mapping_file = MagicMock()
            mock_mappings_dir.__truediv__ = MagicMock(return_value=mock_mapping_file)

            from registry.core.nginx_service import NginxConfigService

            service = NginxConfigService()
            await service._write_virtual_server_mappings([vs])

        # Verify open was called for writing
        m.assert_called()

    @pytest.mark.asyncio
    async def test_mapping_contains_tools(self, mock_server_repository):
        """Test that mapping JSON contains tool data with alias."""
        vs = _make_vs_config(
            tool_mappings=[
                ToolMapping(
                    tool_name="search",
                    alias="gh-search",
                    backend_server_path="/github",
                ),
            ],
        )
        mock_server_repository.get.return_value = {
            "server_name": "GitHub",
            "tool_list": [
                {
                    "name": "search",
                    "description": "Search repos",
                    "inputSchema": {"type": "object"},
                },
            ],
        }

        written_data = {}

        def capture_write(data, f, **kwargs):
            written_data.update(data)

        with (
            patch("registry.core.nginx_service.Path") as mock_path_cls,
            patch("json.dump", side_effect=capture_write),
        ):
            mock_mappings_dir = MagicMock()
            mock_path_cls.return_value = mock_mappings_dir
            mock_mapping_file = MagicMock()
            mock_mappings_dir.__truediv__ = MagicMock(return_value=mock_mapping_file)

            m = mock_open()
            with patch("builtins.open", m):
                from registry.core.nginx_service import NginxConfigService

                service = NginxConfigService()
                await service._write_virtual_server_mappings([vs])

        assert "tools" in written_data
        assert len(written_data["tools"]) == 1
        assert written_data["tools"][0]["name"] == "gh-search"
        assert written_data["tools"][0]["original_name"] == "search"

    @pytest.mark.asyncio
    async def test_mapping_includes_scope_overrides(self, mock_server_repository):
        """Test that mapping JSON includes per-tool scope overrides."""
        vs = _make_vs_config(
            tool_mappings=[
                ToolMapping(tool_name="search", backend_server_path="/github"),
            ],
            tool_scope_overrides=[
                ToolScopeOverride(
                    tool_alias="search",
                    required_scopes=["github:read"],
                ),
            ],
        )
        mock_server_repository.get.return_value = {
            "server_name": "GitHub",
            "tool_list": [
                {"name": "search", "description": "Search", "inputSchema": {}},
            ],
        }

        written_data = {}

        def capture_write(data, f, **kwargs):
            written_data.update(data)

        with (
            patch("registry.core.nginx_service.Path") as mock_path_cls,
            patch("json.dump", side_effect=capture_write),
        ):
            mock_mappings_dir = MagicMock()
            mock_path_cls.return_value = mock_mappings_dir
            mock_mapping_file = MagicMock()
            mock_mappings_dir.__truediv__ = MagicMock(return_value=mock_mapping_file)

            m = mock_open()
            with patch("builtins.open", m):
                from registry.core.nginx_service import NginxConfigService

                service = NginxConfigService()
                await service._write_virtual_server_mappings([vs])

        assert written_data["tools"][0]["required_scopes"] == ["github:read"]

    @pytest.mark.asyncio
    async def test_mapping_includes_backend_map(self, mock_server_repository):
        """Test that mapping JSON includes tool_backend_map."""
        vs = _make_vs_config(
            tool_mappings=[
                ToolMapping(tool_name="search", backend_server_path="/github"),
            ],
        )
        mock_server_repository.get.return_value = {
            "server_name": "GitHub",
            "tool_list": [
                {"name": "search", "description": "Search", "inputSchema": {}},
            ],
        }

        written_data = {}

        def capture_write(data, f, **kwargs):
            written_data.update(data)

        with (
            patch("registry.core.nginx_service.Path") as mock_path_cls,
            patch("json.dump", side_effect=capture_write),
        ):
            mock_mappings_dir = MagicMock()
            mock_path_cls.return_value = mock_mappings_dir
            mock_mapping_file = MagicMock()
            mock_mappings_dir.__truediv__ = MagicMock(return_value=mock_mapping_file)

            m = mock_open()
            with patch("builtins.open", m):
                from registry.core.nginx_service import NginxConfigService

                service = NginxConfigService()
                await service._write_virtual_server_mappings([vs])

        assert "tool_backend_map" in written_data
        assert "search" in written_data["tool_backend_map"]
        assert "/_vs_backend" in written_data["tool_backend_map"]["search"]["backend_location"]


class TestSanitizePathForLocation:
    """Tests for _sanitize_path_for_location."""

    def test_sanitize_simple_path(self):
        """Test sanitizing a simple server path."""
        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        assert service._sanitize_path_for_location("/github") == "_github"

    def test_sanitize_path_with_hyphens(self):
        """Test sanitizing a path with hyphens."""
        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        assert service._sanitize_path_for_location("/my-server") == "_my_server"

    def test_sanitize_path_with_dots(self):
        """Test sanitizing a path with dots."""
        from registry.core.nginx_service import NginxConfigService

        service = NginxConfigService()
        result = service._sanitize_path_for_location("/ai.smithery-test")
        assert "/" not in result
        assert "-" not in result
        assert "." not in result


class TestIsHostResolvableAtStartup:
    """Tests for the upstream host resolvability heuristic."""

    def test_fqdn_is_resolvable(self):
        """A dotted hostname (FQDN) is safe to resolve at config load."""
        from registry.core.nginx_service import NginxConfigService

        assert NginxConfigService._is_host_resolvable_at_startup("api.github.com") is True

    def test_ipv4_is_resolvable(self):
        """An IPv4 literal is safe to resolve at config load."""
        from registry.core.nginx_service import NginxConfigService

        assert NginxConfigService._is_host_resolvable_at_startup("10.0.0.5") is True

    def test_ipv6_is_resolvable(self):
        """An IPv6 literal (contains colons) is safe to resolve at config load."""
        from registry.core.nginx_service import NginxConfigService

        assert NginxConfigService._is_host_resolvable_at_startup("::1") is True

    def test_bare_hostname_is_not_resolvable(self):
        """A bare service name with no dot is not safe to resolve at startup."""
        from registry.core.nginx_service import NginxConfigService

        assert NginxConfigService._is_host_resolvable_at_startup("currenttime-server") is False

    def test_empty_hostname_is_not_resolvable(self):
        """An empty hostname is treated as not safe."""
        from registry.core.nginx_service import NginxConfigService

        assert NginxConfigService._is_host_resolvable_at_startup("") is False
