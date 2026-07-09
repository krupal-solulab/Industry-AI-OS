"""Workflow service — durable, human-in-the-loop execution on Temporal.

Ships ONE generic, industry-neutral workflow: document-review-with-human-approval
(submit -> AI summarize -> human approve/reject -> audit). Industry-specific
workflows are added later as packs; the platform only provides the reusable engine
integration and this reference flow.
"""
