from __future__ import annotations

import responses

from src.email import send_digest


CFG_ENABLED = {
    "enabled": True,
    "from": "ai-digest@example.com",
    "to": ["you@example.com"],
    "subject_template": "AI Digest — Semana {week_label}",
}


class TestSendDigest:
    @responses.activate
    def test_disabled_returns_false_without_request(self):
        cfg = {**CFG_ENABLED, "enabled": False}
        assert send_digest("# md", cfg, "2026-W21", api_key="key") is False
        assert len(responses.calls) == 0

    @responses.activate
    def test_missing_api_key_returns_false(self):
        assert send_digest("# md", CFG_ENABLED, "2026-W21", api_key=None) is False
        assert len(responses.calls) == 0

    @responses.activate
    def test_successful_send_returns_true(self):
        responses.add(
            responses.POST,
            "https://api.resend.com/emails",
            json={"id": "abc"},
            status=200,
        )
        assert send_digest("# md\nhello", CFG_ENABLED, "2026-W21", api_key="rs_test") is True
        call = responses.calls[0]
        assert call.request.headers["Authorization"] == "Bearer rs_test"
        # Subject template rendered
        import json as _json

        body = _json.loads(call.request.body)
        assert body["subject"] == "AI Digest — Semana 2026-W21"
        assert body["to"] == ["you@example.com"]
        assert "html" in body
        assert "<h1>" in body["html"]  # markdown rendered

    @responses.activate
    def test_http_error_returns_false_without_raising(self):
        responses.add(
            responses.POST,
            "https://api.resend.com/emails",
            status=500,
        )
        assert send_digest("# md", CFG_ENABLED, "2026-W21", api_key="rs_test") is False
