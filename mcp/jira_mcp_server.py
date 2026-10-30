#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
}
ACTIVE_JQL = (
    'assignee = currentUser() AND statusCategory IN ("To Do", "In Progress") '
    "ORDER BY updated DESC"
)
TOOLS = [
    {
        "name": "list_active_issues",
        "description": (
            "Lists every Jira issue assigned to the authenticated user whose "
            "status category is To Do or In Progress."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_issue",
        "description": (
            "Gets a Jira issue with its description, comments, links, subtasks, "
            "parent, and attachment metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "Jira issue key, for example FAR-123",
                }
            },
            "required": ["issue_key"],
            "additionalProperties": False,
        },
    },
]


class JiraClient:
    def __init__(self):
        self.base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        token_file = os.environ.get("JIRA_TOKEN_FILE", "")
        if not self.base_url or not token_file:
            raise RuntimeError("JIRA_BASE_URL and JIRA_TOKEN_FILE must be configured")
        token = Path(token_file).read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("The Jira token file is empty")
        self.token = token

    def request(self, path, params=None):
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read(4096).decode("utf-8", errors="replace")
            message = body
            try:
                payload = json.loads(body)
                messages = payload.get("errorMessages", [])
                errors = payload.get("errors", {})
                parts = messages + [f"{key}: {value}" for key, value in errors.items()]
                if parts:
                    message = "; ".join(parts)
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"Jira returned HTTP {error.code}: {message[:1000]}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"Cannot connect to Jira: {error.reason}") from None

    def list_active_issues(self):
        issues = []
        start_at = 0
        total = None
        while total is None or len(issues) < total:
            payload = self.request(
                "/rest/api/2/search",
                {
                    "jql": ACTIVE_JQL,
                    "startAt": start_at,
                    "maxResults": 100,
                    "fields": "summary,status,priority,updated,issuetype",
                },
            )
            batch = payload.get("issues", [])
            total = payload.get("total", len(batch))
            if not batch:
                break
            issues.extend(self.compact_issue(issue) for issue in batch)
            start_at += len(batch)
        return {"total": len(issues), "issues": issues}

    def get_issue(self, issue_key):
        issue_key = issue_key.strip().upper()
        if not issue_key:
            raise ValueError("issue_key cannot be empty")
        payload = self.request(
            f"/rest/api/2/issue/{urllib.parse.quote(issue_key, safe='')}",
            {
                "fields": (
                    "summary,description,status,priority,assignee,reporter,created,"
                    "updated,issuetype,comment,issuelinks,subtasks,parent,attachment"
                )
            },
        )
        result = self.compact_issue(payload)
        fields = payload.get("fields", {})
        result.update(
            {
                "description": fields.get("description"),
                "created": fields.get("created"),
                "reporter": self.person(fields.get("reporter")),
                "parent": self.issue_reference(fields.get("parent")),
                "subtasks": [self.issue_reference(issue) for issue in fields.get("subtasks", [])],
                "links": [self.issue_link(link) for link in fields.get("issuelinks", [])],
                "comments": [
                    {
                        "author": self.person(comment.get("author")),
                        "created": comment.get("created"),
                        "updated": comment.get("updated"),
                        "body": comment.get("body"),
                    }
                    for comment in fields.get("comment", {}).get("comments", [])
                ],
                "attachments": [
                    {
                        "filename": attachment.get("filename"),
                        "size": attachment.get("size"),
                        "url": attachment.get("content"),
                    }
                    for attachment in fields.get("attachment", [])
                ],
            }
        )
        return result

    def compact_issue(self, issue):
        fields = issue.get("fields", {})
        return {
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "type": fields.get("issuetype", {}).get("name"),
            "status": fields.get("status", {}).get("name"),
            "statusCategory": fields.get("status", {}).get("statusCategory", {}).get("name"),
            "priority": (fields.get("priority") or {}).get("name"),
            "assignee": self.person(fields.get("assignee")),
            "updated": fields.get("updated"),
            "url": f"{self.base_url}/browse/{issue.get('key')}",
        }

    @staticmethod
    def person(value):
        if not value:
            return None
        return value.get("displayName") or value.get("name") or value.get("emailAddress")

    @staticmethod
    def issue_reference(issue):
        if not issue:
            return None
        fields = issue.get("fields", {})
        return {
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "status": fields.get("status", {}).get("name"),
        }

    @staticmethod
    def issue_link(link):
        link_type = link.get("type", {})
        if link.get("outwardIssue"):
            direction = link_type.get("outward")
            linked_issue = link["outwardIssue"]
        else:
            direction = link_type.get("inward")
            linked_issue = link.get("inwardIssue")
        return {
            "relation": direction,
            "issue": JiraClient.issue_reference(linked_issue),
        }


def tool_result(payload):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
    }


def call_tool(name, arguments):
    client = JiraClient()
    if name == "list_active_issues":
        return tool_result(client.list_active_issues())
    if name == "get_issue":
        return tool_result(client.get_issue(arguments.get("issue_key", "")))
    raise ValueError(f"Unknown tool: {name}")


def response(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        requested_version = message.get("params", {}).get("protocolVersion")
        version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return response(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "farpost-jira", "version": "1.0.0"},
            },
        )
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params", {})
        try:
            return response(request_id, call_tool(params.get("name", ""), params.get("arguments", {})))
        except Exception as error:
            return response(
                request_id,
                {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                },
            )
    if request_id is not None:
        return response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})
    return None


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            result = handle(message)
        except Exception as error:
            result = response(None, error={"code": -32700, "message": str(error)})
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
