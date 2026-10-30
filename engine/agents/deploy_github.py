"""Small GitHub repository client with bounded, retryable HTTP calls."""

from __future__ import annotations

import os
import time
from typing import Callable, Protocol

import httpx

from .deploy import redact
from .deploy_models import RepoInfo


class NotConfigured(Exception):
    def __init__(self, message: str = "GITHUB_TOKEN is not set; GitHub token is not configured") -> None:
        super().__init__(redact(message))


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(redact(message))


class GitHubClient(Protocol):
    def get_repo(self, name: str) -> RepoInfo | None: ...

    def create_repo(self, name: str, private: bool, description: str) -> RepoInfo: ...


class HttpxGitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 15.0,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise NotConfigured()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentforge-deploy",
        }
        for attempt in range(3):
            try:
                with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                    response = client.request(method, self.BASE_URL + path, headers=headers, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == 2:
                    raise GitHubError(f"GitHub request failed after 3 attempts: {redact(exc)}") from None
                self._sleep(0.5 * 2**attempt)
                continue
            if response.status_code >= 500:
                if attempt == 2:
                    raise GitHubError("GitHub server error after 3 attempts", response.status_code)
                self._sleep(0.5 * 2**attempt)
                continue
            if response.status_code in (401, 403):
                raise GitHubError(
                    "GitHub token is missing, invalid or lacks permission; a fine-grained token needs Administration and Contents write",
                    response.status_code,
                )
            return response
        raise GitHubError("GitHub request failed after 3 attempts")

    @staticmethod
    def _repo_info(response: httpx.Response) -> RepoInfo:
        try:
            data = response.json()
            return RepoInfo(
                html_url=redact(data["html_url"]),
                clone_url=redact(data["clone_url"]),
                default_branch=redact(data.get("default_branch") or "main"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubError(f"GitHub returned invalid repository data: {redact(exc)}", response.status_code) from None

    def get_repo(self, name: str) -> RepoInfo | None:
        identity = self._request("GET", "/user")
        if identity.status_code != 200:
            raise GitHubError("GitHub user lookup failed", identity.status_code)
        try:
            login = identity.json()["login"]
        except (ValueError, KeyError, TypeError):
            raise GitHubError("GitHub returned invalid user data", identity.status_code) from None
        response = self._request("GET", f"/repos/{login}/{name}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubError("GitHub repository lookup failed", response.status_code)
        return self._repo_info(response)

    def create_repo(self, name: str, private: bool, description: str) -> RepoInfo:
        response = self._request(
            "POST", "/user/repos", {"name": name, "private": private, "description": description, "auto_init": False}
        )
        if response.status_code == 201:
            return self._repo_info(response)
        if response.status_code == 422 and "already exists" in response.text.casefold():
            existing = self.get_repo(name)
            if existing is not None:
                return existing
            raise GitHubError("GitHub reports repository exists, but it could not be found", 422)
        raise GitHubError("GitHub repository creation failed", response.status_code)


class FakeGitHubClient:
    def __init__(self, repos: dict[str, RepoInfo] | None = None, failures: list[Exception] | None = None) -> None:
        self.repos = dict(repos or {})
        self.failures = list(failures or [])
        self.create_calls = 0

    def _fail_if_scripted(self) -> None:
        if self.failures:
            failure = self.failures.pop(0)
            raise GitHubError(str(failure)) from None

    def get_repo(self, name: str) -> RepoInfo | None:
        self._fail_if_scripted()
        return self.repos.get(name)

    def create_repo(self, name: str, private: bool, description: str) -> RepoInfo:
        self._fail_if_scripted()
        self.create_calls += 1
        if name not in self.repos:
            self.repos[name] = RepoInfo(
                html_url=f"https://github.com/fake/{name}",
                clone_url=f"https://github.com/fake/{name}.git",
                default_branch="main",
            )
        return self.repos[name]
