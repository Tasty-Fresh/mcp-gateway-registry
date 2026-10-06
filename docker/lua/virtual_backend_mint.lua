-- virtual_backend_mint.lua
--
-- Runs in the rewrite phase of a generated /_vs_backend_* location whose virtual
-- backend uses gateway egress auth (oauth_user / pat / obo_exchange).
--
-- nginx's auth_request directive only runs on the MAIN request (r == r->main),
-- never on ngx.location.capture subrequests, so the /_vs_backend_* location cannot
-- use `auth_request /validate` + `auth_request_set` to mint its backend-bound
-- X-Internal-Token. Instead we mint it here by capturing the sibling
-- /_vs_validate_* location (generated alongside this one, which proxies to
-- auth-server /validate with the resolved backend markers + the nginx source
-- marker), then expose the minted token as $auth_internal_token for the
-- following proxy_pass to the auth-server /mcp-proxy hop.
--
-- Fail closed: if the sibling validate hop returns non-200 or no token, the
-- subrequest is aborted with that status so mcp_proxy is never reached without a
-- verified backend-bound token.

local validate_location = ngx.var.vs_validate_location
if not validate_location or validate_location == "" then
    ngx.log(ngx.ERR, "virtual_backend_mint: vs_validate_location not set")
    ngx.status = ngx.HTTP_INTERNAL_SERVER_ERROR
    ngx.say("virtual backend auth failed")
    return ngx.exit(ngx.HTTP_INTERNAL_SERVER_ERROR)
end

ngx.req.read_body()
local body = ngx.req.get_body_data()

local res = ngx.location.capture(validate_location, {
    method = ngx.HTTP_POST,
    body = body,
})

if not res then
    ngx.log(ngx.ERR, "virtual_backend_mint: no response from ", validate_location)
    ngx.status = ngx.HTTP_INTERNAL_SERVER_ERROR
    ngx.say("virtual backend auth failed")
    return ngx.exit(ngx.HTTP_INTERNAL_SERVER_ERROR)
end

if res.status ~= ngx.HTTP_OK then
    ngx.log(ngx.ERR, "virtual_backend_mint: ", validate_location,
        " returned ", res.status)
    ngx.status = res.status
    ngx.say("virtual backend auth failed")
    return ngx.exit(res.status)
end

local token = nil
if res.header then
    token = res.header["X-Internal-Token"] or res.header["x-internal-token"]
end

if not token or token == "" then
    ngx.log(ngx.ERR, "virtual_backend_mint: no X-Internal-Token from ", validate_location)
    ngx.status = ngx.HTTP_UNAUTHORIZED
    ngx.say("missing internal proxy token")
    return ngx.exit(ngx.HTTP_UNAUTHORIZED)
end

ngx.var.auth_internal_token = token
