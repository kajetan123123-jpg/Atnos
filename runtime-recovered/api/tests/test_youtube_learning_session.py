from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from atnos.learning.youtube_session import (
    ATNOS_LEARNING_INSTRUCTION,
    LocalOllama,
    StorageSafetyError,
    _allowed_thumbnail_url,
    _ensure_allowed_path,
    _epistemic_value,
    _ollama_message_text,
    _parse_public_shorts_page,
    _parse_youtube_rss,
    _public_channel_page,
    _seconds_to_iso_duration,
    _valid_epistemic_record,
)


def test_learning_instruction_keeps_polish_text_and_hard_rules() -> None:
    assert ATNOS_LEARNING_INSTRUCTION.startswith("Jesteś Atnosem. Działasz w LEARNING_MODE.")
    assert "Nie pobieraj pełnych cudzych filmów." in ATNOS_LEARNING_INSTRUCTION
    assert "Nie wykonujesz fine-tuningu." in ATNOS_LEARNING_INSTRUCTION
    assert "\ufffd" not in ATNOS_LEARNING_INSTRUCTION


def test_local_ollama_rejects_external_endpoint() -> None:
    with pytest.raises(Exception, match="loopback"):
        LocalOllama("https://example.com")


def test_learning_outputs_cannot_be_written_to_system_disk() -> None:
    with pytest.raises(StorageSafetyError, match="outside the approved E: data drive"):
        _ensure_allowed_path(Path("C:/AtnosLearningRuns/result.json"))


def test_thumbnail_allowlist_accepts_only_official_https_hosts() -> None:
    assert _allowed_thumbnail_url("https://i.ytimg.com/vi/test/maxresdefault.jpg") is True
    assert _allowed_thumbnail_url("http://i.ytimg.com/vi/test/maxresdefault.jpg") is False
    assert _allowed_thumbnail_url("https://ytimg.com.evil.example/test.jpg") is False
    assert _allowed_thumbnail_url("https://example.com/test.jpg") is False


def test_epistemic_schema_rejects_unlabelled_conclusions() -> None:
    valid = {
        "topic": {"value": "x", "label_type": "observation"},
        "hook_type": {"value": "x", "label_type": "hypothesis"},
        "structure_summary": {"value": "x", "label_type": "lack_of_data"},
        "tempo_estimate": {"value": "x", "label_type": "lack_of_data"},
        "audio_profile": {"value": "x", "label_type": "lack_of_data"},
        "thumbnail_profile": {"value": "x", "label_type": "observation"},
        "potential_success_factors": [{"value": "x", "label_type": "observation"}],
        "potential_issues": [{"value": "x", "label_type": "hypothesis"}],
    }
    assert _valid_epistemic_record(valid) is True

    valid["topic"] = {"value": "x"}
    assert _valid_epistemic_record(valid) is False


def test_official_rss_parser_returns_public_metadata_only() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/">
      <title>Reference channel</title>
      <entry>
        <yt:videoId>video123</yt:videoId>
        <yt:channelId>UC12345678901234567890</yt:channelId>
        <title>Public title</title>
        <published>2026-07-01T10:00:00+00:00</published>
        <media:group>
          <media:description>Public description</media:description>
          <media:thumbnail url="https://i.ytimg.com/vi/video123/hqdefault.jpg" />
          <media:community><media:statistics views="1234" /></media:community>
        </media:group>
      </entry>
    </feed>"""
    items, title = _parse_youtube_rss(xml)

    assert title == "Reference channel"
    assert items[0]["id"] == "video123"
    assert items[0]["statistics"]["viewCount"] == "1234"
    assert "access_token" not in str(items)


def test_public_duration_conversion_is_iso_8601() -> None:
    assert _seconds_to_iso_duration("34") == "PT34S"
    assert _seconds_to_iso_duration("125") == "PT2M5S"
    assert _seconds_to_iso_duration(None) is None


def test_public_channel_page_uses_shorts_surface_and_utf8() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        body = '<title>Zażółć - YouTube</title><script>{"externalId":"UC12345678901234567890"}</script>'
        return httpx.Response(200, content=body.encode("utf-8"), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text, channel_id = _public_channel_page(client, "https://www.youtube.com/@example")

    assert requested_urls == ["https://www.youtube.com/@example/shorts?ucbcb=1"]
    assert channel_id == "UC12345678901234567890"
    assert "Zażółć" in text


def test_public_shorts_parser_preserves_unicode_and_deduplicates() -> None:
    card = (
        '"shortsLockupViewModel":{"accessibilityText":"Pieniądze i rozwój, 123 views",'
        '"onTap":{"innertubeCommand":{"reelWatchEndpoint":{"videoId":"AbCdEfGhI12"}}},'
        '"thumbnail":{"sources":[{"url":"https://i.ytimg.com/vi/AbCdEfGhI12/hqdefault.jpg"}]}}'
    )
    text = f"<title>Życie &amp; Finanse - YouTube</title>{card}{card}"

    items, title = _parse_public_shorts_page(
        text,
        "UC12345678901234567890",
        "https://www.youtube.com/@example",
    )

    assert title == "Życie & Finanse"
    assert len(items) == 1
    assert items[0]["snippet"]["title"] == "Pieniądze i rozwój"


def test_ollama_message_prefers_content_and_supports_local_qwen_thinking() -> None:
    assert _ollama_message_text({"message": {"content": '{"status":"content"}', "thinking": "ignored"}}) == '{"status":"content"}'
    assert _ollama_message_text({"message": {"content": "", "thinking": '{"status":"thinking"}'}}) == '{"status":"thinking"}'


def test_epistemic_value_flattens_model_list_without_python_repr() -> None:
    value = [
        {"value": "Nostalgia", "label_type": "observation"},
        {"value": "Temporal contrast", "label_type": "hypothesis"},
    ]

    normalized = _epistemic_value(value, default_label="observation")

    assert normalized == {"value": "Nostalgia; Temporal contrast", "label_type": "observation"}
    assert "{'value':" not in normalized["value"]
