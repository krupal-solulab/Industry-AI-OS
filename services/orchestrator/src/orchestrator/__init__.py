"""AI Orchestrator — stateful agents (LangGraph) over the LiteLLM gateway.

Provider-agnostic: models are referenced by alias and resolved by LiteLLM. Every
turn is traced in Langfuse. The LangGraph graph is deliberately small here (one
agent node with optional RAG context) but is the extension point for multi-agent
and human-in-the-loop flows in later milestones.
"""
