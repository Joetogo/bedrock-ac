# Verified SDK contract (Task 1 probe)

Captured by running the actual SDK against a real venv (`.venv-agent`, Python 3.12) on
2026-07-08. All method names below are copy-pasted from the interpreter, not assumed.

## Installed versions

| Package | Version |
|---|---|
| strands-agents | 1.45.0 |
| bedrock-agentcore | 1.17.0 |
| bedrock-agentcore-starter-toolkit | 0.3.10 |
| boto3 | 1.43.42 |
| httpx | 0.28.1 |
| mcp | 1.28.1 |

Python used for the venv: 3.12 (via `py -3.12 -m venv .venv-agent`). Note: the system default
`python` on this machine resolves to 3.14, which was avoided for the venv to reduce the risk of
missing prebuilt wheels for compiled deps (e.g. pydantic-core, cryptography, awscrt); all of the
above installed cleanly on 3.12 with prebuilt wheels.

## `bedrock_agentcore.runtime.BedrockAgentCoreApp`

```
app = BedrockAgentCoreApp()
hasattr(app, 'entrypoint') -> True
hasattr(app, 'run') -> True
```

Confirmed: `BedrockAgentCoreApp` exposes both `.entrypoint` (decorator) and `.run()` as the plan
assumes.

## `bedrock_agentcore.memory.MemoryClient`

Full public method list (`[m for m in dir(MemoryClient) if not m.startswith('_')]`):

```
add_custom_episodic_strategy, add_custom_episodic_strategy_and_wait,
add_custom_semantic_strategy, add_custom_semantic_strategy_and_wait,
add_episodic_strategy, add_episodic_strategy_and_wait, add_semantic_strategy,
add_semantic_strategy_and_wait, add_strategy, add_summary_strategy,
add_summary_strategy_and_wait, add_user_preference_strategy,
add_user_preference_strategy_and_wait, create_blob_event, create_event, create_memory,
create_memory_and_wait, create_or_get_memory, delete_memory, delete_memory_and_wait,
delete_strategy, fork_conversation, get_conversation_tree, get_last_k_turns,
get_memory_status, get_memory_strategies, list_branch_events, list_branches, list_events,
list_memories, merge_branch_context, modify_strategy, process_turn_with_llm,
retrieve_memories, save_conversation, update_memory_strategies,
update_memory_strategies_and_wait, wait_for_memories
```

Relevant methods this project depends on, with real signatures (`inspect.signature`):

- **Create event**: `create_event`
  ```
  create_event(self, memory_id: str, actor_id: str, session_id: str,
               messages: List[Tuple[str, str]],
               event_timestamp: Optional[datetime.datetime] = None,
               branch: Optional[Dict[str, str]] = None,
               metadata: Optional[Dict[str, StringValue]] = None,
               extraction_mode: Optional[str] = None) -> Dict[str, Any]
  ```

- **List events**: `list_events`
  ```
  list_events(self, memory_id: str, actor_id: str, session_id: str,
              branch_name: Optional[str] = None,
              include_parent_branches: bool = False,
              event_metadata: Optional[List[EventMetadataFilter]] = None,
              max_results: int = 100,
              include_payload: bool = True) -> List[Dict[str, Any]]
  ```

- **Create memory resource (create-and-wait for ACTIVE status)**: `create_memory_and_wait`
  ```
  create_memory_and_wait(self, name: str, strategies: List[Dict[str, Any]],
                          description: Optional[str] = None,
                          event_expiry_days: int = 90,
                          memory_execution_role_arn: Optional[str] = None,
                          stream_delivery_resources: Optional[Dict[str, Any]] = None,
                          max_wait: int = 300, poll_interval: int = 10,
                          indexed_keys: Optional[List[IndexedKey]] = None) -> Dict[str, Any]
  ```
  (Non-waiting variant also exists: `create_memory(...)`, same shape minus `max_wait`/`poll_interval`.)

## `strands.tools.mcp.MCPClient`

Full public method list:

```
add_consumer, call_tool_async, call_tool_sync, get_prompt_sync, list_prompts_sync,
list_resource_templates_sync, list_resources_sync, list_tools_sync, load_tools,
map_mcp_content_to_tool_result_content, read_resource_sync, remove_consumer, start, stop
```

- **List tools**: `list_tools_sync`
  ```
  list_tools_sync(self, pagination_token: str | None = None, prefix: str | None = None,
                   tool_filters: ToolFilters | None = None)
      -> PaginatedList[MCPAgentTool]
  ```

## Other confirmed imports

- `strands.Agent` — imports cleanly.
- `strands.models.BedrockModel` — imports cleanly.
- `strands.tools.mcp.MCPClient` — imports cleanly.

## DEVIATIONS

None. Every method name the plan assumed was verified exactly as named:

- Memory create-event: `create_event` (matches assumption)
- Memory list-events: `list_events` (matches assumption)
- Memory create-resource (wait variant): `create_memory_and_wait` (matches assumption)
- MCP list-tools: `list_tools_sync` (matches assumption)

Downstream tasks (`agent/agent.py`) can use these names directly. Keep all direct calls to
these SDK methods confined to wrapper functions in `agent/agent.py` per the plan, in case a
future SDK version renames/reshapes them.
