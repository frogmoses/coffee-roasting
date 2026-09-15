"""Claude calls for the analyzer, in one place and fail-soft.

Two shapes are used across the project:

- `structured_call`: one request, JSON output enforced by a schema
  (`output_config.format`), adaptive thinking. The recommender (after a
  roast) and the profile designer (before one) both go through here.
- `research_call`: one request with the web search server tool, plain text
  back plus the URLs the search touched. Used by the bean dossier's
  optional `--research`. Kept separate because citations from the search
  tool and the JSON output format do not combine.

Every function returns `(result, status)` and never raises into a scan or a
roast-day command: a missing SDK, missing credentials, a network error, a
refusal, or truncated output all come back as `(None, "<reason>")`.

Security: the SDK reads ANTHROPIC_API_KEY from the environment, injected by
the run_roast-analyzer wrapper. No .env loading, no key in code.
"""

import json

# Opus 4.8: best reasoning at the time the recommender was built; a few calls
# a week, cached afterwards. One constant so every caller moves together.
MODEL = "claude-opus-4-8"

# Web search server tool variant for Opus 4.6+ (dynamic filtering built in).
WEB_SEARCH_TOOL = "web_search_20260209"


def _import_sdk():
    try:
        import anthropic
    except ImportError:
        return None, "anthropic SDK not installed (uv add anthropic)"
    return anthropic, None


def _call(anthropic, **kwargs):
    """messages.create with the project's error mapping."""
    try:
        client = anthropic.Anthropic()
        return client.messages.create(**kwargs), None
    except anthropic.AuthenticationError:
        return None, "no API credentials (set ANTHROPIC_API_KEY via the wrapper)"
    except anthropic.APIError as e:
        return None, f"LLM API error: {e}"
    except Exception as e:  # network, unexpected SDK errors: fail soft
        return None, f"LLM call failed: {e}"


def _text_of(response):
    """Concatenated text blocks of a response (thinking blocks skipped)."""
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def structured_call(system, user_content, schema, max_tokens=16000, effort="high",
                    model=MODEL, task="this request"):
    """One JSON-schema-constrained call. Returns (dict, "ok") or (None, why)."""
    anthropic, err = _import_sdk()
    if err:
        return None, err
    response, err = _call(
        anthropic,
        model=model,
        # Adaptive thinking counts against max_tokens; 16000 leaves room for
        # a long JSON answer at effort "high".
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    if err:
        return None, err
    if response.stop_reason == "refusal":
        return None, f"model declined {task}"
    # Truncated output is broken JSON; report the real cause.
    if response.stop_reason == "max_tokens":
        return None, "output truncated at max_tokens - raise max_tokens in llm_client.py"
    text = _text_of(response)
    if not text:
        return None, "empty response from model"
    try:
        return json.loads(text), "ok"
    except ValueError:
        return None, "could not parse model output as JSON"


def research_call(system, user_content, max_uses=3, max_tokens=16000, model=MODEL,
                  max_rounds=3):
    """One call with the web search tool. Returns ({"text", "urls"}, "ok") or (None, why).

    A `pause_turn` (the model paused mid-search) is continued by sending the
    assistant content back, up to `max_rounds` times.
    """
    anthropic, err = _import_sdk()
    if err:
        return None, err
    messages = [{"role": "user", "content": user_content}]
    tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}]
    urls, texts = [], []
    for _ in range(max_rounds):
        response, err = _call(
            anthropic, model=model, max_tokens=max_tokens,
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            system=system, tools=tools, messages=messages,
        )
        if err:
            return None, err
        if response.stop_reason == "refusal":
            return None, "model declined the research request"
        for block in response.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                texts.append(block.text)
            elif btype == "web_search_tool_result":
                content = getattr(block, "content", None)
                # A success is a list of results; an error is a single object
                if isinstance(content, list):
                    for r in content:
                        url = getattr(r, "url", None)
                        if url and url not in urls:
                            urls.append(url)
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        return None, "empty research response"
    return {"text": text, "urls": urls}, "ok"
