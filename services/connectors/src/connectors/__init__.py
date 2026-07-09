"""Connector Hub.

The single boundary between the platform and external enterprise systems
(Microsoft, Google, CRM, ERP, SharePoint...). Nothing else in the platform calls a
third-party API. Connectors follow the MCP tool model: each exposes a set of tools
with typed inputs, invoked through one uniform internal interface. Breadth comes from
MCP servers and Composio rather than hand-written API clients.
"""
