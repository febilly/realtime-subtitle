import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vr_overlay import VROverlay, blank_block


class TestVROverlay:
    @pytest.mark.asyncio
    async def test_push_builds_snapshot_with_translation(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.0)
        await overlay.push("Hello world", "en", "こんにちは")

        assert len(received) == 1
        payload = received[0]
        assert payload["type"] == "snapshot"
        assert payload["payload"]["revision"] == 1
        assert payload["payload"]["calibration"]["anchor"] == "head_locked"
        block = payload["payload"]["blocks"][0]
        assert block["primary_text"] == "こんにちは"
        assert block["secondary_text"] == "Hello world"
        assert block["secondary_enabled"] is True
        assert block["secondary_language"] == "en"

    @pytest.mark.asyncio
    async def test_push_without_translation_falls_back_to_source(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.0)
        await overlay.push("Hello world", "en", "")

        block = received[0]["payload"]["blocks"][0]
        assert block["primary_text"] == "Hello world"
        assert block["secondary_text"] == ""
        assert block["secondary_enabled"] is False

    @pytest.mark.asyncio
    async def test_translated_caption_is_not_replaced_by_next_source_draft(self):
        """A next-sentence source draft must not flash over a visible translation."""
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.0)
        await overlay.push("Good evening", "en", "晚上好", finalized=False)
        await overlay.push("How are you", "en", None, finalized=False)

        assert len(received) == 1
        block = overlay.latest_snapshot()["payload"]["blocks"][0]
        assert block["primary_text"] == "晚上好"
        assert block["secondary_text"] == "Good evening"

        await overlay.push("How are you", "en", "你好吗", finalized=False)

        assert len(received) == 2
        block = overlay.latest_snapshot()["payload"]["blocks"][0]
        assert block["primary_text"] == "你好吗"
        assert block["secondary_text"] == "How are you"

    @pytest.mark.asyncio
    async def test_push_empty_is_noop(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.0)
        await overlay.push("", None, "")
        assert received == []

    @pytest.mark.asyncio
    async def test_blank_block_after_clear_after(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.05)
        await overlay.push("Hello", "en", "Hi")
        await asyncio.sleep(0.15)

        assert len(received) == 2
        assert received[1]["payload"]["blocks"] == []
        assert received[1]["payload"]["revision"] == 2

    @pytest.mark.asyncio
    async def test_new_push_cancels_pending_clear(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.05)
        await overlay.push("A", "en", "a")
        await asyncio.sleep(0.03)  # A 的 clear 即将触发 (0.05)
        await overlay.push("B", "en", "b")  # 取消 A 的 pending clear
        await asyncio.sleep(0.03)  # 已过 A 的触发点 (0.05), 未到 B 的 (0.08)

        assert len(received) == 2  # A 的 clear 被取消 → 无中间 blank
        assert received[1]["payload"]["blocks"][0]["primary_text"] == "b"

    @pytest.mark.asyncio
    async def test_live_updates_are_cleared_by_the_hard_timeout(self):
        """A never-finalized recognition stream must not cover the view forever."""
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=1.0, live_clear_after=0.05)
        await overlay.push("Hello", "en", None, finalized=False)
        await asyncio.sleep(0.03)
        await overlay.push("Hello world", "en", None, finalized=False)
        await asyncio.sleep(0.04)

        assert received[-1]["payload"]["blocks"] == []

    @pytest.mark.asyncio
    async def test_latest_snapshot_supports_resync(self):
        received = []

        async def capture(payload):
            received.append(payload)

        overlay = VROverlay(capture, clear_after=0.0)
        await overlay.push("Hello", "en", "こんにちは")

        latest = overlay.latest_snapshot()
        assert latest["payload"]["blocks"][0]["primary_text"] == "こんにちは"

    @pytest.mark.asyncio
    async def test_blank_block_helper(self):
        block = blank_block(5)
        assert block["id"] == "rt:clear:5"
        assert block["primary_text"] == ""
        assert block["secondary_text"] == ""
