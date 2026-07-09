"""The agent graph.

A minimal but real LangGraph `StateGraph`: `prepare` assembles the prompt (system
+ optional retrieved context + history), `agent` calls the LLM through LiteLLM.
Keeping the LLM call behind `ai_os_shared.llm` means the graph is provider-agnostic
and the same graph powers both the streamed and non-streamed endpoints.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ai_os_shared.llm import get_llm

SYSTEM_PROMPT = (
    "You are the Industry AI OS assistant. You are tenant-scoped and must only use "
    "information available within the current tenant's context. Be concise and cite "
    "retrieved context when you use it."
)


class ChatState(TypedDict, total=False):
    # `messages` is the authoritative, ordered conversation. Nodes overwrite it
    # (no reducer) so ordering stays explicit: system, then context, then history.
    messages: list[dict]
    context: str
    model: str
    answer: str


async def _prepare(state: ChatState) -> ChatState:
    """Put the system prompt and any retrieved RAG context in front of history."""
    prefix: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if state.get("context"):
        prefix.append(
            {"role": "system", "content": f"Relevant context:\n{state['context']}"}
        )
    return {"messages": prefix + list(state.get("messages", []))}


async def _agent(state: ChatState) -> ChatState:
    answer = await get_llm().chat(state["messages"], model=state.get("model"))
    return {
        "answer": answer,
        "messages": list(state["messages"]) + [{"role": "assistant", "content": answer}],
    }


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("prepare", _prepare)
    g.add_node("agent", _agent)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_edge("agent", END)
    return g.compile()


GRAPH = build_graph()
