"""The mem0 benchmark arm, offline: a fake extraction LLM, real Mem0 + Qdrant + fastembed."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mem0")
pytest.importorskip("fastembed")
sys.path.insert(0, str(Path(__file__).parent.parent / "evals" / "memory"))
import mem0_arm  # noqa: E402


def test_first_json_takes_the_object_out_of_prose_and_fences():
    assert json.loads(mem0_arm.first_json('ok:\n```json\n{"memory": [{"text": "a"}]}\n```'))["memory"][0]["text"] == "a"
    assert json.loads(mem0_arm.first_json('x {"memory": [{"text": "b {c}"}]} y'))["memory"][0]["text"] == "b {c}"
    assert mem0_arm.first_json("nothing") == '{"memory": []}'


def test_store_saves_each_session_once_and_search_shows_its_date(tmp_path):
    prompts = []

    def fake_complete(prompt, model):
        prompts.append(prompt)
        fact = "John went to yoga with his colleague Rob" if "yoga" in prompt else "Maria adopted a dog named Coco"
        return f'Here you go:\n```json\n{{"memory": [{{"text": "{fact}"}}]}}\n```', {}

    sessions = [
        ("1:00 pm on 3 July, 2023", [{"role": "user", "name": "John", "content": "Rob took me to yoga yesterday"}]),
        ("2:00 pm on 9 July, 2023", [{"role": "assistant", "name": "Maria", "content": "I adopted Coco!"}]),
    ]
    stores = mem0_arm.Mem0Stores(tmp_path, fake_complete, "fake-model")
    hits = stores.search("conv-1", sessions, "Who did John go to yoga with?", keep=12)

    assert len(prompts) == 2  # one extraction call per session
    assert "John: Rob took me to yoga" in prompts[0]
    # the session's date is Mem0's "current date", so "yesterday" resolves to 2 July 2023, not a date today
    assert "## Current Date\n1:00 pm on 3 July, 2023" in prompts[0]
    assert "## Observation Date\n2:00 pm on 9 July, 2023" in prompts[1]
    assert "Rob" in hits[0]["memory"]
    body = mem0_arm.context_body(hits)
    assert "DATE: 1:00 pm on 3 July, 2023\nJohn went to yoga with his colleague Rob" in body

    stores.search("conv-1", sessions, "What is Maria's dog called?", keep=12)
    assert len(prompts) == 2  # the conversation is built once, then reused
    assert (tmp_path / "conv-1" / "sessions_done").read_text() == "2"
