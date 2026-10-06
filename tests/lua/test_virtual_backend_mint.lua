-- Regression tests for the virtual backend egress token minting helper
-- (issue #1607). virtual_backend_mint.lua is a rewrite_by_lua_file script, not a
-- module, so these tests mock ngx and dofile() the script, capturing ngx.exit()
-- to observe the abort status.
--
-- Run from the repo root with OpenResty's resty CLI:
--   resty tests/lua/test_virtual_backend_mint.lua

local failures = 0
local function check(cond, msg)
    if cond then
        print("  ok   - " .. msg)
    else
        failures = failures + 1
        print("  FAIL - " .. msg)
    end
end

local M = {}
M.capture_responses = {}
M.exit_status = nil
M.var = { vs_validate_location = "/_vs_validate_freshguard", auth_internal_token = nil }

_G.ngx = {
    var = M.var,
    req = {
        read_body = function() end,
        get_body_data = function() return '{"method":"tools/call"}' end,
    },
    location = {
        capture = function(loc, _opts) return M.capture_responses[loc] end,
    },
    log = function() end,
    say = function() end,
    exit = function(status) M.exit_status = status end,
    status = 0,
    HTTP_POST = 8,
    HTTP_OK = 200,
    HTTP_UNAUTHORIZED = 401,
    HTTP_INTERNAL_SERVER_ERROR = 500,
    ERR = 4,
}

local function run()
    M.exit_status = nil
    M.var.auth_internal_token = nil
    dofile("docker/lua/virtual_backend_mint.lua")
end

-- Success: validate hop returns a token -> set $auth_internal_token, no abort.
M.capture_responses["/_vs_validate_freshguard"] = {
    status = 200, body = "ok", header = { ["X-Internal-Token"] = "tok-123" },
}
run()
check(M.var.auth_internal_token == "tok-123", "success sets $auth_internal_token")
check(M.exit_status == nil, "success does not abort")

-- Non-200 from the validate hop -> abort with that status, no token.
M.capture_responses["/_vs_validate_freshguard"] = { status = 401, body = "denied" }
run()
check(M.var.auth_internal_token == nil, "401 does not set $auth_internal_token")
check(M.exit_status == 401, "401 aborts with 401")

-- 200 without a token -> abort with 401, no token.
M.capture_responses["/_vs_validate_freshguard"] = { status = 200, body = "ok", header = {} }
run()
check(M.var.auth_internal_token == nil, "missing token does not set $auth_internal_token")
check(M.exit_status == 401, "missing token aborts with 401")

if failures > 0 then
    print(string.format("\n%d check(s) FAILED", failures))
    os.exit(1)
end

print("\nAll checks passed")
