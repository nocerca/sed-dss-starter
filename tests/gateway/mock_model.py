"""Synthetic model endpoint for a real OpenClaw test on an internal Docker network."""

import json
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    successful_calls = 0
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/state":
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps({"calls": Handler.successful_calls}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        wire = json.dumps(body, ensure_ascii=False)
        assistant = 'untrusted_finding' in wire
        valid = (
            self.path == "/api/paas/v4/chat/completions"
            and self.headers.get("Authorization") == "Bearer synthetic-zai-api-key"
            and body.get("model") == "glm-4.7-flash"
            and body.get("thinking") == {"type": "disabled"}
            and not body.get("tools")
            and (assistant or ('untrusted_letter' in wire and 'untrusted_structure' in wire))
            and not ("SECOND_SYNTHETIC_MARKER" in json.dumps(body) and "FIRST_SYNTHETIC_MARKER" in json.dumps(body))
            and "PRIVATE-SED-ID" not in json.dumps(body)
        )
        if not valid:
            self.send_response(400)
            self.end_headers()
            return
        Handler.successful_calls += 1
        quote = "учебных материалов" if "учебных материалов" in json.dumps(body, ensure_ascii=False) else "учебные материалы"
        findings = {"findings": [{
            "code": "SEMANTIC_DEMO", "category": "content", "severity": "info",
            "message": "Уточните учебный запрос", "evidence": quote,
            "recommendation": "Уточните перечень", "source_ref": None,
        }]}
        if assistant:
            findings = {'status': 'completed', 'answer': 'Синтетический ответ: уточните срок по согласованным данным.',
                        'steps': [], 'suggestion': None, 'source_refs': []}
        completion = {
            "id": "synthetic-response", "object": "chat.completion", "created": 0,
            "model": "glm-4.7-flash", "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(findings, ensure_ascii=False)},
            }], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        if body.get("stream"):
            event = {
                "id": "synthetic-response", "object": "chat.completion.chunk", "created": 0,
                "model": "glm-4.7-flash", "choices": [{
                    "index": 0, "delta": {"role": "assistant", "content": json.dumps(findings, ensure_ascii=False)},
                    "finish_reason": None,
                }],
            }
            end = {**event, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            response = ("data: " + json.dumps(event, ensure_ascii=False) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
        else:
            response = json.dumps(completion, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
HTTPServer(("0.0.0.0", 8002), Handler).serve_forever()
