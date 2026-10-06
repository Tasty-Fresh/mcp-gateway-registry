-- Regression tests for the virtual MCP server router's tools/list aggregation
-- (PR #1634 / originally #1526). Covers the fixed behaviors:
--   * a failed backend falls back to its mapping-file metadata (complete list),
--   * a genuine empty tools array is a success, not a failure,
--   * truncated / malformed backend responses are failures,
--   * partial (fallback) results are NOT cached; fully-discovered results ARE.
--
-- Run from the repo root with OpenResty's resty CLI (provides lua-cjson):
--   resty tests/lua/test_virtual_router.lua
-- Or via Docker without a local OpenResty install:
--   docker run --rm -v "$PWD":/app -w /app openresty/openresty:alpine \
--     resty tests/lua/test_virtual_router.lua
--
-- The router file is a content_by_lua script; it exposes its internal helpers
-- and returns the module (instead of executing) when _G._VR_TEST is set.

_G._VR_TEST = true

local cjson = require("cjson")

-- Environment guard (issue #1532): the empty-array fix relies on
-- cjson.empty_array_mt, an OpenResty lua-cjson extension. If this suite is ever
-- run against a cjson without it (e.g. Debian lua-cjson 2.1.0, which is what the
-- registry image used to ship), the schema-array assertions below would give a
-- false sense of safety. Fail loudly instead of passing on the wrong runtime.
assert(cjson.empty_array_mt,
    "cjson.empty_array_mt is missing -- these tests require OpenResty's lua-cjson")

local failures = 0
local function check(cond, msg)
    if cond then
        print("  ok   - " .. msg)
    else
        failures = failures + 1
        print("  FAIL - " .. msg)
    end
end

-- Minimal ngx.shared dict mock.
local function _new_dict()
    local store = {}
    return {
        get = function(_, k) return store[k] end,
        set = function(_, k, v) store[k] = v; return true end,
        delete = function(_, k) store[k] = nil end,
        _store = store,
    }
end

local dict = _new_dict()

-- Programmable ngx.location.capture responses, keyed by backend location.
local capture_responses = {}

_G.ngx = {
    shared = { virtual_server_map = dict },
    location = {
        capture = function(loc, _opts) return capture_responses[loc] end,
    },
    -- Stateful request-header mock so tests can seed a client-supplied header
    -- and observe whether set_header overwrote it or clear_header removed it.
    req = {
        _headers = {},
        set_header = function(k, v) _G.ngx.req._headers[k] = v end,
        clear_header = function(k) _G.ngx.req._headers[k] = nil end,
    },
    log = function() end,
    ERR = 4,
    WARN = 5,
    HTTP_POST = 8,
    escape_uri = function(s) return s end,
    var = { request_id = "0" },
    status = 200,
    say = function() end,
    exit = function() end,
}

-- Load the router with the test hook active; it returns the module table.
local M = assert(loadfile("docker/lua/virtual_router.lua"))()

-- ---------------------------------------------------------------------------
print("test: _append_mapping_tools_for_backend appends only the given backend")
do
    local mapping = { tools = {
        { name = "a", backend_location = "/b1", inputSchema = { type = "object" } },
        { name = "b", backend_location = "/b2" },
    } }
    local enriched = {}
    M._append_mapping_tools_for_backend(enriched, mapping, "/b1")
    check(#enriched == 1, "only one tool appended for /b1")
    check(enriched[1] and enriched[1].name == "a", "the appended tool is 'a'")
end

-- ---------------------------------------------------------------------------
print("test: _fetch_backend_tools_list classifies responses correctly")
do
    -- success with tools
    capture_responses["/loc"] = { status = 200,
        body = cjson.encode({ result = { tools = { { name = "x" } } } }) }
    local tools, ok = M._fetch_backend_tools_list("/loc", nil, "srv")
    check(ok == true and #tools == 1, "200 + tools -> (tools, true)")

    -- genuine empty list is success
    capture_responses["/loc"] = { status = 200,
        body = cjson.encode({ result = { tools = {} } }) }
    tools, ok = M._fetch_backend_tools_list("/loc", nil, "srv")
    check(ok == true and #tools == 0, "200 + empty tools -> ([], true)")

    -- http error is failure
    capture_responses["/loc"] = { status = 500, body = "" }
    tools, ok = M._fetch_backend_tools_list("/loc", nil, "srv")
    check(ok == false, "500 -> (_, false)")

    -- truncated is failure
    capture_responses["/loc"] = { status = 200, truncated = true,
        body = cjson.encode({ result = { tools = { { name = "x" } } } }) }
    tools, ok = M._fetch_backend_tools_list("/loc", nil, "srv")
    check(ok == false, "truncated -> (_, false)")

    -- missing tools array is failure
    capture_responses["/loc"] = { status = 200, body = cjson.encode({ result = {} }) }
    tools, ok = M._fetch_backend_tools_list("/loc", nil, "srv")
    check(ok == false, "missing tools array -> (_, false)")
end

-- ---------------------------------------------------------------------------
print("test: _handle_tools_list falls back per-backend and does NOT cache partial")
do
    dict._store["tools_enriched:srv1"] = nil
    local mapping = { required_scopes = nil, tools = {
        { name = "live_tool", original_name = "live_tool", backend_location = "/ok",
          inputSchema = { type = "object" } },
        { name = "down_tool", original_name = "down_tool", backend_location = "/down",
          inputSchema = { type = "object" } },
    } }
    capture_responses = {}
    capture_responses["/ok"] = { status = 200,
        body = cjson.encode({ result = { tools = { { name = "live_tool", description = "live" } } } }) }
    capture_responses["/down"] = { status = 500, body = "" }

    local resp = M._handle_tools_list("1", mapping, "", nil, "srv1")
    local decoded = cjson.decode(resp)
    local names = {}
    for _, t in ipairs(decoded.result.tools) do names[t.name] = true end
    check(names["live_tool"] == true, "live backend tool present")
    check(names["down_tool"] == true, "failed backend tool present via fallback")
    check(dict._store["tools_enriched:srv1"] == nil, "partial/fallback result is NOT cached")
end

-- ---------------------------------------------------------------------------
print("test: _handle_tools_list caches when every backend succeeds")
do
    dict._store["tools_enriched:srv2"] = nil
    local mapping = { required_scopes = nil, tools = {
        { name = "live_tool", original_name = "live_tool", backend_location = "/ok",
          inputSchema = { type = "object" } },
        { name = "down_tool", original_name = "down_tool", backend_location = "/down",
          inputSchema = { type = "object" } },
    } }
    capture_responses = {}
    capture_responses["/ok"] = { status = 200,
        body = cjson.encode({ result = { tools = { { name = "live_tool" } } } }) }
    capture_responses["/down"] = { status = 200,
        body = cjson.encode({ result = { tools = { { name = "down_tool" } } } }) }

    M._handle_tools_list("1", mapping, "", nil, "srv2")
    check(dict._store["tools_enriched:srv2"] ~= nil, "fully-discovered result IS cached")
end

-- ---------------------------------------------------------------------------
print("test: _forward_identity_headers overwrites a spoofed client X-User")
do
    -- Client tries to spoof identity; a validated user is present.
    ngx.req._headers = { ["X-User"] = "attacker", ["X-Username"] = "attacker" }
    ngx.var.auth_user = "alice"
    ngx.var.auth_username = "alice@corp"
    M._forward_identity_headers()
    check(ngx.req._headers["X-User"] == "alice",
        "validated auth_user overwrites the client-supplied X-User")
    check(ngx.req._headers["X-Username"] == "alice@corp",
        "validated auth_username overwrites the client-supplied X-Username")
end

-- ---------------------------------------------------------------------------
print("test: _forward_identity_headers CLEARS a spoofed X-User when auth_user is empty")
do
    -- M2M / client-credentials token: authenticates but carries no user, so
    -- nginx auth_request_set yields "" for $auth_user. A client-supplied X-User
    -- must NOT survive to the backend (this is the fail-open bug from #1627).
    ngx.req._headers = { ["X-User"] = "attacker", ["X-Username"] = "attacker" }
    ngx.var.auth_user = ""
    ngx.var.auth_username = ""
    M._forward_identity_headers()
    check(ngx.req._headers["X-User"] == nil,
        "empty auth_user clears the client-supplied X-User (no spoof passthrough)")
    check(ngx.req._headers["X-Username"] == nil,
        "empty auth_username clears the client-supplied X-Username")
end

-- ---------------------------------------------------------------------------
print("test: _forward_identity_headers CLEARS a spoofed X-User when auth_user is nil")
do
    ngx.req._headers = { ["X-User"] = "attacker", ["X-Username"] = "attacker" }
    ngx.var.auth_user = nil
    ngx.var.auth_username = nil
    M._forward_identity_headers()
    check(ngx.req._headers["X-User"] == nil,
        "nil auth_user clears the client-supplied X-User")
    check(ngx.req._headers["X-Username"] == nil,
        "nil auth_username clears the client-supplied X-Username")
end

-- ---------------------------------------------------------------------------
print("test: _handle_tools_list keeps empty schema arrays as [] (issue #1532)")
do
    dict._store["tools_enriched:srv3"] = nil
    local mapping = { required_scopes = nil, tools = {
        { name = "no_arg", original_name = "no_arg", backend_location = "/ok" },
        { name = "one_arg", original_name = "one_arg", backend_location = "/ok" },
    } }
    capture_responses = {}
    capture_responses["/ok"] = { status = 200, body = cjson.encode({ result = { tools = {
        { name = "no_arg", inputSchema = {
            type = "object", properties = {},
            required = setmetatable({}, cjson.empty_array_mt) } },
        { name = "one_arg", inputSchema = {
            type = "object",
            properties = { q = { type = "string",
                                 enum = setmetatable({}, cjson.empty_array_mt) } },
            required = { "q" } } },
    } } }) }

    local body = M._handle_tools_list("1", mapping, "", nil, "srv3")
    check(body:find('"required":[]', 1, true) ~= nil,
        "empty required serializes as [] not {}")
    check(body:find('"required":["q"]', 1, true) ~= nil,
        "non-empty required is still an array")
    check(body:find('"properties":{}', 1, true) ~= nil,
        "empty properties stays an object")
    check(body:find('"enum":[]', 1, true) ~= nil,
        "empty enum nested under properties serializes as []")

    -- The cached path decodes and re-encodes, so it must hold the same shape.
    local cached_body = M._handle_tools_list("1", mapping, "", nil, "srv3")
    check(dict._store["tools_enriched:srv3"] ~= nil, "result was cached")
    check(cached_body:find('"required":[]', 1, true) ~= nil,
        "empty required is still [] when served from cache")
end

-- ---------------------------------------------------------------------------
print("test: a property named like a schema keyword stays an object")
do
    dict._store["tools_enriched:srv4"] = nil
    local mapping = { required_scopes = nil, tools = {
        { name = "odd", original_name = "odd", backend_location = "/ok" },
    } }
    capture_responses = {}
    capture_responses["/ok"] = { status = 200, body = cjson.encode({ result = { tools = {
        { name = "odd", inputSchema = {
            type = "object", properties = { required = { type = "object" } } } },
    } } }) }

    local body = M._handle_tools_list("1", mapping, "", nil, "srv4")
    check(body:find('"required":[]', 1, true) == nil,
        "a property called 'required' is not coerced into an array")
end

-- ---------------------------------------------------------------------------
print("test: _initialize_backend distinguishes stateless vs stateful vs failure")
do
    ngx.var.auth_user = "alice"
    ngx.var.auth_username = "alice@corp"

    -- 200 with no Mcp-Session-Id -> stateless sentinel
    capture_responses["/stateless"] = { status = 200,
        body = cjson.encode({ result = {} }), header = {} }
    local sid = M._initialize_backend("/stateless")
    check(M._is_stateless_session(sid), "200 without Mcp-Session-Id -> stateless sentinel")

    -- 200 with a real session id -> the id
    capture_responses["/stateful"] = { status = 200,
        body = cjson.encode({ result = {} }),
        header = { ["Mcp-Session-Id"] = "backend-sess-1" } }
    local sid2 = M._initialize_backend("/stateful")
    check(sid2 == "backend-sess-1", "200 with Mcp-Session-Id -> session id")

    -- non-200 -> nil (failure, not stateless)
    capture_responses["/broken"] = { status = 500, body = "" }
    local sid3 = M._initialize_backend("/broken")
    check(sid3 == nil, "non-200 -> nil (failure)")
end

-- ---------------------------------------------------------------------------
print("test: stateless backend is cached in L1 and does not re-initialize")
do
    ngx.var.auth_user = "alice"
    ngx.var.auth_username = "alice@corp"

    dict._store["bsess:vs-stateless:/backend"] = nil

    local backend_calls = 0
    local real_capture = ngx.location.capture
    ngx.location.capture = function(loc, _opts)
        if loc == "/backend" then
            backend_calls = backend_calls + 1
        end
        return capture_responses[loc]
    end

    capture_responses["/backend"] = { status = 200,
        body = cjson.encode({ result = {} }), header = {} }
    capture_responses["/_internal/sessions/backend/vs-stateless:/backend?user_id=alice"] =
        { status = 404, body = "" }

    local s1 = M._get_backend_session("vs-stateless", "/backend", "srv")
    check(M._is_stateless_session(s1), "first call returns the stateless sentinel")
    check(backend_calls == 1, "first call initializes the backend exactly once")

    local s2 = M._get_backend_session("vs-stateless", "/backend", "srv")
    check(M._is_stateless_session(s2), "second call returns the stateless sentinel")
    check(backend_calls == 1, "second call does NOT re-initialize (L1 sentinel hit)")
    check(dict._store["bsess:vs-stateless:/backend"] == "__stateless__",
        "stateless sentinel is cached in L1")

    ngx.location.capture = real_capture
end

-- ---------------------------------------------------------------------------
print("test: _set_backend_session_header never sends the sentinel on the wire")
do
    M._set_backend_session_header("__stateless__")
    check(ngx.req._headers["Mcp-Session-Id"] == "",
        "stateless sentinel clears Mcp-Session-Id")

    M._set_backend_session_header("backend-sess-2")
    check(ngx.req._headers["Mcp-Session-Id"] == "backend-sess-2",
        "real session id is set")

    M._set_backend_session_header(nil)
    check(ngx.req._headers["Mcp-Session-Id"] == "",
        "nil session clears Mcp-Session-Id")
end

-- ---------------------------------------------------------------------------
print("test: _initialize_backend sends notifications/initialized with the session id")
do
    ngx.var.auth_user = "alice"

    local captured = {}
    local real_capture = ngx.location.capture
    ngx.location.capture = function(loc, opts)
        captured[#captured + 1] = { loc = loc, opts = opts }
        return capture_responses[loc]
    end

    capture_responses["/_vs_backend_freshguard"] = {
        status = 200,
        body = cjson.encode({ result = {} }),
        header = { ["Mcp-Session-Id"] = "fg-X" },
    }

    local sid = M._initialize_backend("/_vs_backend_freshguard")
    check(sid == "fg-X", "initialize returns the backend session id")

    check(#captured == 2, "two captures: initialize + notifications/initialized")
    local n = captured[2]
    check(n and n.loc == "/_vs_backend_freshguard",
        "second capture targets the backend")
    local ok_dec, notif_data = pcall(cjson.decode, n and n.opts and n.opts.body or "")
    check(ok_dec and notif_data.method == "notifications/initialized",
        "second capture body is notifications/initialized")
    check(ngx.req._headers["Mcp-Session-Id"] == "fg-X",
        "notifications/initialized is sent with the backend session id")

    ngx.location.capture = real_capture
end

-- ---------------------------------------------------------------------------
print("test: stateful backend session is initialized once and reused (L1)")
do
    ngx.var.auth_user = "alice"
    dict._store["bsess:vs-A:/_vs_backend_freshguard"] = nil

    local backend_calls = 0
    local real_capture = ngx.location.capture
    ngx.location.capture = function(loc, _opts)
        if loc == "/_vs_backend_freshguard" then backend_calls = backend_calls + 1 end
        return capture_responses[loc]
    end

    capture_responses["/_vs_backend_freshguard"] = {
        status = 200, body = cjson.encode({ result = {} }),
        header = { ["Mcp-Session-Id"] = "fg-X" },
    }
    capture_responses["/_internal/sessions/backend/vs-A:/_vs_backend_freshguard?user_id=alice"] =
        { status = 404, body = "" }

    local s1 = M._get_backend_session("vs-A", "/_vs_backend_freshguard", "tasty")
    check(s1 == "fg-X", "first call initializes and returns fg-X")

    local s2 = M._get_backend_session("vs-A", "/_vs_backend_freshguard", "tasty")
    check(s2 == "fg-X", "second call reuses the backend session from L1 cache")
    check(backend_calls > 0, "backend was initialized (L2 miss path)")
    check(dict._store["bsess:vs-A:/_vs_backend_freshguard"] == "fg-X",
        "backend session persisted in L1 cache")

    ngx.location.capture = real_capture
end

-- ---------------------------------------------------------------------------
print("test: backend session header is the backend id, never the virtual (vs-) id")
do
    M._set_backend_session_header("fg-X")
    check(ngx.req._headers["Mcp-Session-Id"] == "fg-X",
        "backend session id set on Mcp-Session-Id")
    check(ngx.req._headers["Mcp-Session-Id"] ~= "vs-A",
        "virtual session id (vs-A) is never set as Mcp-Session-Id")
    M._set_backend_session_header(nil)
    check(ngx.req._headers["Mcp-Session-Id"] == "",
        "cleared when no backend session")
end

-- ---------------------------------------------------------------------------
if failures > 0 then
    print(string.format("\n%d check(s) FAILED", failures))
    os.exit(1)
end
print("\nAll checks passed")
