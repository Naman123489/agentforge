"""Load an OpenAPI 3.1 contract (FR-3) and normalise it into endpoint descriptions.

The Backend agent puts the normalised form in its prompt and the contract test
uses it to call every endpoint of the generated app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

METHODS = ("get", "post", "put", "patch", "delete")


class FieldSpec(BaseModel):
    name: str
    type: str = "string"
    required: bool = True
    enum: list[Any] | None = None
    example: Any = None
    format: str | None = None
    min_length: int | None = None


class Endpoint(BaseModel):
    method: str
    path: str
    summary: str = ""
    auth: bool = False
    path_params: list[FieldSpec] = Field(default_factory=list)
    query: list[FieldSpec] = Field(default_factory=list)
    request: list[FieldSpec] = Field(default_factory=list)
    has_body: bool = False  # the operation declares a requestBody
    success_status: int = 200
    response_is_list: bool = False
    response: list[FieldSpec] = Field(default_factory=list)


class Contract(BaseModel):
    title: str = ""
    endpoints: list[Endpoint]
    raw: dict = Field(default_factory=dict, exclude=True)

    def prompt_text(self) -> str:
        """Compact, unambiguous description for the model (the raw spec is also included)."""
        lines = []
        for e in self.endpoints:
            def f(fields):
                return ", ".join(f"{x.name}:{x.type}{'' if x.required else '?'}"
                                 + (f" in {x.enum}" if x.enum else "") for x in fields) or "-"
            lines.append(
                f"{e.method} {e.path}{'  [auth: Bearer token]' if e.auth else ''}\n"
                f"   summary: {e.summary or '-'}\n   path params: {f(e.path_params)}   query: {f(e.query)}\n"
                f"   JSON body: {f(e.request)}\n   success: {e.success_status} -> "
                f"{'list of ' if e.response_is_list else ''}{{{f(e.response)}}}")
        return "\n".join(lines)


class ContractError(ValueError):
    pass


def load_contract(src: str | Path | dict) -> Contract:
    if isinstance(src, dict):
        spec = src
    else:
        text = Path(src).read_text(encoding="utf-8")
        if str(src).endswith((".yaml", ".yml")):
            import yaml

            spec = yaml.safe_load(text)
        else:
            spec = json.loads(text)
    if not str(spec.get("openapi", "")).startswith("3."):
        raise ContractError("contract must be an OpenAPI 3.x document (missing 'openapi: 3.1.0')")
    resolver = _Resolver(spec)
    global_auth = bool(spec.get("security"))
    endpoints = []
    for path, item in (spec.get("paths") or {}).items():
        shared = item.get("parameters", [])
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            params = [resolver.resolve(p) for p in shared + op.get("parameters", [])]
            e = Endpoint(method=method.upper(), path=path, summary=op.get("summary") or op.get("operationId") or "",
                         auth=bool(op["security"]) if "security" in op else global_auth,
                         path_params=[_param(p, resolver) for p in params if p.get("in") == "path"],
                         query=[_param(p, resolver) for p in params if p.get("in") == "query"])
            body = (op.get("requestBody") or {})
            e.has_body = bool(body)
            body = resolver.resolve(body).get("content", {}).get("application/json", {}).get("schema")
            if body:
                e.request = _fields(resolver.resolve(body), resolver)
            for status, resp in sorted((op.get("responses") or {}).items()):
                if str(status).startswith("2"):
                    e.success_status = int(status)
                    schema = resolver.resolve(resp).get("content", {}).get("application/json", {}).get("schema")
                    if schema:
                        schema = resolver.resolve(schema)
                        if schema.get("type") == "array":
                            e.response_is_list = True
                            schema = resolver.resolve(schema.get("items", {}))
                        e.response = _fields(schema, resolver)
                    break
            endpoints.append(e)
    contract = Contract(title=(spec.get("info") or {}).get("title", ""), endpoints=endpoints)
    contract.raw = spec
    return contract


def contract_problems(c: Contract) -> list[str]:
    errs = [] if c.endpoints else ["contract has no endpoints"]
    for e in c.endpoints:
        tag = f"{e.method} {e.path}"
        # An action with no input (POST /api/sessions to start a game) may omit the body; a body that is declared
        # must be a JSON object with fields (run_68259ab31790 was rejected 3x for a valid body-less POST).
        if e.method in ("POST", "PUT", "PATCH") and e.has_body and not e.request:
            errs.append(f"{tag}: requestBody must be an application/json object schema with properties")
        for p in e.path_params:
            if "{" + p.name + "}" not in e.path:
                errs.append(f"{tag}: path parameter '{p.name}' not in path")
    return errs


class _Resolver:
    def __init__(self, spec: dict):
        self.spec = spec

    def resolve(self, node: Any, depth: int = 0) -> Any:
        while isinstance(node, dict) and "$ref" in node and depth < 20:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise ContractError(f"only local $ref supported, got {ref}")
            cur: Any = self.spec
            for part in ref[2:].split("/"):
                cur = cur[part.replace("~1", "/").replace("~0", "~")]
            node, depth = cur, depth + 1
        return node


def _type(schema: dict) -> str:
    t = schema.get("type", "string")
    if isinstance(t, list):  # OpenAPI 3.1: ["string", "null"]
        t = next((x for x in t if x != "null"), "string")
    return t


def _fields(schema: dict, r: _Resolver) -> list[FieldSpec]:
    if "allOf" in schema:
        merged: dict = {"properties": {}, "required": []}
        for part in schema["allOf"]:
            part = r.resolve(part)
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
        schema = merged
    req = set(schema.get("required", []))
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = r.resolve(prop)
        out.append(FieldSpec(name=name, type=_type(prop), required=name in req, enum=prop.get("enum"),
                             example=prop.get("example", (prop.get("examples") or [None])[0]),
                             format=prop.get("format"), min_length=prop.get("minLength")))
    return out


def _param(p: dict, r: _Resolver) -> FieldSpec:
    s = r.resolve(p.get("schema", {}))
    return FieldSpec(name=p["name"], type=_type(s), required=bool(p.get("required", p.get("in") == "path")),
                     enum=s.get("enum"), example=p.get("example", s.get("example")), format=s.get("format"),
                     min_length=s.get("minLength"))
