from __future__ import annotations

from session_runtime.language_profile import build_language_profile_payload


def test_language_profile_filters_meta_prompts_and_assistant_echoes() -> None:
    payload = build_language_profile_payload(
        [
            {
                "session": {
                    "session_id": "ses_demo_001",
                    "status": "ended",
                },
                "current_task": {
                    "name": "场景识别",
                },
                "turns": [
                    {
                        "child_input_text": "开始玩消防站。",
                        "assistant_reply": {"reply_text": "好的，我们开始吧。"},
                    },
                    {
                        "child_input_text": "请你以时间过得真快为开头，用富有感情的话结束这场对话。",
                        "assistant_reply": {"reply_text": "时间过得真快呀，我们下次再见。"},
                    },
                    {
                        "child_input_text": "墙上的画不会动。",
                        "assistant_reply": {"reply_text": "对，墙上的画不会动。"},
                    },
                    {
                        "child_input_text": "你的火源都在楼道里面了，为什么还要车和直升飞机呢？你自己拿水管冲不就完了吗？",
                        "assistant_reply": {"reply_text": "我们先继续玩消防站。"},
                    },
                    {
                        "child_input_text": "对，墙上的画不会动。",
                        "assistant_reply": {"reply_text": "那消防车能动吗？"},
                    },
                    {
                        "child_input_text": "消防车能动。",
                        "assistant_reply": {"reply_text": "太棒了。"},
                    },
                ],
            }
        ]
    )

    assert payload["utterance_count"] == 3
    assert payload["recent_utterances"] == ["墙上的画不会动。", "对，墙上的画不会动。", "消防车能动。"]
    top_tokens = {item["token"] for item in payload["top_tokens"]}
    assert "结束这场对话" not in top_tokens


def test_language_profile_counts_filtered_child_utterances_only() -> None:
    payload = build_language_profile_payload(
        [
            {
                "session": {
                    "session_id": "ses_demo_002",
                    "status": "active",
                },
                "current_task": {
                    "name": "火源判断",
                },
                "turns": [
                    {
                        "child_input_text": "大火。",
                        "assistant_reply": {"reply_text": "大火还是小火呢？"},
                    },
                    {
                        "child_input_text": "不知道。",
                        "assistant_reply": {"reply_text": "没关系，我们再看看。"},
                    },
                    {
                        "child_input_text": "音量大一点。",
                        "assistant_reply": {"reply_text": "好的，音量调大一点。"},
                    },
                ],
            }
        ]
    )

    assert payload["utterance_count"] == 2
    assert payload["recent_utterances"] == ["大火。", "不知道。"]
