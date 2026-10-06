/**
 * Shared Virtual MCP Server type definitions for the MCP Gateway Registry frontend.
 *
 * These interfaces mirror the backend Pydantic models defined in
 * registry/schemas/virtual_server_models.py.
 */


/**
 * Maps a tool from a backend server into a virtual server.
 *
 * Each mapping selects a specific tool from a backend MCP server,
 * optionally renaming it (alias) and pinning it to a specific version.
 */
export interface ToolMapping {
  tool_name: string;
  alias?: string | null;
  backend_server_path: string;
  backend_version?: string | null;
  description_override?: string | null;
}


/**
 * Separator used between the backend-path namespace and the original tool name
 * in an auto-namespaced virtual tool name. Must stay in sync with
 * TOOL_NAMESPACE_SEPARATOR in registry/schemas/virtual_server_models.py.
 */
export const TOOL_NAMESPACE_SEPARATOR = '__';


/**
 * Compute the client-visible (effective) virtual tool name.
 *
 * An explicit alias always wins. Otherwise the tool is auto-namespaced from its
 * registered backend server path (operator-controlled, short, unique, stable):
 *   <backend path without leading "/"> + "__" + <original tool name>
 *
 * Mirrors build_effective_tool_name() in the backend schema.
 */
export function buildEffectiveToolName(mapping: ToolMapping): string {
  if (mapping.alias) {
    return mapping.alias;
  }
  return `${mapping.backend_server_path.replace(/^\/+/, '')}${TOOL_NAMESPACE_SEPARATOR}${mapping.tool_name}`;
}


/**
 * Per-tool scope override for fine-grained access control.
 *
 * Allows requiring additional scopes to see or call specific tools
 * beyond the virtual server's base required_scopes.
 */
export interface ToolScopeOverride {
  tool_alias: string;
  required_scopes: string[];
}


/**
 * Full virtual MCP server configuration.
 *
 * A virtual server aggregates tools from multiple backend MCP servers
 * into a single endpoint. It supports tool aliasing, version pinning,
 * and scope-based access control.
 */
export interface VirtualServerConfig {
  path: string;
  server_name: string;
  description: string;
  tool_mappings: ToolMapping[];
  required_scopes: string[];
  tool_scope_overrides: ToolScopeOverride[];
  is_enabled: boolean;
  tags: string[];
  supported_transports: string[];
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}


/**
 * Lightweight virtual server summary for listings.
 *
 * Optionally includes detailed fields (tool_mappings, required_scopes, etc.)
 * when the full configuration is needed for display purposes.
 */
/**
 * Rating detail for a virtual server.
 */
export interface RatingDetail {
  user: string;
  rating: number;
}


/**
 * Lightweight virtual server summary for listings.
 *
 * Optionally includes detailed fields (tool_mappings, required_scopes, etc.)
 * when the full configuration is needed for display purposes.
 */
export interface VirtualServerInfo {
  path: string;
  server_name: string;
  description: string;
  tool_count: number;
  backend_count: number;
  backend_paths: string[];
  is_enabled: boolean;
  tags: string[];
  num_stars?: number;
  rating_details?: RatingDetail[];
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  // Optional detailed fields for modal display
  tool_mappings?: ToolMapping[];
  required_scopes?: string[];
  supported_transports?: string[];
}


/**
 * Request model for creating a virtual server.
 */
export interface CreateVirtualServerRequest {
  server_name: string;
  path?: string | null;
  description?: string;
  tool_mappings?: ToolMapping[];
  required_scopes?: string[];
  tool_scope_overrides?: ToolScopeOverride[];
  tags?: string[];
  supported_transports?: string[];
}


/**
 * Request model for updating a virtual server.
 * All fields are optional; only provided fields are updated.
 */
export interface UpdateVirtualServerRequest {
  server_name?: string | null;
  description?: string | null;
  tool_mappings?: ToolMapping[] | null;
  required_scopes?: string[] | null;
  tool_scope_overrides?: ToolScopeOverride[] | null;
  tags?: string[] | null;
  supported_transports?: string[] | null;
}


/**
 * A tool available in the registry, from the global tool catalog.
 *
 * Aggregates tool information across all enabled backend servers.
 */
export interface ToolCatalogEntry {
  tool_name: string;
  server_path: string;
  server_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  available_versions: string[];
}


/**
 * A tool resolved from a virtual server's tool mappings.
 *
 * Contains the final tool name (alias or original), its source backend,
 * and the full tool metadata for serving in tools/list responses.
 */
export interface ResolvedTool {
  name: string;
  original_name: string;
  backend_server_path: string;
  backend_version?: string | null;
  description: string;
  input_schema: Record<string, unknown>;
  required_scopes: string[];
}
