"""The agentic tool-calling loop shared by the cheminformatics and critic agents.

Call llm.chat() with tools; if the reply carries tool_calls, execute each via
the caller-supplied executor, append the results as role="tool" messages, and
loop. Stops when the model replies with plain text instead, or after
max_iters — always returns a best-effort string, never raises.

Tool failures aren't swallowed: the error message goes back to the model as
the tool result, so it can see what went wrong and try something else. The
tool_call event's "error"/"retry" status is what makes that visible on the wire.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from . import llm as llm_module
from .store import emit

ToolExecutor = Callable[[str, dict], Awaitable[dict]]


async def run_tool_loop(
    run,
    agent_name: str,
    system_prompt: str,
    user_msg: str,
    tools: list,
    executor: ToolExecutor,
    cfg=None,
    max_iters: int = 6,
) -> str:
    """
    Run the LLM tool-calling loop for one agent, emitting events to the run's SSE
    stream. Returns the final text output (or empty string if the model never
    produced plain text)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]
    prompt_tokens = 0
    completion_tokens = 0
    final_text = ""
    errored_tools: set = set()

    for iteration in range(1, max_iters + 1):
        # Small local models sometimes narrate a plan in prose instead of
        # calling a tool on the first turn. Force real action on iteration 1;
        # after that, let the model decide when it's actually done.
        tool_choice = "required" if iteration == 1 else "auto"
        try:
            resp = await llm_module.chat(
                messages=messages, tools=tools, cfg=cfg, tool_choice=tool_choice
            )
        except Exception as e:
            emit(
                run,
                {
                    "type": "tool_call",
                    "agent": agent_name,
                    "payload": {
                        "iteration": iteration,
                        "thought": "",
                        "tool": None,
                        "args": {},
                        "result_summary": f"LLM call failed: {e}",
                        "status": "error",
                    },
                },
            )
            break

        usage = getattr(resp, "usage", None)
        if usage is not None:
            prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        msg = resp.choices[0].message
        thought = msg.content or ""
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            final_text = thought
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            args: dict = {}
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception as e:
                result_str = f"could not parse tool arguments as JSON: {e}"
                status = "error"
                errored_tools.add(name)
            else:
                try:
                    result_obj = await executor(name, args)
                    result_str = json.dumps(result_obj)
                    status = "retry" if name in errored_tools else "ok"
                    errored_tools.discard(name)
                except Exception as e:
                    result_str = str(e)
                    status = "error"
                    errored_tools.add(name)

            emit(
                run,
                {
                    "type": "tool_call",
                    "agent": agent_name,
                    "payload": {
                        "iteration": iteration,
                        "thought": thought,
                        "tool": name,
                        "args": args,
                        "result_summary": result_str[:600],
                        "status": status,
                    },
                },
            )

            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_str}
            )

        if iteration == max_iters:
            final_text = thought  # hard stop — best-effort, never raise

    emit(
        run,
        {
            "type": "tool_call",
            "agent": agent_name,
            "payload": {
                "iteration": -1,
                "thought": "",
                "tool": None,
                "args": {},
                "result_summary": (
                    f"loop done · {prompt_tokens + completion_tokens} tokens "
                    f"(prompt {prompt_tokens} / completion {completion_tokens})"
                ),
                "status": "ok",
            },
        },
    )

    return final_text
